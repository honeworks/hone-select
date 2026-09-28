"""AC-21: every examples/*.py runs offline, opens with a What / How / Why docstring and is indexed."""

import ast
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

EXAMPLES_DIR = Path(__file__).parents[2] / "examples"
EXAMPLES = sorted(EXAMPLES_DIR.glob("*.py"))
ALL_PYTHON = sorted(EXAMPLES_DIR.rglob("*.py"))  # also the helper files in examples/cli and examples/command
PARTS = re.compile(r"^What:.+^How:.+^Why:.+", re.S | re.M)
ROW = re.compile(r"^\| \[`(\w+\.py)`\]\((\w+\.py)\) \|(.+)$", re.M)  # | [`x.py`](x.py) | concept | ... |
PUBLIC_MODULES = {
    "hone_select",
    "hone_select.testing",
    "hone_select.ports",
    "hone_select.cache",
    "hone_select.explain",
    "hone_select.adapters.openai",
    "hone_select.adapters.langchain",
}
EXTRAS = {"cli_run_and_explain.py": "typer", "hone_models_judge.py": "hone_models"}


def run(example: Path, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in env.items() if k != "PYTHONOPTIMIZE"}  # -O would strip the examples' asserts
    return subprocess.run(
        [sys.executable, str(example)], cwd=cwd, env=env, capture_output=True, text=True, check=False
    )


def test_ac21_there_is_an_example_per_design_concept() -> None:
    expected = {
        "quickstart.py", "gates_and_fallbacks.py", "aggregation_and_missing.py", "cascade_and_budget.py",
        "selection_policies.py", "prompt_scorers.py", "command_scorer.py", "dedup.py", "config_file.py",
        "scoring_existing.py", "records_and_explain.py", "bring_your_own_client.py", "lyrics_selection.py",
    }  # fmt: skip
    assert expected <= {p.name for p in EXAMPLES}


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_ac21_example_runs_and_reruns(example: Path, tmp_path: Path) -> None:
    """Twice with the same stores: an example must not depend on a fresh score cache or span store."""
    env = {**os.environ, "HONE_HOME": str(tmp_path / "hone-home")}
    for _ in range(2):
        done = run(example, tmp_path, env)
        assert done.returncode == 0, done.stderr
        if done.stdout.startswith("skipped:"):
            pytest.skip(done.stdout.strip())  # an extra is missing: shown as a skip, not a pass
        assert done.stdout.strip(), "an example prints what it shows"


@pytest.mark.parametrize(("name", "module"), sorted(EXTRAS.items()))
def test_ac21_examples_for_installed_extras_do_not_skip(name: str, module: str, tmp_path: Path) -> None:
    if importlib.util.find_spec(module) is None:
        pytest.skip(f"{module} is not installed")
    done = run(EXAMPLES_DIR / name, tmp_path, {**os.environ, "HONE_HOME": str(tmp_path)})
    assert done.returncode == 0, done.stderr
    assert "skipped:" not in done.stdout


def test_ac21_examples_leave_the_source_tree_clean(tmp_path: Path) -> None:
    """Without HONE_HOME, stores go to .hone/ in the current folder - never inside examples/."""
    env = {k: v for k, v in os.environ.items() if k != "HONE_HOME"}
    for example in EXAMPLES:
        assert run(example, tmp_path, env).returncode == 0, example.name
    assert not list(EXAMPLES_DIR.rglob(".hone")), "an example wrote a store into examples/"


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_ac21_example_explains_what_how_why_and_asserts(example: Path) -> None:
    tree = ast.parse(example.read_text())
    docstring = ast.get_docstring(tree)
    assert docstring, f"{example.name} must open with a docstring"
    assert PARTS.search(docstring), (
        f"{example.name}: the docstring needs What:, How: and Why: parts, in order"
    )
    asserts = [node for node in ast.walk(tree) if isinstance(node, ast.Assert)]
    assert len(asserts) >= 2, f"{example.name} must assert the key facts it shows"


def test_ac21_readme_indexes_every_example_once() -> None:
    rows = ROW.findall((EXAMPLES_DIR / "README.md").read_text())
    assert all(text == target for text, target, _ in rows), "link text and target must match"
    listed = [target for _, target, _ in rows]
    assert sorted(listed) == sorted(p.name for p in EXAMPLES)
    assert len(listed) == len(set(listed))
    design = (EXAMPLES_DIR.parent / "design" / "current.md").read_text()
    sections = set(re.findall(r"^#{2,3} (\d+(?:\.\d+)?)\.? ", design, re.M))
    for name, _, rest in rows:  # concept | one sentence | sections of design/current.md
        cells = [cell.strip() for cell in rest.strip().strip("|").split("|")]
        assert len(cells) == 3, name
        assert all(cells), name
        cited = re.findall(r"§(\d+(?:\.\d+)?)", cells[2])
        assert cited, name
        assert set(cited) <= sections, f"{name} cites sections missing from design/current.md: {cited}"


def test_ac21_readme_links_resolve() -> None:
    targets = re.findall(r"\]\(([\w/.]+)\)", (EXAMPLES_DIR / "README.md").read_text())
    missing = [t for t in targets if not (EXAMPLES_DIR / t).exists()]
    assert not missing


def imported_modules(tree: ast.Module) -> list[tuple[str, list[str]]]:
    """(module, imported names) for every hone_select import."""
    found: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("hone_select"):
            found.append((node.module or "", [alias.name for alias in node.names]))
        elif isinstance(node, ast.Import):
            found += [(a.name, []) for a in node.names if a.name.startswith("hone_select")]
    return found


@pytest.mark.parametrize("path", ALL_PYTHON, ids=lambda p: str(p.relative_to(EXAMPLES_DIR)))
def test_ac21_examples_use_only_the_public_api(path: Path) -> None:
    for module, names in imported_modules(ast.parse(path.read_text())):
        assert module in PUBLIC_MODULES, f"{path.name} imports {module}; use {sorted(PUBLIC_MODULES)}"
        assert not [n for n in names if n.startswith("_")], f"{path.name} imports private names {names}"


def test_ac21_no_example_shadows_a_stdlib_module() -> None:
    # `python examples/selectors.py` puts examples/ first on sys.path; the stdlib's `socket` does
    # `import selectors`, which would then load the example instead
    shadowing = [p.name for p in EXAMPLES if p.stem in sys.stdlib_module_names]
    assert not shadowing, f"rename these examples: {shadowing}"


def test_ac21_docs_link_to_existing_examples() -> None:
    docs = sorted((EXAMPLES_DIR.parent / "docs").glob("*.md"))
    linked = {name for doc in docs for name in re.findall(r"\(\.\./examples/(\w+\.py)\)", doc.read_text())}
    assert linked, "docs link to the examples"
    assert linked <= {p.name for p in EXAMPLES}
