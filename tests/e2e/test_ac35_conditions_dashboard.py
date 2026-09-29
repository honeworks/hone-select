"""AC-35: the Experiments page shows run conditions: the list carries the `waiting` status and its reason;
an experiment carries the declared conditions, the plan's reading before approval and the waits; each sample
row carries its environment; outside samples are marked and the results say how many were not counted."""

import json
import threading
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from hone_select.dashboard import make_server
from hone_select.experiments import start

from .condition_helpers import BUSY, CONDITIONS_EXPERIMENT, QUIET, FakeMachine, ready

pytestmark = pytest.mark.e2e


@contextmanager
def serving(root: Path) -> Iterator[str]:
    srv = make_server(root / "no-store.db", port=0, project=root)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()
        srv.server_close()


def get(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310 - a local test server
        return json.loads(r.read())


def test_ac35_the_page_shows_waiting_conditions_and_excluded_samples(tmp_path: Path) -> None:
    # readings: the plan, busy before the first sample (a wait), then quiet except around ferry/large
    machine = FakeMachine(tmp_path, [QUIET, BUSY, QUIET, QUIET, QUIET, BUSY, QUIET])
    exp = CONDITIONS_EXPERIMENT.format(conditions="max_cpu_load = 0.5")
    cases = '[[case]]\nid = "keeper"\ntopic = "the keeper"\n[[case]]\nid = "ferry"\ntopic = "the ferryman"\n'
    p, _ = ready(tmp_path, exp, machine.sources(), cases)
    with serving(tmp_path) as base:
        _look_while_waiting(p, machine, base)


def _look_while_waiting(p: Any, machine: FakeMachine, base: str) -> None:
    proposed = get(base + "/api/experiments/E0001")
    assert proposed["plan"]["conditions"]["declared"]["max_cpu_load"] == 0.5  # shown before approval
    assert proposed["plan"]["conditions"]["now"]["would"] == "start"
    seen: dict[str, Any] = {}

    def look(_: FakeMachine) -> None:
        if not seen:
            seen["list"] = get(base + "/api/experiments")
            seen["detail"] = get(base + "/api/experiments/E0001")

    machine.on_poll.append(look)
    start(p, "E0001", sources=machine.sources())
    done = get(base + "/api/experiments/E0001")
    page = urllib.request.urlopen(base + "/", timeout=10).read().decode()  # noqa: S310 - a local test server
    row = seen["list"][0]
    assert row["status"] == "waiting"
    assert row["run"]["waiting"]["reasons"] == ["max_cpu_load: 0.90 of the CPU busy, limit 0.5"]
    assert seen["detail"]["status"] == "waiting"
    assert done["run"]["waits"][0]["outcome"] == "conditions met"
    samples = done["samples"]
    assert all(s["environment"]["checks"]["max_cpu_load"]["limit"] == 0.5 for s in samples)
    assert {s["environment_status"] for s in samples} == {"ok"}
    assert any(s["environment"]["attempt"] == 2 for s in samples)  # one sample was set aside and ran again
    for text in (
        "Run conditions",
        "Waiting for the run conditions",
        "not counted",
        "no samples in conditions",
        "outside-1.json",
        'rowClass: s => offConditions(s) ? "off"',
    ):
        assert text in page


def test_ac35_results_say_how_many_samples_were_not_counted(tmp_path: Path) -> None:
    machine = FakeMachine(tmp_path, [QUIET, QUIET, BUSY])  # both samples after the first one: outside
    exp = CONDITIONS_EXPERIMENT.format(conditions='max_cpu_load = 0.5\non_violation = "record_only"')
    p, _ = ready(tmp_path, exp, machine.sources())
    start(p, "E0001", sources=machine.sources())
    with serving(tmp_path) as base:
        x = get(base + "/api/experiments/E0001")
    marks = sorted(s["environment_status"] for s in x["samples"])
    assert marks == ["outside", "outside"]
    assert all(s["off_conditions"] == ["max_cpu_load"] for s in x["samples"])
    assert x["results"]["conditions"]["outside_or_unknown"] == 2
    assert x["results"]["conditions"]["reasons"] == {"max_cpu_load": 2}
