"""The commented `experiment.toml` that `hone-select experiments new` writes (design change 0009 §2)."""

TEMPLATE = """# {title}
# Edit this file, then: hone-select experiments plan <EID>   (add --pilot to measure one sample)
# Approve the plan in the dashboard (or `experiments approve <EID>`),
# then: hone-select experiments start <EID>

title = "{title}"
question = "What should this experiment decide?"

cases = "cases/"          # cases/cases.toml ([[case]] id + fields), and / or one folder of files per case
# cases = {{ from = "E0001", keep = "winners" }}   (an earlier experiment's outputs as cases)
samples = 1               # outputs per setup and case (different seeds)
seed = 0
registry = []             # modules with your @scorer / @gate functions, e.g. ["myproject.criteria"]

# What is tested (one of three kinds) ---------------------------------------------------------------
[generate]
kind = "prompt"                       # prompt | python | command
client = "hone_models:text"           # prompt: "module:factory" returning a TextClient; called with the model
prompt = "{{prompt}}"                 # a template; "{{prompt}}" uses the file in prompts/ named by the factor
output = "text"                       # text | json
# kind = "python"   ->  function = "myproject.module:function"      (called with case, setup, ctx)
# kind = "command"  ->  command = ["python", "{{experiment}}/scripts/run.py", "{{setup_json}}", "{{workdir}}"]
#   (runs from the project root; {{experiment}} is this folder, {{workdir}} the sample's own folder)
# timeout = 1800
# wrap = ["scripts/gpu-lock.sh"]      # run the command inside a wrapper
# keep_files = "all"                  # all | small | none

# What is compared -----------------------------------------------------------------------------------
[factors]
model  = ["gemma4-12b"]
prompt = ["plain.md"]
# temperature = [0.7, 1.0]

[design]
kind = "full"                         # full (every combination) | one_at_a_time (around the baseline) | list

# [[baseline]]
# name = "today"
# model = "gemma4-12b"
# prompt = "plain.md"

# How it is measured ---------------------------------------------------------------------------------
[criteria]
gates   = []
scorers = []                          # code, command, prompt-judge and human scorers by name
# measure = {{ seconds = "lower" }}     # automatic measurements as criteria

# [scorers.owner_look]                # rated by a person in the dashboard, blind to the setup
# kind = "human"
# question = "How much do you like it?"
# scale = [1, 5]

[budget]
# money_usd = 10
# seconds = 36000

[run]
order = "model"                       # run every cell of one model before the next
"""
