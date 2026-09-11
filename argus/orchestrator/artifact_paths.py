"""Path constructors for orchestration artifacts and shared ``runs/`` layout (path construction only)."""

from __future__ import annotations

from pathlib import Path


def orchestration_latest_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "orchestration" / "latest"


def orchestration_latest_path(repo_root: Path, product_id: str) -> Path:
    """Per-product orchestration state JSON under ``runs/orchestration/latest/``."""
    return orchestration_latest_dir(repo_root) / f"{product_id}.json"


def orchestration_index_path(repo_root: Path) -> Path:
    return orchestration_latest_dir(repo_root) / "index.json"


def orchestration_batch_advancement_path(repo_root: Path) -> Path:
    return orchestration_latest_dir(repo_root) / "batch_advancement.json"


def orchestration_operator_summary_path(repo_root: Path) -> Path:
    return orchestration_latest_dir(repo_root) / "operator_summary.json"


def orchestration_fleet_governance_rollup_path(repo_root: Path) -> Path:
    return orchestration_latest_dir(repo_root) / "fleet_governance_rollup.json"


def portfolio_priorities_path(repo_root: Path) -> Path:
    """Cross-product portfolio prioritization (``argus.portfolio_priorities.v1``)."""
    return orchestration_latest_dir(repo_root) / "portfolio_priorities.json"


def orchestration_generations_dir(repo_root: Path) -> Path:
    """Timestamped orchestration artifacts (e.g. portfolio priority snapshots)."""
    return Path(repo_root).resolve() / "runs" / "orchestration" / "generations"


def portfolio_priority_trends_path(repo_root: Path) -> Path:
    """Derived portfolio priority trend summary (``argus.portfolio_priority_trends.v1``)."""
    return orchestration_latest_dir(repo_root) / "portfolio_priority_trends.json"


def orchestration_advancement_path(repo_root: Path, product_id: str) -> Path:
    return orchestration_latest_dir(repo_root) / "advancements" / f"{product_id}.json"


def orchestration_progression_latest_path(repo_root: Path, product_id: str) -> Path:
    return orchestration_latest_dir(repo_root) / "progression_runs" / f"{product_id}.json"


def orchestration_progression_generation_path(repo_root: Path, run_id: str) -> Path:
    return Path(repo_root).resolve() / "runs" / "orchestration" / "progression_runs" / "generations" / f"{run_id}.json"


# --- Shared ``runs/`` paths (no ``repo_root.resolve()``; matches historical eligibility composition) ---


def ideas_bundle_latest_path(repo_root: Path) -> Path:
    return Path(repo_root) / "runs" / "ideas" / "latest.json"


def experiments_proposals_latest_product_path(repo_root: Path, product_id: str) -> Path:
    return Path(repo_root) / "runs" / "experiments" / "proposals" / "latest" / f"{product_id}.json"


def experiments_prioritization_latest_product_path(repo_root: Path, product_id: str) -> Path:
    return Path(repo_root) / "runs" / "experiments" / "prioritization" / "latest" / f"{product_id}.json"


def runs_escalations_latest_dir(repo_root: Path) -> Path:
    return Path(repo_root) / "runs" / "escalations" / "latest"


def execution_product_dir(repo_root: Path, product_id: str) -> Path:
    return Path(repo_root) / "runs" / "execution" / product_id
