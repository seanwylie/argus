"""
Expected keys for local Argus artifacts (lightweight structural checks).

Full JSON Schema is optional; validators use these sets for required fields.
"""

from __future__ import annotations

# --- execution run.json ---
EXECUTION_RUN_REQUIRED = frozenset(
    {
        "schema",
        "run_id",
        "action_id",
        "product_id",
        "command",
        "working_directory",
        "started_at",
        "status",
    }
)

# --- findings latest per-product ---
FINDINGS_LATEST_REQUIRED = frozenset({"schema", "product_id", "findings"})

# --- decisions latest per-product ---
DECISIONS_PRODUCT_REQUIRED = frozenset({"schema", "product_id", "lifecycle", "candidates"})

# --- portfolio aggregate ---
DECISIONS_PORTFOLIO_REQUIRED = frozenset({"schema", "ranked"})

# --- experiments ---
EXPERIMENT_REQUIRED = frozenset(
    {"schema", "id", "product_id", "hypothesis", "status", "type", "confidence"}
)

# --- history snapshot ---
SNAPSHOT_REQUIRED = frozenset({"schema", "snapshot_id", "observed_at_utc", "products"})

# --- trends latest ---
TRENDS_LATEST_REQUIRED = frozenset({"schema", "generated_at_utc", "summaries"})

# --- signals latest per-product (argus.signal_collection.v1 bundle from persistence) ---
SIGNALS_LATEST_REQUIRED = frozenset(
    {"schema", "product_id", "collected_at_utc", "records"}
)

# --- temporal latest per-product (argus.temporal_bundle.v1 from temporal/persistence) ---
# Note: ``worst_freshness_status`` is written by current writers but may be absent on older bundles.
TEMPORAL_LATEST_REQUIRED = frozenset(
    {
        "schema",
        "product_id",
        "repo_root",
        "collected_at_utc",
        "source_signal_schema",
        "record_count",
        "signals",
    }
)

CANONICAL_SIGNAL_SCHEMA = "argus.canonical_signal.v1"
TEMPORAL_BUNDLE_SCHEMA = "argus.temporal_bundle.v1"
TEMPORAL_SIGNAL_ROW_SCHEMA = "argus.temporal_signal.v1"

# --- ideas latest (IdeasBundle / runs/ideas/latest.json) ---
IDEAS_BUNDLE_REQUIRED = frozenset({"schema", "generated_at_utc", "ideas"})

# --- autonomy ---
AUTONOMY_CONFIG_REQUIRED = frozenset({"schema", "mode"})
AUTONOMY_STATE_OPTIONAL_KEYS = frozenset(
    {
        "utc_day",
        "actions_executed_today",
        "cost_usd_today",
        "block_streak",
        "capability_pauses",
        "executable_queue",
    }
)
