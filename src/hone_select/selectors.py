"""Pick the winner from scored candidates: policies, tie escalation to pairwise, and fallbacks."""

from __future__ import annotations

from typing import Any, cast

from hone_select._tracing import span
from hone_select.run import Run
from hone_select.scoring import image_attributes, rank_key, span_context
from hone_select.types import Candidate, Scored

SWAP = {"a": "b", "b": "a", "tie": "tie"}
# (choice in order A-B, choice in order B-A mapped back): both orders chose the same position
POSITION = {("a", "b"): "first", ("b", "a"): "second"}


def select(run: Run, scored: list[Scored]) -> tuple[Scored | None, list[Scored]]:
    """``(winner, ranked)``; ``scored`` is in generation order. The winner is moved to the front."""
    ranked = sorted(scored, key=rank_key)
    valid = [item for item in ranked if not item.rejected]
    policy = run.config.select.policy
    if not valid:
        winner = fallback(run, ranked, scored)
    elif policy == "first_above":
        winner = first_above(run, [item for item in scored if not item.rejected], valid[0])
    elif policy == "pairwise_tournament":
        winner = tournament(run, valid)
    else:
        winner = escalate_if_tied(run, valid)
    warn_position_bias(run)
    if winner is not None:
        ranked.remove(winner)
        ranked.insert(0, winner)
        run.note("selected", winner=winner.candidate.id, policy=policy, total=winner.total)
    return winner, ranked


def reaches(item: Scored, threshold: float | None) -> bool:
    """Not rejected and total at or above ``threshold``."""
    return not item.rejected and item.total is not None and item.total >= (threshold or 0.0)


def first_above(run: Run, in_order: list[Scored], best: Scored) -> Scored:
    """The first candidate (generation order) whose total reaches the threshold; else the best one."""
    threshold = run.config.select.threshold
    for item in in_order:
        if reaches(item, threshold):
            return item
    run.note("threshold_not_met", threshold=threshold, best=best.candidate.id)
    return best


def tie_reason(run: Run, valid: list[Scored]) -> str | None:
    """Why the top of the ranking is not decisive (tie margin or low confidence), or ``None``."""
    if len(valid) < 2:
        return None
    cfg = run.config.select
    top, second = valid[0], valid[1]
    if top.total is not None and second.total is not None and top.total - second.total <= cfg.tie_margin:
        return f"top two within tie_margin {cfg.tie_margin:g} ({top.total:.4f} vs {second.total:.4f})"
    if cfg.min_confidence is not None:
        for item in (top, second):
            for name, score in item.scores.items():
                if score.confidence is not None and score.confidence < cfg.min_confidence:
                    return (
                        f"{name} confidence {score.confidence:g} below min_confidence {cfg.min_confidence:g}"
                    )
    return None


def escalate_if_tied(run: Run, valid: list[Scored]) -> Scored:
    """argmax: the top candidate, unless the top is tied or unsure and ``escalate="pairwise"``."""
    reason = tie_reason(run, valid)
    if reason is None:
        return valid[0]
    tied = [item.candidate.id for item in valid[:2]]
    if run.config.select.escalate == "none":
        run.note("tie", reason=reason, candidates=tied, kept=valid[0].candidate.id)
        return valid[0]
    top = valid[0].total
    margin = run.config.select.tie_margin
    group = [
        item for item in valid if top is not None and item.total is not None and top - item.total <= margin
    ]
    contenders = group if len(group) >= 2 else valid[:2]
    run.note("escalation", reason=reason, candidates=[item.candidate.id for item in contenders])
    return tournament(run, contenders)


def tournament(run: Run, contenders: list[Scored]) -> Scored:
    """King of the hill in ranking order: a challenger takes over only by winning in both orders."""
    champion = contenders[0]
    for challenger in contenders[1:]:
        if compare(run, champion.candidate, challenger.candidate) == "b":
            champion = challenger
    return champion


def compare(run: Run, a: Candidate, b: Candidate) -> str:
    """Ask the pairwise judge in both orders; a win counts only if both orders agree, else ``"tie"``.

    A judge that chose by position in both orders is named (design change 0003); after
    ``max_biased_pairwise`` such comparisons in a row it is not asked again in this run.
    """
    limit, in_a_row = run.config.select.max_biased_pairwise, biased_in_a_row(run.pairwise_bias)
    if limit is not None and in_a_row >= limit:
        run.note("escalation_skipped", a=a.id, b=b.id, reason="position_bias", biased_in_a_row=in_a_row)
        return "tie"
    first = call_pairwise(run, a, b)
    second = SWAP[call_pairwise(run, b, a)]
    outcome = first if first == second else "tie"
    bias = POSITION.get((first, second))
    run.pairwise_bias.append(bias)
    run.note(
        "pairwise",
        a=a.id,
        b=b.id,
        ab=first,
        ba=second,
        outcome=outcome,
        **({"reason": "position_bias"} if bias else {}),
    )
    return outcome


def biased_in_a_row(bias: list[str | None]) -> int:
    """How many of the latest comparisons, counting back, were decided by position."""
    count = 0
    for position in reversed(bias):
        if position is None:
            break
        count += 1
    return count


def warn_position_bias(run: Run) -> None:
    """One warning when the pairwise judge chose by position in every comparison it made."""
    bias = run.pairwise_bias
    if not bias or None in bias:
        return
    judge = run.components[run.config.select.pairwise or ""]
    position = f"{bias[0]}-shown" if len(set(bias)) == 1 else "same-position"
    model = f" ({judge.judge_model})" if judge.judge_model else ""
    run.warn(
        f"pairwise judge {judge.name!r}{model} picked the {position} candidate in {len(bias)}/{len(bias)} "
        "comparisons; its pairwise decisions are ties. Try a judge from another model family, or set "
        "[select] max_biased_pairwise to stop asking it"
    )


def call_pairwise(run: Run, a: Candidate, b: Candidate) -> str:
    """One pairwise call: ``"a"``, ``"b"`` or ``"tie"``. Errors and bad answers count as a tie."""
    judge = run.components[run.config.select.pairwise or ""]
    attributes = {"hone.select.scorer": judge.name, **image_attributes(judge)}
    with span(run.sink, "hone.select.pairwise", attributes, context=span_context(a, judge)) as s:
        run.budget.charge(judge.cost)
        try:
            choice = _choice(judge(a, b))
        except Exception as e:
            choice = "tie"
            error = run.mark_error(s, e)
            run.note("pairwise_error", a=a.id, b=b.id, error=error)
        s["attributes"].update(
            {
                "hone.select.pairwise.a": a.id,
                "hone.select.pairwise.b": b.id,
                "hone.select.pairwise.choice": choice,
            }
        )
    return choice


def _choice(answer: Any) -> str:
    """``"a"`` / ``"b"`` / ``"tie"``, or ``(choice, confidence)``."""
    choice = cast("tuple[Any, ...]", answer)[0] if isinstance(answer, tuple) else answer
    if choice not in SWAP:
        raise ValueError(f"pairwise judge must return 'a', 'b' or 'tie', got {answer!r}")
    return str(choice)


def fallback(run: Run, ranked: list[Scored], in_order: list[Scored]) -> Scored | None:
    """No candidate survived: pick a rejected one (flagged) or nobody, as configured."""
    mode = run.config.select.fallback
    pool = ranked if mode == "best_rejected" else in_order
    winner = None
    if mode != "none" and pool:
        winner = next((item for item in pool if item.total is not None), pool[0])
    run.note("fallback", mode=mode, winner=winner.candidate.id if winner else None)
    return winner
