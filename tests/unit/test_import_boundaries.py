"""The core imports no optional extra and no other honeworks package (architecture principle 1)."""

import subprocess
import sys

BLOCKED = ["openai", "langchain_core", "typer", "rich", "httpx", "hone_models", "hone_flow", "hone_lens"]


def test_core_imports_without_extras() -> None:
    code = f"""
import sys
for name in {BLOCKED!r}:
    sys.modules[name] = None  # any import of these now fails
import hone_select, hone_select.testing, hone_select.ports
from hone_select import Engine, generator, scorer
@generator()
def g(task, v):
    return task
@scorer()
def s(c):
    return 0.5
engine = Engine("[score]\\ncascade = [{{ scorers = ['s'] }}]\\n[record]\\nsink = 'none'", registry=[g, s], cache=None)
assert engine.run("x").winner is not None
"""
    subprocess.run([sys.executable, "-c", code], check=True)
