"""
Controlled transition into supervised autonomy (activation gate).
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from argus.autonomy.models import AutonomyMode
from argus.autonomy.operator_policy import save_autonomy_config
from argus.core.serialize import dumps_json
from argus.validation.validate import validate_repo_artifacts


@dataclass
class ActivationResult:
    ok: bool
    reasons: list[str]
    autonomy_mode: str | None = None


def _append_activation_log(repo_root: Path, payload: dict[str, Any]) -> None:
    p = repo_root.resolve() / "runs" / "autonomy" / "activation.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    line = dumps_json({**payload, "timestamp_utc": datetime.now(timezone.utc).isoformat()}, indent=None)
    with p.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_activation_gate(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
) -> ActivationResult:
    """
    Preconditions: doctor clean of errors, artifact validation passes, autonomy policy readable,
    no critical autonomy warnings (high block streak).
    """
    from argus.cli.doctor_cmd import cmd_doctor

    root = repo_root.resolve()
    reasons: list[str] = []

    doc_args = SimpleNamespace(products_dir=products_dir, json=True, strict=False)

    buf = io.StringIO()
    with redirect_stdout(buf):
        doc_code = cmd_doctor(root, doc_args)
    try:
        report = json.loads(buf.getvalue())
    except json.JSONDecodeError:
        report = {}
    if report.get("errors"):
        reasons.append(f"doctor errors: {report['errors'][:3]}")
    elif doc_code != 0:
        reasons.append("doctor exited non-zero")

    # Re-parse doctor JSON by running inventory errors only - doctor already printed;
    # we validate artifacts deeply here:
    rep = validate_repo_artifacts(root)
    if not rep.ok:
        reasons.append(f"artifact validation: {len(rep.issues)} issue(s)")

    # Autonomy policy readable
    try:
        from argus.autonomy.operator_policy import effective_policy

        mode, pol, _tier = effective_policy(root)
        _ = pol.max_actions_per_run
    except Exception as e:
        reasons.append(f"autonomy policy unreadable: {e}")

    # Critical warnings: block streak
    try:
        st_path = root / "runs" / "autonomy" / "state.json"
        if st_path.is_file():
            st = json.loads(st_path.read_text(encoding="utf-8"))
            if isinstance(st, dict):
                bs = int(float(st.get("block_streak", 0)))
                if bs >= 8:
                    reasons.append(f"critical: autonomy block_streak={bs} (resolve blocks before activation)")
    except (OSError, ValueError, TypeError):
        pass

    if reasons:
        _append_activation_log(
            root,
            {"event": "activation_blocked", "reasons": reasons},
        )
        return ActivationResult(ok=False, reasons=reasons)

    save_autonomy_config(root, AutonomyMode.SUPERVISED, tier=2)
    _append_activation_log(
        root,
        {"event": "activation_ok", "mode": AutonomyMode.SUPERVISED.value},
    )
    return ActivationResult(ok=True, reasons=[], autonomy_mode=AutonomyMode.SUPERVISED.value)
