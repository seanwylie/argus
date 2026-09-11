#!/usr/bin/env python3
"""Thin entrypoint for ``python tools/import_product.py`` (see ``argus.importer.cli``).

Subcommands: ``status``, ``drift`` (read-only), ``replay`` (re-sync from import_state).
Initial import: ``--repo-url … --product-id …``. See ``docs/importer-operations.md``.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _run() -> int:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from argus.importer.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(_run())
