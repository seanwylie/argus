"""
Deterministic listing of timestamped portfolio JSON artifacts (``YYYYMMDDTHHMMSSZ.json``).

Shared by portfolio evaluators and :mod:`argus.portfolio.history` so directory walks stay consistent.
"""

from __future__ import annotations

import re
from pathlib import Path

PORTFOLIO_TS_JSON_PATTERN = re.compile(r"^\d{8}T\d{6}Z\.json$")


def list_timestamped_portfolio_json_files(directory: Path) -> list[Path]:
    """
    Return stamped JSON paths under ``directory``, newest first (lexicographic on filename).
    """
    d = Path(directory)
    if not d.is_dir():
        return []
    files = [p for p in d.iterdir() if p.is_file() and PORTFOLIO_TS_JSON_PATTERN.match(p.name)]
    return sorted(files, key=lambda p: p.name, reverse=True)


def stamp_run_id_from_path(path: Path) -> str:
    """Return the run id (filename stem) for a stamped portfolio JSON path."""
    return Path(path).stem
