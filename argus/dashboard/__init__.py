"""Local portfolio dashboard (static HTML from repo artifacts)."""

from argus.dashboard.data import build_dashboard_payload
from argus.dashboard.operator_summary import evaluate_operator_summary, run_operator_summary
from argus.dashboard.render import write_dashboard_html

__all__ = [
    "build_dashboard_payload",
    "evaluate_operator_summary",
    "run_operator_summary",
    "write_dashboard_html",
]
