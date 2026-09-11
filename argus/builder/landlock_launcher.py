"""
``python -m argus.builder.landlock_launcher`` — apply Landlock then ``exec`` the agent.

Environment (set by :mod:`argus.builder.sandbox` inside bubblewrap):

- ``ARGUS_BUILDER_LANDLOCK_WRITE_PATHS`` — colon-separated absolute directories
- ``ARGUS_BUILDER_LANDLOCK_STATUS_FILE`` — JSON status path (written before ``exec``)
"""

from __future__ import annotations

import json
import os
import sys

from argus.builder.landlock_support import apply_landlock_write_allowlist, landlock_sysctl_enabled


def _write_status(path: str, payload: dict) -> None:
    try:
        p = os.path.abspath(path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.write("\n")
    except OSError:
        pass


def main() -> None:
    argv = sys.argv[1:]
    if "--" not in argv:
        print("landlock_launcher: missing -- separator", file=sys.stderr)
        sys.exit(127)
    i = argv.index("--")
    agent_argv = argv[i + 1 :]
    if not agent_argv:
        print("landlock_launcher: no argv after --", file=sys.stderr)
        sys.exit(127)

    status_path = os.environ.get("ARGUS_BUILDER_LANDLOCK_STATUS_FILE", "").strip()
    raw_paths = os.environ.get("ARGUS_BUILDER_LANDLOCK_WRITE_PATHS", "")
    paths = [p for p in raw_paths.split(":") if p.strip()]

    applied = False
    reason: str | None = "not_attempted"
    abi_ver: int | None = None

    if status_path:
        _write_status(status_path, {"landlock_applied": False, "reason": "pending"})

    if paths and landlock_sysctl_enabled():
        applied, reason = apply_landlock_write_allowlist(paths)
        if applied:
            reason = None
    else:
        if not paths:
            reason = "no_write_paths_in_env"
        elif not landlock_sysctl_enabled():
            reason = "landlock_disabled_in_kernel"

    if status_path:
        payload = {
            "landlock_applied": applied,
            "reason": reason,
        }
        if abi_ver is not None:
            payload["abi_version"] = abi_ver
        _write_status(status_path, payload)

    os.execvp(agent_argv[0], agent_argv)


if __name__ == "__main__":
    main()
