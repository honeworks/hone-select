"""AC-9: PromptScorer, PromptGate and PromptPairwise send the right questions and normalize answers."""

import pytest

from hone_select import Candidate, Engine, PromptGate, PromptPairwise, PromptScorer
from hone_select.testing import FakeDecisionClient, MemorySink

pytestmark = pytest.mark.e2e

LYRICS = [
    Candidate.of({"lyrics": "la la la", "title": "A"}),
    Candidate.of({"lyrics": "do re mi", "title": "B"}),
]


def test_ac9_prompt_scorer_question_payload_and_normalization() -> None:
    judge = FakeDecisionClient(answers={"singable": {"value": None, "raw": 4}})  # raw only: we normalize
    singable = PromptScorer(
        "singable",
        "Easy to sing back.",
        judge,
        field="lyrics",
        scale=(1, 5),
        anchors={"1": "flat", "5": "instantly singable"},
    )
    ranked = Engine('[score]\ncascade = [{ scorers = ["singable"] }]', registry=[singable]).score(LYRICS[:1])

    call = judge.calls[0]
    assert call["state"] == "la la la"
    assert call["questions"] == {
        "singable": {
            "type": "score",
            "instructions": "Easy to sing back.",
            "scale": [1, 5],
            "anchors": {"1": "flat", "5": "instantly singable"},
        }
    }
    assert call["trace"]["hone.candidate_id"] == LYRICS[0].id
    assert call["trace"]["hone.scorer"] == "singable"
    assert ranked[0].scores["singable"].value == pytest.approx(0.75)


def test_ac9_checklist_mean() -> None:
    judge = FakeDecisionClient(
        answers={"hook_1": 1.0, "hook_2": 0.0, "hook_3": {"value": None, "error": "refused"}}
    )
    hook = PromptScorer(
        "hook", ["Has a hook.", "Hook repeats.", "Hook is in the title."], judge, output="yes_no"
    )
    score = Engine('[score]\ncascade = [{ scorers = ["hook"] }]', registry=[hook]).score(LYRICS[:1])[0]
    assert set(judge.calls[0]["questions"]) == {"hook_1", "hook_2", "hook_3"}
    assert all(q["type"] == "yes_no" for q in judge.calls[0]["questions"].values())
    assert judge.calls[0]["state"] == '{"lyrics":"la la la","title":"A"}'  # whole data, JSON-dumped
    assert score.scores["hook"].value == pytest.approx(0.5)  # mean of the answered items
    assert score.scores["hook"].details == {"hook_1": 1.0, "hook_2": 0.0, "hook_3": None}


def test_ac9_prompt_gate() -> None:
    judge = FakeDecisionClient(rule=lambda state, qs: {"clean": 0.2 if "la" in state else 0.9})
    clean = PromptGate("clean", "No profanity.", judge, threshold=0.5, field="lyrics")
    config = '[score]\ngates = ["clean"]'
    ranked = Engine(config, registry=[clean]).score(LYRICS)
    assert judge.calls[0]["questions"] == {"clean": {"type": "yes_no", "instructions": "No profanity."}}
    by_title = {s.candidate.data["title"]: s for s in ranked}
    assert by_title["A"].rejected
    assert by_title["A"].gates["clean"].probability == pytest.approx(0.2)
    assert not by_title["B"].rejected


def test_ac9_prompt_pairwise_both_orders() -> None:
    def prefer_do_re_mi(state, qs):
        return {"better": "A" if state["A"] == "do re mi" else "B"}

    judge = FakeDecisionClient(rule=prefer_do_re_mi)
    better = PromptPairwise("better", "Which chorus is catchier?", judge, field="lyrics")
    config = """
[select]
policy = "pairwise_tournament"
pairwise = "better"
"""
    ranked = Engine(config, registry=[better]).score(LYRICS)
    assert [c["state"] for c in judge.calls] == [
        {"A": "la la la", "B": "do re mi"},
        {"A": "do re mi", "B": "la la la"},
    ]
    assert judge.calls[0]["questions"]["better"] == {
        "type": "choice",
        "instructions": "Which chorus is catchier?",
        "options": ["A", "B", "tie"],
    }
    assert ranked[0].candidate.data["title"] == "B"


def test_ac9_judge_failure_is_explicit() -> None:
    judge = FakeDecisionClient(answers={"singable": {"value": None, "error": "context too long"}})
    singable = PromptScorer("singable", "Easy to sing.", judge)
    score = Engine('[score]\ncascade = [{ scorers = ["singable"] }]', registry=[singable]).score(LYRICS[:1])[
        0
    ]
    assert score.scores["singable"].value is None
    assert score.scores["singable"].error == "singable: context too long"

    gate_judge = FakeDecisionClient(answers={"clean": {"value": None, "error": "offline"}})
    clean = PromptGate("clean", "No profanity.", gate_judge)
    gated = Engine('[score]\ngates = ["clean"]', registry=[clean]).score(LYRICS[:1])[0]
    assert gated.rejected
    assert "offline" in gated.gates["clean"].reason


def test_ac9_config_defined_prompt_scorer_uses_named_judge() -> None:
    judge = FakeDecisionClient(answers={"singability": 0.8})
    config = """
[score]
cascade = [{ scorers = ["singability"] }]
[scorers.singability]
kind = "prompt"
judge = "local"
criteria = "The chorus can be sung back after one listen."
field = "lyrics"
"""
    ranked = Engine(config, judges={"local": judge}).score(LYRICS[:1])
    assert ranked[0].total == pytest.approx(0.8)
    assert judge.calls[0]["questions"]["singability"]["scale"] == [1, 5]


def test_ac9_gate_reads_field_and_passes_at_threshold() -> None:
    judge = FakeDecisionClient(answers={"clean": 0.5})
    clean = PromptGate("clean", "No profanity.", judge, threshold=0.5, field="lyrics")
    gated = Engine('[score]\ngates = ["clean"]', registry=[clean]).score(LYRICS[:1])[0]
    assert judge.calls[0]["state"] == "la la la"
    assert judge.calls[0]["trace"]["hone.scorer"] == "clean"
    assert not gated.rejected  # probability == threshold passes


def test_ac9_confidence_rationale_scale_and_images() -> None:
    judge = FakeDecisionClient(
        answers={"vivid": {"value": None, "raw": 7, "confidence": 0.8, "rationale": "ok"}}
    )
    vivid = PromptScorer("vivid", "How vivid?", judge, scale=(0, 10), images_from="cover")
    candidate = Candidate("id-1", "x", {"cover": "/p.png"}, {})
    score = vivid(candidate)
    assert (score.value, score.confidence, score.reason) == (pytest.approx(0.7), 0.8, "ok")
    assert judge.calls[0]["questions"]["vivid"]["scale"] == [0, 10]
    assert judge.calls[0]["images"] == ["/p.png"]

    checklist = FakeDecisionClient(
        answers={"c_1": {"value": 1.0, "confidence": 0.9}, "c_2": {"value": 0.0, "confidence": 0.6}}
    )
    items = PromptScorer("c", ["one", "two"], checklist, output="yes_no")
    score = items(LYRICS[0])
    assert score.confidence == 0.6  # the least confident item decides
    assert checklist.calls[0]["images"] == []
    assert "scale" not in checklist.calls[0]["questions"]["c_1"]


def test_ac9_partial_checklist_reports_the_failed_item() -> None:
    judge = FakeDecisionClient(answers={"c_1": 0.8, "c_2": {"value": None, "error": "refused"}})
    score = PromptScorer("c", ["one", "two"], judge, output="yes_no")(LYRICS[0])
    assert score.value == pytest.approx(0.8)
    assert score.error == "c_2: refused"


def test_ac9_bad_answers_are_errors() -> None:
    out_of_range = FakeDecisionClient(answers={"clean": 1.7})
    gated = Engine('[score]\ngates = ["clean"]', registry=[PromptGate("clean", "ok?", out_of_range)]).score(
        LYRICS[:1]
    )[0]
    assert gated.rejected
    assert "outside 0..1" in gated.gates["clean"].reason

    class Silent(FakeDecisionClient):
        def decide(self, state, questions, *, images=(), trace=None):
            super().decide(state, questions, images=images, trace=trace)
            return {}

    score = PromptScorer("s", "ok?", Silent())(LYRICS[0])
    assert (score.value, score.error) == (None, "s: not answered")

    with pytest.raises(Exception, match=r"judges=\{'local': client\}"):
        PromptScorer("s", "ok?", "local")(LYRICS[0])


def test_ac9_pairwise_tie_and_invalid_choice() -> None:
    tie = PromptPairwise(
        "better", "Which?", FakeDecisionClient(answers={"better": {"choice": "tie", "confidence": 0.4}})
    )
    assert tie(LYRICS[0], LYRICS[1]) == ("tie", 0.4)

    bad = PromptPairwise("better", "Which?", FakeDecisionClient(answers={"better": {"choice": "C"}}))
    config = '[select]\npolicy = "pairwise_tournament"\npairwise = "better"'
    sink = MemorySink()
    ranked = Engine(config, registry=[bad], sink=sink).score(LYRICS)
    assert [s.candidate.id for s in ranked] == [c.id for c in LYRICS]  # original order kept
    pairwise_spans = sink.named("hone.select.pairwise")
    assert pairwise_spans
    assert all("no usable choice" in s["status"]["message"] for s in pairwise_spans)
    trace = sink.named("hone.select.decision")[0]["attributes"]["hone.select.decision_trace"]
    assert any(e["event"] == "pairwise_error" for e in trace)


SCENE = [
    Candidate("s1", "scene 1", {"reference": "/sheet.png", "image": "/s1.png"}, {}),
    Candidate("s2", "scene 2", {"reference": "/sheet.png", "image": "/s2.png"}, {}),
]


def test_ac9_prompt_scorer_sends_several_images_in_order() -> None:
    """Design change 0005: images_from takes a sequence of file keys."""
    judge = FakeDecisionClient(answers={"same_person": 0.9})
    same = PromptScorer(
        "same_person",
        "The first image is the reference; does the second show the same person?",
        judge,
        images_from=("reference", "image"),
    )
    sink = MemorySink()
    Engine(
        '[score]\ncascade = [{ scorers = ["same_person"] }]', registry=[same], sink=sink, cache=None
    ).score(SCENE[:1])
    assert judge.calls[0]["images"] == ["/sheet.png", "/s1.png"]
    assert sink.named("hone.select.score")[0]["attributes"]["hone.select.image_keys"] == [
        "reference",
        "image",
    ]


def test_ac9_images_from_list_in_config_and_missing_file() -> None:
    judge = FakeDecisionClient(answers={"same_person": 0.9})
    config = """
[score]
cascade = [{ scorers = ["same_person"] }]
[scorers.same_person]
kind = "prompt"
judge = "local"
criteria = "The first image is the reference; same person in the second?"
images_from = ["reference", "image"]
"""
    no_reference = Candidate("s3", "scene 3", {"image": "/s3.png"}, {})
    ranked = Engine(config, judges={"local": judge}, cache=None).score([SCENE[0], no_reference])
    assert judge.calls[0]["images"] == ["/sheet.png", "/s1.png"]
    missing = next(s for s in ranked if s.candidate.id == "s3").scores["same_person"]
    assert missing.value is None
    assert "no file 'reference'" in missing.error


def test_ac9_prompt_pairwise_sends_images_in_both_orders() -> None:
    """Design change 0007: PromptPairwise(images_from=...) shows A's images, then B's."""
    judge = FakeDecisionClient(answers={"thumb": "A"})
    thumb = PromptPairwise(
        "thumb", "The first image is A, the second is B. Which thumbnail?", judge, images_from="image"
    )
    assert thumb(SCENE[0], SCENE[1]) == ("a", None)
    assert thumb(SCENE[1], SCENE[0]) == ("a", None)
    assert [c["images"] for c in judge.calls] == [["/s1.png", "/s2.png"], ["/s2.png", "/s1.png"]]


def test_ac9_prompt_pairwise_shared_reference_is_sent_once() -> None:
    judge = FakeDecisionClient(answers={"closer": "B"})
    closer = PromptPairwise(
        "closer", "Image 1 is the reference, 2 is A, 3 is B.", judge, images_from=("reference", "image")
    )
    config = '[select]\npolicy = "pairwise_tournament"\npairwise = "closer"\n[record]\nsink = "none"'
    Engine(config, registry=[closer], cache=None).score(SCENE)
    assert [c["images"] for c in judge.calls] == [
        ["/sheet.png", "/s1.png", "/s2.png"],
        ["/sheet.png", "/s2.png", "/s1.png"],
    ]


def test_ac9_pairwise_span_notes_image_keys() -> None:
    judge = FakeDecisionClient(answers={"thumb": "A"})
    thumb = PromptPairwise("thumb", "Which thumbnail?", judge, images_from="image")
    sink = MemorySink()
    config = '[select]\npolicy = "pairwise_tournament"\npairwise = "thumb"'
    Engine(config, registry=[thumb], sink=sink, cache=None).score(SCENE)
    assert all(
        s["attributes"]["hone.select.image_keys"] == ["image"] for s in sink.named("hone.select.pairwise")
    )
