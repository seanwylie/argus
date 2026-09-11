#!/usr/bin/env python3
"""Run ``config/importer_regression.yaml`` and write ``runs/importer_regression/<timestamp>.md``."""

from __future__ import annotations

import sys
from pathlib import Path


def _run() -> int:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from argus.importer.regression_runner import main as regression_main

    return regression_main()


if __name__ == "__main__":
    raise SystemExit(_run())
