"""Judging images: show a vision judge several pictures, and break image ties with a pairwise judge.

What: PromptScorer(images_from=("reference", "image")) sends a shared reference picture and the
      candidate's picture, in that order; PromptPairwise(images_from="image") compares two candidates'
      pictures (A's first, then B's; a file both share is sent once, first).
How:  1. put every picture the judge needs into Candidate.files - including a reference that is the
         same for every candidate (Candidate.of(data, files={"reference": sheet, "image": picture}));
      2. images_from names the file keys, in the order the judge sees them; a single string is one key;
         the [scorers.<name>] config form is images_from = ["reference", "image"];
      3. say in the criteria which image is which ("the first image is the reference");
      4. for ties, PromptPairwise(images_from=...) with [select] escalate = "pairwise": the engine asks
         both orders, so the images are swapped too.
Why:  pasting a reference and a candidate side by side into one new picture shrinks both and judges
      tend to read the halves as one scene; sending separate images keeps full resolution.
      Pitfall: the judge's client must accept several images (vision models of hone-models do); the
      image file keys are recorded on the score and pairwise spans as hone.select.image_keys.
"""

import shutil
import tempfile
from pathlib import Path

from hone_select import Candidate, Engine, PromptPairwise, PromptScorer
from hone_select.testing import FakeDecisionClient, MemorySink

folder = Path(tempfile.mkdtemp())
sheet = folder / "character_sheet.png"
sheet.write_bytes(b"reference picture")
pictures = []
for name in ("scene_a.png", "scene_b.png"):
    (folder / name).write_bytes(name.encode())
    pictures.append(str(folder / name))

candidates = [
    Candidate.of({"scene": i}, files={"reference": str(sheet), "image": p}) for i, p in enumerate(pictures)
]


def rule(state, questions):
    """The fake vision judge: both candidates look equally like the reference; scene_b is the better shot."""
    if "better_shot" in questions:  # state = {"A": ..., "B": ...}, the candidates' data as JSON
        return {"better_shot": "A" if '"scene":1' in state["A"] else "B"}
    return {"same_person": 0.8}


judge = FakeDecisionClient(rule=rule, model_id="qwen2.5vl-7b")
same_person = PromptScorer(
    "same_person",
    "The first image is the reference sheet. Does the second image show the same person?",
    judge,
    images_from=("reference", "image"),
)
better_shot = PromptPairwise(
    "better_shot",
    "The first image is the reference, the second is A, the third is B. Which shot shows that person better?",
    judge,
    images_from=("reference", "image"),
)

CONFIG = """
[score]
cascade = [{ scorers = ["same_person"] }]
[select]
escalate = "pairwise"
pairwise = "better_shot"
"""
sink = MemorySink()
result = Engine(CONFIG, registry=[same_person, better_shot], sink=sink, cache=None).select(candidates)

score_calls = [call for call in judge.calls if "same_person" in call["questions"]]
pair_calls = [call for call in judge.calls if "better_shot" in call["questions"]]
print("scorer images:", [Path(p).name for p in score_calls[0]["images"]])
print("pairwise images:", [[Path(p).name for p in call["images"]] for call in pair_calls])
assert score_calls[0]["images"] == [str(sheet), pictures[0]]  # reference first, as images_from says
assert pair_calls[0]["images"] == [str(sheet), pictures[0], pictures[1]]  # the shared sheet once, then A, B
assert pair_calls[1]["images"] == [str(sheet), pictures[1], pictures[0]]  # the second order swaps A and B
assert result.winner is not None and result.winner.candidate.files["image"] == pictures[1]
assert sink.named("hone.select.pairwise")[0]["attributes"]["hone.select.image_keys"] == ["reference", "image"]
shutil.rmtree(folder)  # the pictures were only for this example
