"""Manual / file-based signal ingestion (JSON array)."""

from __future__ import annotations

from pathlib import Path

from argus.world_context.service import ingest_signals_from_json_file


def load_signals_from_json_path(path: Path) -> list[dict]:
    """
    Adapter entrypoint: read operator-prepared JSON from disk.

    Same as :func:`argus.world_context.service.ingest_signals_from_json_file` — kept as a named
    adapter for future registry wiring.
    """
    return ingest_signals_from_json_file(path)
