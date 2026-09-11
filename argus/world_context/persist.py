"""Persist world context artifacts under ``runs/world_context/``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json

WORLD_CONTEXT_SCHEMA = "argus.world_context.v1"
WORLD_CONTEXT_INTERPRETATION_SCHEMA = "argus.world_context.interpretation.v1"
WORLD_CONTEXT_CREATION_CANDIDATES_SCHEMA = "argus.world_context.creation_candidates.v1"


def world_context_output_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "world_context"


def write_world_context_artifact(repo_root: Path, payload: dict[str, Any]) -> Path:
    """Write ``latest.json``; returns absolute path."""
    d = world_context_output_dir(repo_root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "latest.json"
    p.write_text(dumps_json(payload) + "\n", encoding="utf-8")
    return p.resolve()


def interpretation_output_dir(repo_root: Path) -> Path:
    return world_context_output_dir(repo_root) / "interpretation"


def write_interpretation_artifact(repo_root: Path, payload: dict[str, Any]) -> Path:
    """Write ``runs/world_context/interpretation/latest.json``; returns absolute path."""
    d = interpretation_output_dir(repo_root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "latest.json"
    p.write_text(dumps_json(payload) + "\n", encoding="utf-8")
    return p.resolve()


def creation_candidates_output_dir(repo_root: Path) -> Path:
    return world_context_output_dir(repo_root) / "creation_candidates"


def write_creation_candidates_artifact(repo_root: Path, payload: dict[str, Any]) -> Path:
    """Write ``runs/world_context/creation_candidates/latest.json``; returns absolute path."""
    d = creation_candidates_output_dir(repo_root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "latest.json"
    p.write_text(dumps_json(payload) + "\n", encoding="utf-8")
    return p.resolve()
