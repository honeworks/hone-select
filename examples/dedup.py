"""Dedup: drop duplicate candidates before paying to score them - exactly, or by meaning.

What: exact dedup (the default: same Candidate.id, i.e. same data and files) and embedding dedup
      (cosine similarity >= threshold) through an Embedder - here FakeEmbedder with scripted similarity.
How:  1. [dedup] method = "exact" (default) | "embedding" | "off"; threshold = 0.95 for embedding;
      2. for "embedding", pass Engine(..., embedder=...): any object with model_id, dimensions and
         embed(texts, *, trace=None) -> L2-normalized vectors (e.g. a hone-models embedder);
      3. each removed candidate appears in result.decision as {"event": "dedup", "candidate",
         "duplicate_of", "method"}; the first one generated is kept.
Why:  samplers often repeat themselves; duplicates waste scorer budget and crowd the ranking. Exact dedup
      is free; embedding dedup also catches paraphrases. Pitfall: if the embedder fails, the engine
      records a warning and falls back to exact dedup instead of crashing the run.
"""

from hone_select import Engine, generator, scorer
from hone_select.testing import FakeEmbedder

DRAFTS = [
    "the night is young",
    "the night is young",  # exact duplicate of 0
    "the evening is young",  # a paraphrase of 0
    "we ride at dawn",
]


@generator()
def write(task, v):
    return DRAFTS[v["index"]]


@scorer()
def length(c):
    return len(c.data) / 30


SCORE = """
[generate]
n = 4
[score]
cascade = [{ scorers = ["length"] }]
"""


def removed(result):
    return [
        (e["candidate"], e["duplicate_of"], e["method"]) for e in result.decision if e["event"] == "dedup"
    ]


# 1. Exact: only the identical draft is removed.
result = Engine(SCORE, registry=[write, length]).run("a line")
print("exact kept:", [s.candidate.data for s in result.ranked])
assert len(result.ranked) == 3 and len(removed(result)) == 1

# 2. Embedding: the paraphrase is removed too. FakeEmbedder(similar={b: a}) embeds b exactly like a.
embedder = FakeEmbedder(similar={"the evening is young": "the night is young"})
result = Engine(
    SCORE + '[dedup]\nmethod = "embedding"\nthreshold = 0.9\n', registry=[write, length], embedder=embedder
).run("a line")
print("embedding kept:", [s.candidate.data for s in result.ranked])
print("removed:", removed(result))
assert sorted(s.candidate.data for s in result.ranked) == ["the night is young", "we ride at dawn"]
assert [method for *_, method in removed(result)] == ["embedding", "embedding"]
assert embedder.calls == [DRAFTS]  # one batch with every candidate's text


# 3. A failing embedder does not crash the run: a warning is recorded and exact dedup is used.
class BrokenEmbedder:
    model_id, dimensions = "broken", 8

    def embed(self, texts, *, trace=None):
        raise ConnectionError("embedding server is down")


result = Engine(
    SCORE + '[dedup]\nmethod = "embedding"\n', registry=[write, length], embedder=BrokenEmbedder()
).run("a line")
warning = next(e["message"] for e in result.decision if e["event"] == "warning")
print("warning:", warning)
assert "falling back to exact dedup" in warning and len(result.ranked) == 3
