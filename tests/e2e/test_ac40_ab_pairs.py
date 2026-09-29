"""AC-40: an `ab` criterion with `between = "top"`, `top = 2`, `pairs = 6` over 3 cases: after the run
`ab_plan.json` holds 6 pairs, each the same case from the two best setups, 2 per case, left and right
seeded; failed and outside samples never appear; the API serves the next pair without setup names."""

import json
import shutil
import threading
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest

from hone_select import ConfigError
from hone_select.dashboard import make_server
from hone_select.experiments import ab, report
from hone_select.experiments import definition as d

from .ab_helpers import AB_CASES, AB_EXPERIMENT, FAILING, LARGE, MEDIUM, ab_plan, ab_run, result_of
from .experiment_helpers import project
from .test_ac29_experiment_ratings_and_dashboard import call

pytestmark = pytest.mark.e2e


def test_ac40_the_plan_pairs_the_same_case_of_the_two_best_setups(tmp_path: Path) -> None:
    _, folder = ab_run(tmp_path)
    plan = ab_plan(folder)["criteria"]["owner_pick"]
    assert plan["setups"] == [[LARGE, MEDIUM]]  # the two best by the automatic total
    pairs = plan["pairs"]
    assert len(pairs) == 6
    assert Counter(p["case"] for p in pairs) == {"c1": 2, "c2": 2, "c3": 2}
    for p in pairs:
        left, right = result_of(folder, p["left"]), result_of(folder, p["right"])
        assert {left["setup"], right["setup"]} == {LARGE, MEDIUM}
        assert left["case"] == right["case"] == p["case"]
        assert left["sample"] == right["sample"]  # the same sample index when both have it
        assert p["pair"] == [LARGE, MEDIUM]
    assert f"c2__{LARGE}__s1" not in {s for p in pairs for s in (p["left"], p["right"])}  # it failed
    sides = [result_of(folder, p["left"])["setup"] for p in pairs]
    assert set(sides) == {LARGE, MEDIUM}  # each setup is shown on the left sometimes


def test_ac40_the_draw_is_seeded_and_fixed(tmp_path: Path) -> None:
    _, folder = ab_run(tmp_path / "a")
    _, again = ab_run(tmp_path / "b")
    assert ab_plan(folder)["criteria"] == ab_plan(again)["criteria"]  # the same seed, the same draw
    first = ab_plan(folder)
    spec = d.load(folder)
    report(folder, spec, json.loads((folder / "plan.json").read_text()))
    assert ab_plan(folder) == first  # fixed: recomputing the results does not draw again


def test_ac40_outside_samples_never_appear(tmp_path: Path) -> None:
    _, folder = ab_run(tmp_path)
    outside = f"c3__{MEDIUM}__s0"
    path = folder / "outputs" / "c3" / MEDIUM / "s0" / "result.json"
    r = json.loads(path.read_text())
    r["environment"] = {**r["environment"], "status": "outside"}  # kept by on_violation = "record_only"
    path.write_text(json.dumps(r))
    (folder / "ab_plan.json").unlink()
    report(folder, d.load(folder), json.loads((folder / "plan.json").read_text()))
    pairs = ab_plan(folder)["criteria"]["owner_pick"]["pairs"]
    assert len(pairs) == 6
    assert outside not in {s for p in pairs for s in (p["left"], p["right"])}


@pytest.fixture
def served(tmp_path: Path) -> Iterator[str]:
    ab_run(tmp_path)
    srv = make_server(tmp_path / "no-store.db", port=0, project=tmp_path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def test_ac40_the_api_serves_the_next_pair_blind(served: str) -> None:
    status, pair = call(served + "/api/experiments/E0001/ab/owner_pick")
    assert status == 200
    assert pair["state"] == "pair"
    assert pair["question"] == "Which story would you rather read?"
    assert (pair["judged"], pair["total"], pair["allow_tie"]) == (0, 6, True)
    assert pair["left"]["data"].startswith("topic ")
    assert pair["left"]["files"] == ["story.txt"]
    text = json.dumps(pair)
    for secret in (LARGE, MEDIUM, "large", "medium", "sample_id", "setup", "params"):
        assert secret not in text  # nothing names a setup or a model
    index = pair["index"]
    status, content = call(f"{served}/api/experiments/E0001/ab/owner_pick/{index}/left/story.txt", raw=True)
    assert status == 200
    assert content.startswith(b"topic ")
    status, _ = call(f"{served}/api/experiments/E0001/ab/owner_pick/{index}/left/../../x", raw=True)
    assert status == 404
    status, x = call(served + "/api/experiments/E0001")
    assert x["ab"]["owner_pick"]["pairs"] == 6


def test_ac40_before_the_run_the_pairs_wait(tmp_path: Path) -> None:
    (tmp_path / "ab_subjects.py").write_text(FAILING)
    p, folder = project(tmp_path, AB_EXPERIMENT.format(ab="pairs = 6"), AB_CASES)
    p.plan("E0001")
    assert ab.next_pair(folder, d.load(folder), "owner_pick") == {"state": "waiting"}
    assert not (folder / "ab_plan.json").exists()


def test_ac40_unknown_setup_names_fail_at_plan_time(tmp_path: Path) -> None:
    (tmp_path / "ab_subjects.py").write_text(FAILING)
    p, _ = project(tmp_path, AB_EXPERIMENT.format(ab='between = ["today", "nobody"]'), AB_CASES)
    with pytest.raises(ConfigError, match="nobody"):
        p.plan("E0001")
    shutil.rmtree(tmp_path / "experiments")
    p, _ = project(tmp_path, AB_EXPERIMENT.format(ab=f'between = ["today", "{LARGE}"]'), AB_CASES)
    assert p.plan("E0001")["outputs"] == 27  # a baseline name and a setup id are both fine
