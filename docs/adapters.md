# Bring your own client

Prompt judges talk to a `DecisionClient` ([design §7.2](../design/current.md#72-decisionclient)):
`decide(state, questions, *, images=(), trace=None) -> {name: answer}`. An answer has
`type`, `value` (0..1, or `None` with an `error`), and optionally `choice`, `probabilities`, `raw`,
`confidence`, `calibrated` and `rationale`. Any object with that method works; the adapters below cover the
common cases.

## OpenAI (and OpenAI-compatible servers such as Ollama)
*Example: [`bring_your_own_client.py`](../examples/bring_your_own_client.py).*

`pip install "hone-select[openai]"`

```python
from openai import OpenAI

from hone_select import PromptScorer
from hone_select.adapters.openai import OpenAIDecisionClient, OpenAITextClient

judge = OpenAIDecisionClient(OpenAI(api_key="sk-..."), model="gpt-4.1-mini")

# a local model through Ollama's OpenAI-compatible endpoint
local = OpenAIDecisionClient(
    OpenAI(base_url="http://127.0.0.1:11434/v1", api_key="ollama"), model="qwen2.5vl:7b", seed=1
)

singable = PromptScorer("singable", "The chorus can be sung back after one listen.", local, field="lyrics")
text = OpenAITextClient(OpenAI(api_key="sk-..."), model="gpt-4.1-mini", temperature=0.7)
```

Decisions are emulated: all questions go in one prompt, and the model replies with JSON
`{name: {"rationale", "answer"}}` requested through `response_format` (JSON schema). Plain, fenced
(```json) and chatty replies are parsed. An answer outside the scale or options becomes
`value=None, error="unusable answer ..."`, never a made-up number. Answers are `calibrated=False`.
`OpenAIDecisionClient` uses `temperature=0` by default; pass `temperature=None` for models that reject it.
Transport errors (bad model, rate limit, timeout) raise the SDK's exceptions; inside a run they become
`Score(None, error=...)`.

## LangChain
`pip install "hone-select[langchain]"`, then wrap any chat model:

```python
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from hone_select.adapters.langchain import LangChainDecisionClient

chat = FakeListChatModel(responses=['{"clear": {"rationale": "short", "answer": 4}}'])  # e.g. ChatOpenAI(...)
judge = LangChainDecisionClient(chat)
assert (
    judge.decide("Hi.", {"clear": {"type": "score", "instructions": "How clear?"}})["clear"]["value"] == 0.75
)
```

Set temperature and other options on the chat model itself.

## hone-models
*Example: [`hone_models_judge.py`](../examples/hone_models_judge.py).*

With `hone-models` installed (`hone-select[models]`), name it in config:

```toml
[judges.local]
client = "hone_models:decision"   # the "hone.decision_clients" entry point
model  = "qwen2.5vl-7b"            # other keys go to hone_models.decision(...)
```

or pass clients directly: `Engine(..., judges={"local": hone_models.decision("qwen2.5vl-7b")})`.
`model` is a hone-models registry id, not an Ollama tag: `qwen2.5vl-7b` is in hone-models' packaged
registry and runs `qwen2.5vl:7b` on a local Ollama (`ollama pull qwen2.5vl:7b` first). To use another
model, or to point an id at a tag you have pulled, add an entry to your registry in
`~/.config/hone/models.toml` (see
[the hone-models registry docs](https://github.com/honeworks/hone-models/blob/main/docs/registry.md)).
Other packages can register their own factories in the `hone.decision_clients` entry-point group.

hone-models also provides the machine probe for an experiment's run conditions
([experiments](experiments.md#run-conditions)): `probe = "hone_models:machine"` in `[conditions]` (the
`hone.machine_probes` entry point) reports the loaded models and their VRAM and unloads the ones an
experiment does not need. Without hone-models installed, planning such an experiment raises a
`ConfigError` that names `hone-select[models]`. Any object with `snapshot()` and `prepare(needed, *,
if_busy=...)` fits (`hone_select.ports.MachineProbe`); check yours with
`hone_select.testing.check_machine_probe`, and use `FakeMachineProbe` in tests.

It also provides what experiments need for image, music and video models
([experiments](experiments.md#generation-experiments-per-model-asks-and-needs)): `client =
"hone_models:image"` (`:music`, `:video`) in a `kind = "generate"` subject resolves through the
`hone.image_clients` / `hone.music_clients` / `hone.video_clients` entry points, and `guides =
"hone_models:guides"` (the `hone.model_guides` entry point, the default with those clients) reads each
model's guide: what it can take, its limits and license, and whether it is installed. Any object with
`guide(model_id)` returning the guide as a mapping (or `None`) fits (`hone_select.ports.ModelGuides`);
check yours with `hone_select.testing.check_model_guides`, and use `FakeModelGuides` in tests.

## Your own client and the contract checkers
*Example: [`bring_your_own_client.py`](../examples/bring_your_own_client.py).*

Write a class with `decide(...)` and check it with the exported contract checker. The fakes in
`hone_select.testing` pass the same checks and are handy in your tests.

```python
from hone_select.testing import FakeDecisionClient, FakeEmbedder, FakeMachineProbe, FakeTextClient, contracts


class AlwaysYes:
    model_id = "always-yes"

    def decide(self, state, questions, *, images=(), trace=None):
        answers = {}
        for name, q in questions.items():
            if q["type"] == "choice":
                answers[name] = {"type": "choice", "value": 1.0, "choice": q["options"][0]}
            else:
                answers[name] = {"type": q["type"], "value": 1.0}
        return answers


contracts.check_decision_client(AlwaysYes())
contracts.check_decision_client(FakeDecisionClient(answers={"q1": "yes"}))
contracts.check_text_client(FakeTextClient())
contracts.check_machine_probe(FakeMachineProbe())
contracts.check_embedder(FakeEmbedder())
```

`model_id` (or `model`) on a judge is used in the score cache key and the self-judging check.
