"""AC-41: picks through the dashboard API (left, right, tie, undo) are stored in `ab.jsonl` as specified; undo
removes the last pick; a write without the page's header is refused and stores nothing."""

import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from hone_select import ConfigError
from hone_select.dashboard import make_server
from hone_select.experiments import ab
from hone_select.experiments import definition as d

from .ab_helpers import LARGE, MEDIUM, ab_plan, ab_run
from .test_ac29_experiment_ratings_and_dashboard import PAGE, call

pytestmark = pytest.mark.e2e


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    _, folder = ab_run(tmp_path)
    srv = make_server(tmp_path / "no-store.db", port=0, project=tmp_path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", folder
    srv.shutdown()
    srv.server_close()


def lines(folder: Path) -> list[dict]:
    path = folder / "ab.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def test_ac41_picks_undo_and_refused_writes(served: tuple[str, Path]) -> None:
    base, folder = served
    url = base + "/api/experiments/E0001/ab"
    first = call(url + "/owner_pick")[1]
    assert call(url, {"criterion": "owner_pick", "index": first["index"], "choice": "left"})[0] == 403
    assert lines(folder) == []  # the refused write stored nothing
    plan = ab_plan(folder)["criteria"]["owner_pick"]["pairs"]
    for choice in ("left", "right", "tie"):
        pair = call(url + "/owner_pick")[1]
        status, body = call(url, {"criterion": "owner_pick", "index": pair["index"], "choice": choice}, PAGE)
        assert (status, body["ok"]) == (200, True)
        planned = plan[pair["index"]]
        stored = lines(folder)[-1]
        assert stored["choice"] == choice
        assert stored["criterion"] == "owner_pick"
        assert stored["pair"] == [LARGE, MEDIUM]
        assert (stored["case"], stored["left"], stored["right"]) == (
            planned["case"],
            planned["left"],
            planned["right"],
        )
        assert set(stored) == {"criterion", "pair", "case", "left", "right", "choice", "at"}
    assert call(url + "/owner_pick")[1]["judged"] == 3
    status, _ = call(url + "/undo", {"criterion": "owner_pick"}, PAGE)
    assert status == 200
    assert lines(folder)[-1] == {"undo": 2}  # the file is append-only: undo names the line it removes
    after = call(url + "/owner_pick")[1]
    assert (after["judged"], after["can_undo"]) == (2, True)
    assert after["index"] == pair["index"]  # the undone pair comes back
    assert call(url + "/undo", {"criterion": "owner_pick"})[0] == 403  # no header: refused
    assert len(lines(folder)) == 4


def test_ac41_bad_picks_are_refused(served: tuple[str, Path]) -> None:
    base, folder = served
    url = base + "/api/experiments/E0001/ab"
    pair = call(url + "/owner_pick")[1]
    for body in (
        {"criterion": "owner_pick", "index": pair["index"], "choice": "both"},
        {"criterion": "owner_pick", "index": 99, "choice": "left"},
        {"criterion": "length", "index": pair["index"], "choice": "left"},
    ):
        assert call(url, body, PAGE)[0] == 400
    assert call(url + "/undo", {"criterion": "owner_pick"}, PAGE)[0] == 400  # nothing to undo
    assert call(url, {"criterion": "owner_pick", "index": pair["index"], "choice": "left"}, PAGE)[0] == 200
    again = {"criterion": "owner_pick", "index": pair["index"], "choice": "right"}
    assert call(url, again, PAGE)[0] == 400  # a pair is picked once (undo first)
    assert len(lines(folder)) == 1


def test_ac41_a_tie_needs_allow_tie(tmp_path: Path) -> None:
    _, folder = ab_run(tmp_path, "pairs = 2\nallow_tie = false")
    spec = d.load(folder)
    pair = ab.next_pair(folder, spec, "owner_pick")
    assert pair["allow_tie"] is False
    with pytest.raises(ConfigError, match="tie"):
        ab.add(folder, spec, "owner_pick", pair["index"], "tie")
    ab.add(folder, spec, "owner_pick", pair["index"], "right")
    ab.add(folder, spec, "owner_pick", ab.next_pair(folder, spec, "owner_pick")["index"], "left")
    assert ab.next_pair(folder, spec, "owner_pick") == {"state": "done", "judged": 2, "total": 2}
