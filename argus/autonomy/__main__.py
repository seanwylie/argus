"""``python -m argus.autonomy`` — background scheduler entry."""

from __future__ import annotations

from argus.autonomy.runner import daemon_main

if __name__ == "__main__":
    raise SystemExit(daemon_main())
