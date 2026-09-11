"""Trend and drift analysis over historical portfolio snapshots."""

from __future__ import annotations

from argus.trends.analyze import analyze_product, analyze_product_series
from argus.trends.models import TrendFlag, TrendSummary

__all__ = [
    "TrendFlag",
    "TrendSummary",
    "analyze_product",
    "analyze_product_series",
]
