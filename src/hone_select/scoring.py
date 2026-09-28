"""Run gates and cascade stages over candidates; convert whatever user code returns or raises."""

from __future__ import annotations

from hone_select._tracing import span
from hone_select.aggregate import total
from hone_select.prompt import image_keys
from hone_select.registry import Component
from hone_select.run import Run
from hone_select.types import Candidate, GateResult, Score, Scored, to_gate_result, to_score


def span_context(candidate: Candidate, component: Component) -> dict[str, str]:
    """Trace-context keys for a call about one candidate (design/current.md §7.1)."""
    return {"hone.candidate_id": candidate.id, "hone.scorer": component.name}


def image_attributes(component: Component) -> dict[str, list[str]]:
    """``{"hone.select.image_keys": [...]}`` for a prompt judge that sends images, else ``{}``."""
    keys = image_keys(getattr(component.fn, "images_from", None))
    return {"hone.select.image_keys": keys} if keys else {}


def run_gate(run: Run, gate: Component, candidate: Candidate) -> GateResult:
    """Call one gate. An exception rejects the candidate, visibly (decision trace + reason)."""
    with span(
        run.sink, "hone.select.gate", {"hone.select.gate": gate.name}, context=span_context(candidate, gate)
    ) as s:
        run.budget.charge(gate.cost)
        try:
            result = to_gate_result(gate(candidate))
        except Exception as e:
            error = run.mark_error(s, e)
            result = GateResult(False, reason=f"error: {error}")
            run.note("gate_error", candidate=candidate.id, gate=gate.name, error=error)
        s["attributes"]["hone.select.gate.passed"] = result.passed
        s["attributes"]["hone.select.gate.probability"] = result.probability
        if result.details:
            s["attributes"]["hone.select.gate.details"] = run.content(dict(result.details))
    return result


def run_scorer(run: Run, scorer: Component, candidate: Candidate) -> Score:
    """Call one scorer (or reuse a cached score). An exception becomes ``Score(None, error=...)``."""
    key = (candidate.id, scorer.name, scorer.version, scorer.judge_model)
    cached = run.cache.get(key) if run.cache is not None else None
    attributes = {
        "hone.select.scorer": scorer.name,
        "hone.select.scorer_version": scorer.version,
        **image_attributes(scorer),
    }
    with span(run.sink, "hone.select.score", attributes, context=span_context(candidate, scorer)) as s:
        if cached is not None:
            score = cached
            run.note("cache_hit", candidate=candidate.id, scorer=scorer.name)
        else:
            run.budget.charge(scorer.cost)
            try:
                score = to_score(scorer(candidate))
            except Exception as e:
                score = Score(None, error=run.mark_error(s, e))
            if run.cache is not None and score.value is not None and not score.error:
                run.cache.put(key, score)
        if score.error:
            run.note("score_error", candidate=candidate.id, scorer=scorer.name, error=score.error)
        s["attributes"].update(
            {
                "hone.select.cache_hit": cached is not None,
                "hone.select.score.value": score.value,
                "hone.select.score.confidence": score.confidence,
                "hone.select.score.reason": run.content(score.reason),
                "hone.select.score.error": run.content(score.error) if score.error else "",
            }
        )
    return score


def run_gates(run: Run, scored: list[Scored]) -> None:
    """Run the configured gates cheapest first; the first failure rejects the candidate."""
    gates = sorted((run.components[name] for name in run.config.score.gates), key=lambda g: g.cost)
    if not gates:
        return
    rejected: dict[str, str] = {}
    for item in scored:
        for gate in gates:
            result = run_gate(run, gate, item.candidate)
            item.gates[gate.name] = result
            if not result.passed:
                item.rejected = True
                rejected[item.candidate.id] = f"{gate.name}: {result.reason}".rstrip(": ")
                break
    passed = [item.candidate.id for item in scored if not item.rejected]
    run.note("gated", passed=passed, rejected=rejected)


def reject_missing(run: Run, items: list[Scored]) -> None:
    """``missing="reject"``: a candidate with any ``None`` score is rejected, with the reason recorded."""
    if run.config.score.missing != "reject":
        return
    for item in items:
        failed = [name for name, score in item.scores.items() if score.value is None]
        if failed and not item.rejected:
            item.rejected = True
            run.note(
                "rejected", candidate=item.candidate.id, reason=f"no score from {failed} (missing=reject)"
            )


def rank_key(item: Scored) -> tuple[bool, bool, int, float]:
    """Rejected last, no total last, then deeper cascade stage, then higher total (design/decisions.md D-003).

    A candidate cut at stage 1 only has a partial total, so it never outranks a finalist.
    Sorting is stable, so the earlier candidate wins exact ties.
    """
    return (item.rejected, item.total is None, -item.stage_reached, -(item.total or 0.0))


def keep_top(items: list[Scored], k: int | None) -> list[Scored]:
    """The best ``k`` by partial total; candidates tied with the k-th are kept too."""
    if k is None or len(items) <= k:
        return items
    ranked = sorted(items, key=rank_key)
    cut = ranked[k - 1].total
    return [item for i, item in enumerate(ranked) if i < k or (cut is not None and item.total == cut)]


def run_cascade(run: Run, scored: list[Scored]) -> None:
    """Score stage by stage; each stage only sees the survivors of the previous one."""
    cfg = run.config.score
    survivors = [item for item in scored if not item.rejected]
    fallback_mode = not survivors and run.config.select.fallback != "none"
    if fallback_mode:
        survivors = list(scored)  # the fallback picks among rejected candidates, so they need scores
    for number, stage in enumerate(run.config.score.cascade, start=1):
        if number > 1 and (reason := run.budget.exhausted()):
            run.note("stop", reason=reason, stage=number)
            return
        for item in survivors:
            for name in stage.scorers:
                item.scores[name] = run_scorer(run, run.components[name], item.candidate)
            item.stage_reached = number
            item.total = total(item.scores, cfg.weights, cfg.aggregate, cfg.missing, cfg.floor)
        reject_missing(run, survivors)
        survivors = keep_top(
            [item for item in survivors if fallback_mode or not item.rejected], stage.keep_top
        )
        run.note(
            "scored", stage=number, scorers=stage.scorers, kept=[item.candidate.id for item in survivors]
        )
