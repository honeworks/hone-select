# Configuration reference (`selection.toml`)
*Example: [`config_file.py`](../examples/config_file.py).*

`Engine(config)` accepts a `SelectionConfig`, a path to a `.toml` file, or TOML text. Every section is
optional. Unknown keys, a scorer name that is not registered, a weight for an unknown scorer, or a bad
policy raise `ConfigError`, and the message says what to change.

```toml
[judges.local]                  # a DecisionClient built from config (entry point "hone.decision_clients")
client = "hone_models:decision" # other keys are passed to the factory
model  = "qwen2.5vl-7b"

[generate]
n = 6                           # candidates to generate (default 4)
vary = { seed = "increment", temperature = [0.8, 0.9, 1.0] }   # seed: "increment" or a list; lists cycle
max_concurrency = 1             # v0.1 generates sequentially

[dedup]
method = "exact"                # exact | embedding | off
threshold = 0.95                # cosine similarity for "embedding"

[score]
gates   = ["hook_on_rhyme"]     # run cheapest first; the first failure rejects
cascade = [
  { scorers = ["syllable_balance"],            keep_top = 4 },
  { scorers = ["lyric_recall", "singability"], keep_top = 2 },
]
weights   = { syllable_balance = 0.2, lyric_recall = 0.5, singability = 0.3 }
aggregate = "weighted_mean"     # weighted_mean | min | geometric | weighted_mean_with_floor
floor     = 0.3                 # for weighted_mean_with_floor
missing   = "renormalize"       # renormalize | zero | reject

[scorers.singability]           # a prompt scorer defined in config, no code
kind = "prompt"
judge = "local"                 # a [judges.*] name or a key of Engine(judges={...})
criteria = "The chorus can be sung back after one listen."   # a list of strings is a checklist
output = "score"                # score | yes_no
scale = [1, 5]
anchors = { 1 = "flat", 3 = "competent", 5 = "instantly singable" }
field = "lyrics"                # judge candidate.data["lyrics"] (default: all of data)
images_from = "cover"           # optional: send candidate.files["cover"] as an image;
                                # a list (["reference", "image"]) sends several, in order
cost = 5

[scorers.tests_pass]            # a command scorer
kind = "command"
command = ["./scripts/run_tests.sh"]
timeout_s = 60
cost = 20

[select]
policy         = "argmax"       # argmax | first_above | pairwise_tournament
threshold      = 0.75           # for first_above
tie_margin     = 0.03           # top candidates this close count as a tie
min_confidence = 0.6            # a deciding score below this escalates
escalate       = "pairwise"     # pairwise | none (default none)
pairwise       = "better_lyric" # a @pairwise function or PromptPairwise name
max_biased_pairwise = 2         # optional: stop asking a judge that chose by position this many times in a row
fallback       = "best_rejected"  # best_rejected | first_valid | none (default none)

[budget]
max_cost = 1000
max_seconds = 3600
max_money_usd = 2.0

[record]
sink = "sqlite"                 # sqlite | jsonl | none
path = ".hone/select/spans.db"  # default: $HONE_HOME/select/spans.db, HONE_HOME defaults to .hone
capture_content = true          # false: store hashes, not candidate text (also HONE_CAPTURE_CONTENT=0)
```

The config above is loaded by the test suite (`tests/fixtures/selection.toml`). The same settings as Python
objects:

```python
from hone_select import SelectionConfig

config = SelectionConfig.model_validate(
    {"generate": {"n": 3}, "select": {"policy": "argmax", "fallback": "best_rejected"}}
)
assert config.generate.n == 3
```
