"""Nationwide network design, assignment, routing evaluation and guided search."""
from .assignment import solve_assignment
from .context import NetworkContext
from .evaluation import NetworkEvaluator
from .pipeline import SearchSettings, run_pipeline

__all__ = ["NetworkContext", "NetworkEvaluator", "SearchSettings", "run_pipeline", "solve_assignment", "run_upper"]


def run_upper(log=print, *, context=None, output_dir=None, settings=None, k_values=None, **input_options):
    from project import ROOT
    context = NetworkContext.from_project(**input_options) if context is None else context
    output_dir = ROOT / "code" / "results" / "higher_level" if output_dir is None else output_dir
    return run_pipeline(context, output_dir, settings, k_values=k_values, log=log)
