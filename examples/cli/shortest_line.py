"""Registry module for the CLI example (see README):

hone-select run selection.toml --task task.json --registry shortest_line
"""

from hone_select import gate, generator, scorer


@generator()
def write(task, v):
    return f"{task['topic']} #{v['index']}" * (v["index"] + 1)


@gate()
def not_too_long(c):
    return len(c.data) < 80


@scorer(cost=1)
def shorter_is_better(c):
    return 1 - len(c.data) / 100
