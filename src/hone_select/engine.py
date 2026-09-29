"""The engine: generate -> dedup -> gates -> cascade -> aggregate -> select -> record."""

from __future__ import annotations

import hashlib
import os
import re
import time
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any, Literal, cast

from hone_select._records import default_sink
from hone_select._tracing import span
from hone_select.budget import Budget
from hone_select.cache import default_cache
from hone_select.config import SelectionConfig, check_config, load_config
from hone_select.dedup import dedup
from hone_select.errors import BudgetExceeded, ConfigError
from hone_select.explain import explain_rows, ranked_rows
from hone_select.ports import DecisionClient, Embedder, RecordSink, ScoreCache, TextClient
from hone_select.prompt import model_family
from hone_select.registry import KINDS, Component, bind_judges, config_scorers, load_registry
from hone_select.run import Run
from hone_select.scoring import run_cascade, run_gates
from hone_select.selectors import reaches, select
from hone_select.types import Candidate, Result, Scored, Variation, as_text, canonical_json
from hone_select.variation import variations

TASK_PREVIEW = 2000  # characters of the task kept on the run span (design change 0008)
# configuration keys whose values are never recorded, and prompt text recorded as content (design change 0008)
SECRET_KEY = re.compile(
    r"(^|_)(api_?key|key|access_?token|token|secret|password|passwd|authorization|credentials?)(_|$)",
    re.IGNORECASE,
)
CONTENT_KEYS = ("criteria", "anchors")


def recorded_config(value: Any, run: Run, key: str = "") -> Any:
    """The configuration as recorded: values of secret-named keys become ``***``; prompt text goes through
    ``run.content`` (hashed when content capture is off)."""
    if SECRET_KEY.search(key):
        return "***"
    if key in CONTENT_KEYS:
        return run.content(value)
    if isinstance(value, dict):
        return {str(k): recorded_config(v, run, str(k)) for k, v in cast(dict[Any, Any], value).items()}
    if isinstance(value, list):
        return [recorded_config(v, run) for v in cast(list[Any], value)]
    return value


class Engine:
    """Generate N candidates, score them with your scorers, and select a winner by policy.

    Example::

        engine = Engine("selection.toml", registry=[write, not_too_long, shorter_is_better])
        result = engine.run("hello")
        print(result.winner.candidate.data, engine.explain(result))
    """

    def __init__(
        self,
        config: SelectionConfig | str | Path,
        *,
        registry: Iterable[Any] = (),
        judges: Mapping[str, DecisionClient] | None = None,
        text_client: TextClient | None = None,
        embedder: Embedder | None = None,
        sink: RecordSink | None = None,
        cache: ScoreCache | Literal["default"] | None = "default",
    ) -> None:
        self.config = load_config(config)
        self.judges = dict(judges or {})  # name -> client; bind_judges adds the [judges.*] clients here
        items = bind_judges([*registry, *config_scorers(self.config)], self.judges, self.config)
        self.components = load_registry(items)
        names = {kind: {c.name for c in self.components.values() if c.kind == kind} for kind in KINDS}
        check_config(self.config, names)
        if self.config.dedup.method == "embedding" and embedder is None:
            raise ConfigError('dedup.method = "embedding" needs an embedder; pass Engine(..., embedder=...)')
        self.text_client = text_client  # v0.1: accepted for forward compatibility, not used yet
        self.embedder = embedder
        self.sink = sink if sink is not None else default_sink(self.config.record)
        self.cache = default_cache(self.config.record) if cache == "default" else cache

    def run(self, task: Any, *, trace: Mapping[str, str] | None = None, seed: int | None = None) -> Result:
        """Generate candidates for ``task``, score them and select the winner."""
        run = self._new_run()
        with span(self.sink, "hone.select.run", self._run_attributes(run, task), trace=trace) as root:
            self._warn_goodhart(run)
            stream = self._generate(run, task, seed or 0)
            if self.config.select.policy == "first_above":
                scored = self._first_above(run, stream)
            else:
                scored = self._pipeline(run, list(stream))
            return self._decide(run, scored, root)

    def select(self, candidates: list[Candidate], *, trace: Mapping[str, str] | None = None) -> Result:
        """Score and select among existing candidates (no generator): the ``run()`` twin, same ``Result``
        with the decision trace, ``run_id`` and budget (design change 0006)."""
        run = self._new_run()
        with span(self.sink, "hone.select.run", self._run_attributes(run), trace=trace) as root:
            return self._decide(run, self._pipeline(run, list(candidates)), root)

    def score(self, candidates: list[Candidate], *, trace: Mapping[str, str] | None = None) -> list[Scored]:
        """Score and select among existing candidates (no generator); returns them ranked, winner first.

        The short form of ``select(candidates).ranked``."""
        return self.select(candidates, trace=trace).ranked

    def explain(self, result: Result) -> str:
        """A human-readable account of how ``result`` was decided."""
        return explain_rows(ranked_rows(result.ranked), result.decision, result.run_id)

    # -- steps ---------------------------------------------------------------------------------------

    def _new_run(self) -> Run:
        capture = self.config.record.capture_content and os.environ.get("HONE_CAPTURE_CONTENT") != "0"
        return Run(self.config, self.components, self.sink, self.cache, Budget(self.config.budget), capture)

    def _run_attributes(self, run: Run, task: Any = None) -> dict[str, Any]:
        config = self.config.model_dump(mode="json")
        config_json = canonical_json(config)
        attributes: dict[str, Any] = {
            "hone.select.config_hash": hashlib.sha256(config_json.encode()).hexdigest()[:16],
            "hone.select.config": recorded_config(config, run),  # what was tested (design change 0008)
            "hone.select.policy": self.config.select.policy,
            "hone.select.n": self.config.generate.n,
        }
        if task is not None:
            attributes["hone.select.task"] = run.content(as_text(task)[:TASK_PREVIEW])
        return attributes

    def _generator(self) -> Component:
        generators = [c for c in self.components.values() if c.kind == "generator"]
        if len(generators) != 1:
            found = [g.name for g in generators]
            raise ConfigError(
                f"run() needs exactly one registered @generator, found {found}; "
                "use engine.select(candidates) to score existing candidates"
            )
        return generators[0]

    def _generate(self, run: Run, task: Any, seed: int) -> Iterator[Candidate]:
        """Yield candidates one at a time until ``n`` or the budget runs out."""
        generator = self._generator()
        for variation in variations(self.config.generate.n, self.config.generate.vary, seed):
            try:
                run.budget.check()
                candidate = self._generate_one(run, generator, task, variation)
            except BudgetExceeded as e:
                run.note("stop", reason=str(e), generated=variation["index"])
                return
            if candidate is not None:
                yield candidate

    def _generate_one(
        self, run: Run, generator: Component, task: Any, variation: Variation
    ) -> Candidate | None:
        with span(run.sink, "hone.select.generate") as s:
            run.budget.charge(generator.cost)
            started = time.monotonic()
            try:
                output = generator(task, variation)
            except BudgetExceeded:  # a generator's own budget stop ends generation, it is not an error
                raise
            except Exception as e:
                run.note("generate_error", index=variation["index"], error=run.mark_error(s, e))
                return None
            candidate = output if isinstance(output, Candidate) else Candidate.of(output)
            meta: dict[str, Any] = {
                "generator": generator.name,
                **variation,
                "seconds": round(time.monotonic() - started, 6),
                **candidate.meta,  # what the generator set itself wins
            }
            candidate = Candidate(candidate.id, candidate.data, candidate.files, meta)
            run.budget.charge(money=float(meta.get("cost_usd", 0.0)))
            s["attributes"]["hone.candidate_id"] = candidate.id
            s["attributes"]["hone.select.candidate"] = self._candidate_record(run, candidate)
        run.note("generated", candidate=candidate.id, index=variation["index"])
        return candidate

    @staticmethod
    def _candidate_record(run: Run, candidate: Candidate) -> dict[str, Any]:
        text = as_text(candidate.data)
        record: dict[str, Any] = {
            "id": candidate.id,
            "meta": run.content(dict(candidate.meta)),  # meta may hold a raw model response
            "data_sha256": hashlib.sha256(text.encode()).hexdigest(),
        }
        if run.capture_content:
            record["data_preview"] = text[:200]
        return record

    def _pipeline(
        self, run: Run, candidates: list[Candidate], earlier: list[Candidate] | None = None
    ) -> list[Scored]:
        """Dedup, gate and score ``candidates`` (in generation order)."""
        cfg = self.config.dedup
        method = cfg.method
        try:
            kept, removed = dedup(
                candidates, method, earlier=earlier or [], embedder=self.embedder, threshold=cfg.threshold
            )
        except Exception as e:  # the embedder is a port: record its failure, don't crash the run
            run.warn(f"embedding dedup failed ({type(e).__name__}: {e}); falling back to exact dedup")
            method = "exact"
            kept, removed = dedup(candidates, method, earlier=earlier or [])
        for candidate, original in removed:
            run.note("dedup", candidate=candidate.id, duplicate_of=original, method=method)
        scored = [Scored(c, {}, {}, None, False, 0) for c in kept]
        run_gates(run, scored)
        run_cascade(run, scored)
        return scored

    def _first_above(self, run: Run, stream: Iterator[Candidate]) -> list[Scored]:
        """Score each candidate as it arrives; stop generating once one reaches the threshold."""
        threshold = self.config.select.threshold or 0.0
        scored: list[Scored] = []
        for candidate in stream:
            new = self._pipeline(run, [candidate], [item.candidate for item in scored])
            scored.extend(new)
            if any(reaches(s, threshold) for s in new):
                run.note("stop", reason=f"threshold {threshold:g} reached", generated=len(scored))
                break
        return scored

    def _warn_goodhart(self, run: Run) -> None:
        scorers = {name for stage in self.config.score.cascade for name in stage.scorers}
        n = self.config.generate.n
        if scorers and n > 10 * len(scorers):
            run.warn(
                f"Goodhart risk: n={n} candidates for {len(scorers)} independent scorer(s); "
                "the winner may exploit scorer quirks. Add scorers or lower n."
            )

    def _warn_self_judging(self, run: Run, scored: list[Scored]) -> None:
        """Warn when a judge shares a model family with the model that generated the candidates."""
        generator_models = sorted({str(m) for s in scored if (m := s.candidate.meta.get("model"))})
        score = self.config.score
        used = {*score.gates, *(name for stage in score.cascade for name in stage.scorers)}
        used.add(self.config.select.pairwise or "")
        for component in (c for c in self.components.values() if c.name in used):
            family = model_family(component.judge_model)
            for model in generator_models:
                if family and family == model_family(model):
                    run.warn(
                        f"self-judging: {component.name!r} is judged by {component.judge_model!r}, the same "
                        f"model family as the generator model {model!r}; prefer a judge from another family"
                    )

    def _decide(self, run: Run, scored: list[Scored], root: dict[str, Any]) -> Result:
        self._warn_self_judging(run, scored)
        values = [s.value for item in scored for s in item.scores.values()]
        if values and all(v is None for v in values):
            run.warn("all scores are None: no scorer produced a value; the ranking carries no information")
        winner, ranked = select(run, scored)
        attributes = {
            "hone.select.winner_id": winner.candidate.id if winner else None,
            "hone.select.ranked": ranked_rows(ranked),
            "hone.select.decision_trace": run.content_trace(),
            "hone.select.fallback_used": any(e["event"] == "fallback" and e["winner"] for e in run.decision),
            "hone.select.escalated": any(e["event"] == "escalation" for e in run.decision),
        }
        with span(run.sink, "hone.select.decision", attributes):
            pass  # a point-in-time record of the decision; everything is in its attributes
        budget = run.budget.summary()
        root["attributes"].update({f"hone.select.budget.{k}": v for k, v in budget.items()})
        run_id, trace_id = root["span_id"], root["trace_id"]
        run.sink.flush()
        return Result(winner, ranked, run.decision, run_id, trace_id, budget)
