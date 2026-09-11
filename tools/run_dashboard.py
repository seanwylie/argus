#!/usr/bin/env python3
"""Thin launcher for the Streamlit operator console (sets repo root, forwards argv)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    os.environ.setdefault("ARGUS_REPO_ROOT", str(root))
    os.chdir(root)
    port = os.environ.get("ARGUS_DASHBOARD_PORT", "8501")
    cmd = [
        "uv",
        "run",
        "--group",
        "dashboard",
        "streamlit",
        "run",
        "argus/dashboard/app.py",
        "--server.port",
        port,
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        *sys.argv[1:],
    ]
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
