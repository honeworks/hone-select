"""Cascade and budget: cheap scorers first, expensive ones only on finalists, and spending limits.

What: a two-stage cascade where stage 1 keeps the best 2 (`keep_top`) so the costly stage-2 scorer runs
      twice instead of six times; a money budget that stops generation early; a cost budget that is used
      up before stage 2, so the selection is made on stage-1 totals.
How:  1. [score] cascade = [{ scorers = [...], keep_top = k }, { scorers = [...] }] - ties at the cut stay;
      2. give components a cost (@scorer(cost=...), @generator(cost=...)); a generator reports real money
         spent as meta["cost_usd"] on the Candidate it returns;
      3. [budget] max_cost / max_seconds / max_money_usd are checked before each generation and before
         each cascade stage after the first; once reached, generation stops, later stages are skipped,
         and the run selects among what exists. BudgetExceeded is not raised to you; look for the
         "stop" entry in result.decision and read result.budget.
      (Variation schedules, the other [generate] setting, are in variations_and_seeds.py.)
Why:  good selection needs many candidates but judges are slow and paid; a cascade spends the expensive
      checks where they can change the outcome, and a budget stops a run from growing without limit.
      Pitfalls: a budget is a stop condition, not a hard cap - the generation or stage already running
      finishes, so a run can overshoot by one generation plus one stage; set limits with that margin. A
      candidate cut at stage k has stage_reached = k and ranks after every finalist, whatever its total.
"""

from hone_select import Candidate, Engine, generator, scorer

calls = {"judge": 0}


@generator(cost=1)
def draft(task, v):
    text = f"{task} " + "la " * v["index"]
    return Candidate.of(text.strip(), meta={"cost_usd": 0.02})  # what this API call cost you


@scorer(cost=1)
def short(c):  # cheap: runs on everyone
    return 1 / len(c.data.split())


@scorer(cost=50)
def judge(c):  # expensive: runs on finalists only
    calls["judge"] += 1
    return 0.5


# 1. The cascade: 6 candidates, stage 1 keeps 2, the judge runs twice.
CASCADE = """
[generate]
n = 6
[score]
cascade = [
  { scorers = ["short"], keep_top = 2 },
  { scorers = ["judge"] },
]
"""
# cache=None: this example counts calls (and cached scores cost nothing, which would change the budget)
result = Engine(CASCADE, registry=[draft, short, judge], cache=None).run("sing")
print("stage reached:", [s.stage_reached for s in result.ranked])
print("budget used:", result.budget)
assert calls["judge"] == 2
assert [s.stage_reached for s in result.ranked] == [2, 2, 1, 1, 1, 1]
assert result.budget["cost_used"] == 6 * 1 + 6 * 1 + 2 * 50  # generator + stage 1 + stage 2

# 2. A money budget: each generation reports $0.02 and the limit is $0.05. After 3 generations $0.06 is
#    spent: the third started at $0.04, under the limit, so the run overshoots by part of one generation.
MONEY = CASCADE + "[budget]\nmax_money_usd = 0.05\n"
result = Engine(MONEY, registry=[draft, short, judge], cache=None).run("sing")
stop = next(entry for entry in result.decision if entry["event"] == "stop")
print("stop:", stop["reason"])
assert len(result.ranked) == 3 and result.winner is not None
assert stop["generated"] == 3
assert round(result.budget["money_used"], 2) == 0.06

# 3. A cost budget used up during stage 1: stage 1 finishes, stage 2 (the judge) is skipped,
#    and the stage-1 totals decide.
calls["judge"] = 0
COST = CASCADE + "[budget]\nmax_cost = 10\n"
result = Engine(COST, registry=[draft, short, judge], cache=None).run("sing")
print("decision:", [entry["event"] for entry in result.decision])
assert calls["judge"] == 0
assert result.budget["cost_used"] == 12  # 6 generations + stage 1 on 6: stage 1 finished past the limit
assert result.winner.candidate.data == "sing" and result.winner.stage_reached == 1
