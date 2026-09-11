"""Map snapshot filenames to ingest functions (order: more specific keys first)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from argus.core.models.signal import SignalRecord
from argus.signals.snapshots.aws_cost import ADAPTER_ID as AWS_ADAPTER
from argus.signals.snapshots.aws_cost import ingest_aws_cost_snapshot
from argus.signals.snapshots.content_platform import ADAPTER_ID as CP_ADAPTER
from argus.signals.snapshots.content_platform import ingest_content_platform_snapshot
from argus.signals.snapshots.google_analytics import ADAPTER_ID as GA_ADAPTER
from argus.signals.snapshots.google_analytics import ingest_google_analytics_snapshot
from argus.signals.snapshots.local_generic import ADAPTER_ID as LS_ADAPTER
from argus.signals.snapshots.mobile_app import ADAPTER_ID as MOB_ADAPTER
from argus.signals.snapshots.mobile_app import ingest_mobile_app_snapshot
from argus.signals.snapshots.posthog import ADAPTER_ID as PH_ADAPTER
from argus.signals.snapshots.posthog import ingest_posthog_snapshot
from argus.signals.snapshots.stripe_revenue import ADAPTER_ID as ST_ADAPTER
from argus.signals.snapshots.stripe_revenue import ingest_stripe_revenue_snapshot
from argus.signals.snapshots.temporal_market import ADAPTER_ID as TM_ADAPTER
from argus.signals.snapshots.temporal_market import ingest_temporal_market_snapshot
from argus.signals.snapshots.temporal_news import ADAPTER_ID as TN_ADAPTER
from argus.signals.snapshots.temporal_news import ingest_temporal_news_snapshot
from argus.signals.snapshots.temporal_recency import ADAPTER_ID as TR_ADAPTER
from argus.signals.snapshots.temporal_recency import ingest_temporal_recency_snapshot

# (substring in filename, ingest callable). First match wins.
_SNAPSHOT_DISPATCH: list[tuple[str, Callable[[Path, Path], list[SignalRecord]]]] = [
    ("content_platform", ingest_content_platform_snapshot),
    ("channel_metrics", ingest_content_platform_snapshot),
    ("youtube", ingest_content_platform_snapshot),
    ("substack", ingest_content_platform_snapshot),
    ("temporal_market", ingest_temporal_market_snapshot),
    ("temporal_news", ingest_temporal_news_snapshot),
    ("temporal_recency", ingest_temporal_recency_snapshot),
    ("google_analytics", ingest_google_analytics_snapshot),
    ("ga4", ingest_google_analytics_snapshot),
    ("posthog", ingest_posthog_snapshot),
    ("aws_cost", ingest_aws_cost_snapshot),
    ("aws-cost", ingest_aws_cost_snapshot),
    ("stripe", ingest_stripe_revenue_snapshot),
    ("mobile", ingest_mobile_app_snapshot),
    ("app_metrics", ingest_mobile_app_snapshot),
]


def match_snapshot_parser(filename: str) -> Callable[[Path, Path], list[SignalRecord]] | None:
    n = filename.lower()
    for key, fn in _SNAPSHOT_DISPATCH:
        if key in n:
            return fn
    return None


def snapshot_type_catalog() -> list[dict[str, str]]:
    """Stable rows for CLI ``snapshot-types``."""
    return [
        {
            "adapter_id": LS_ADAPTER,
            "filename_contains": "(metrics/snapshots/local/ or runs/signals/snapshots/products/<id>/)",
            "formats": "json, csv, manifest",
            "signal_type": "custom",
        },
        {
            "adapter_id": TM_ADAPTER,
            "filename_contains": "temporal_market",
            "formats": "json, csv",
            "signal_type": "temporal",
        },
        {
            "adapter_id": TN_ADAPTER,
            "filename_contains": "temporal_news",
            "formats": "json, csv",
            "signal_type": "temporal",
        },
        {
            "adapter_id": TR_ADAPTER,
            "filename_contains": "temporal_recency",
            "formats": "json, csv",
            "signal_type": "temporal",
        },
        {
            "adapter_id": PH_ADAPTER,
            "filename_contains": "posthog",
            "formats": "json, csv",
            "signal_type": "custom",
        },
        {
            "adapter_id": GA_ADAPTER,
            "filename_contains": "google_analytics or ga4",
            "formats": "json, csv",
            "signal_type": "custom",
        },
        {
            "adapter_id": AWS_ADAPTER,
            "filename_contains": "aws_cost or aws-cost",
            "formats": "json, csv",
            "signal_type": "custom",
        },
        {
            "adapter_id": ST_ADAPTER,
            "filename_contains": "stripe",
            "formats": "json, csv",
            "signal_type": "custom",
        },
        {
            "adapter_id": MOB_ADAPTER,
            "filename_contains": "mobile or app_metrics",
            "formats": "json, csv",
            "signal_type": "custom",
        },
        {
            "adapter_id": CP_ADAPTER,
            "filename_contains": "content_platform, channel_metrics, youtube, or substack",
            "formats": "json, csv",
            "signal_type": "custom",
        },
    ]
