# Scorers, gates and pairwise judges

Every component has a `name` (default: the function name), a `cost` (for budgets and cascade ordering) and
a `version` (part of the score cache key; bump it when you change the logic). Pass components to
`Engine(..., registry=[...])`; config refers to them by name.

## Code: decorated functions
*Examples: [`gates_and_fallbacks.py`](../examples/gates_and_fallbacks.py), [`other_packages_through_ports.py`](../examples/other_packages_through_ports.py).*

```python
from hone_select import Candidate, GateResult, Score, gate, pairwise, scorer


@gate(cost=0.1)
def has_hook(c):
    return GateResult("hook" in c.data, reason="needs the word hook", details={"length": len(c.data)})


@scorer(cost=1, version="2")
def brevity(c):
    if not c.data:
        return None  # could not score: not the same as 0
    return Score(1 - min(len(c.data), 100) / 100, confidence=0.9, reason=f"{len(c.data)} chars")


@pairwise()
def shorter(a, b):
    return "a" if len(a.data) < len(b.data) else "b" if len(b.data) < len(a.data) else "tie"


assert brevity(Candidate.of("a hook")).value == 0.94
assert shorter(Candidate.of("x"), Candidate.of("xy")) == "a"
```

A gate's `details` (per-item verdicts, for example which questions of a quiz passed) are kept in
`scored.gates[name].details` and recorded on the gate span as `hone.select.gate.details` (content: only a
hash when content capture is off).

Any object works too if it is callable and has `name`, plus optional `kind` (`"scorer"`, `"gate"`,
`"pairwise"`), `cost` and `version` attributes, and returns a float, a `Score` or a `Score`-like
object with `value`. This is the "scorer shape" other packages (such as hone-taste panels) implement.

## Prompt judges
*Example: [`prompt_scorers.py`](../examples/prompt_scorers.py).*

`PromptScorer`, `PromptGate` and `PromptPairwise` ask a `DecisionClient` structured questions
(see [adapters](adapters.md) for real clients). The fake below shows the questions they send.

```python
from hone_select import Candidate, PromptGate, PromptPairwise, PromptScorer
from hone_select.testing import FakeDecisionClient

judge = FakeDecisionClient(answers={"singable": 0.75, "clean": "yes", "better": "A"})

singable = PromptScorer(
    "singable",
    "Easy to sing back after one listen.",
    judge,
    field="lyrics",
    scale=(1, 5),
    anchors={"1": "flat", "5": "instantly singable"},
)
clean = PromptGate("clean", "Free of profanity.", judge, threshold=0.5)
better = PromptPairwise("better", "Which chorus is catchier?", judge, field="lyrics")

song = Candidate.of({"lyrics": "Sun on our shoulders"})
assert singable(song).value == 0.75
assert clean(song).passed
assert judge.calls[0]["questions"]["singable"]["type"] == "score"
```

- `criteria` as a list makes a checklist: one question per item; the value is the mean.
- `output="yes_no"` asks a yes/no question; the value is the probability of "yes".
- `PromptPairwise` asks one order per call; the engine always asks both orders and counts a win only when
  they agree.
- `images_from` sends files of the candidate as images: one key (`images_from="image"`) or several, in
  order (`images_from=("reference", "image")`, e.g. a reference sheet plus the candidate's picture; put
  the shared reference into every candidate's `files`). Say in the criteria which image is which.
  `PromptPairwise(images_from=...)` sends A's images, then B's, and a file both share (the same path) only
  once, first. Example: [`judging_images.py`](../examples/judging_images.py).
- `judge` may be a name (`"local"`), resolved from `Engine(judges={...})` or a `[judges.local]` section.
- If the judge's model shares a family with the generator's `meta["model"]` (both `gemma...`), a
  self-judging warning is recorded.

## Command scorers
*Example: [`command_scorer.py`](../examples/command_scorer.py).*

`CommandScorer(name, command, cost=20, timeout_s=60, version="1")` runs an executable once per candidate.
stdin is `{"candidate": {"id", "data", "files", "meta"}}`, stdout is
`{"value": 0.9, "reason": "...", "confidence": 0.8, "details": {}}`. A non-zero exit, bad JSON or a
timeout gives `Score(None, error=<stderr tail>)`.

```python
import sys

from hone_select import Candidate, CommandScorer

script = "import json, sys; c = json.load(sys.stdin)['candidate']; print(json.dumps({'value': len(c['data']) / 10}))"
length = CommandScorer("length", [sys.executable, "-c", script], cost=5)
assert length(Candidate.of("hello")).value == 0.5
```

Define one in config with `[scorers.<name>] kind = "command"` (see [config](config.md)).
