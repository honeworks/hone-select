"""Experiments (design change 0009): declare a comparison of setups over test cases, plan it, have a person
approve it, run it, and read which setup, model, parameter or prompt is best.

    from hone_select.experiments import Project, start
    project = Project(".")
    folder = project.new("Open-weight writing")      # edit experiments/E0001-.../experiment.toml
    project.plan("E0001")                             # status: proposed
    project.review("E0001", "approved", note="go")    # or the dashboard's Approve button
    start(project, "E0001")                           # outputs/, selections, results/
"""

from hone_select.experiments.project import Project
from hone_select.experiments.results import report
from hone_select.experiments.runner import start, stop
from hone_select.experiments.subjects import Ctx, TransientError

__all__ = ["Ctx", "Project", "TransientError", "report", "start", "stop"]
