"""Doctor checks for refinement session integrity."""

from __future__ import annotations

from pathlib import Path

from argus.refinement.persistence import (
    list_session_entries,
    read_json,
    session_dir,
    valid_session_id,
)
from argus.refinement.round_files import scan_round_chain


def check_refinement_health(repo: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    root = repo.resolve()
    ref = root / "runs" / "refinement"
    if not ref.is_dir():
        return errors, warnings

    for e in list_session_entries(root):
        sid = str(e.get("session_id", ""))
        if not valid_session_id(sid):
            errors.append(f"refinement index: invalid session_id shape {sid!r}")
            continue
        sd = session_dir(root, sid)
        if not sd.is_dir():
            errors.append(f"refinement: index lists {sid} but directory missing")
            continue
        sess_p = sd / "session.json"
        if not sess_p.is_file():
            errors.append(f"refinement/{sid}: missing session.json")
            continue
        raw = read_json(sess_p)
        if not raw:
            errors.append(f"refinement/{sid}/session.json: invalid JSON")
            continue
        st = str(raw.get("status", ""))
        r = int(raw.get("current_round", 0))
        mx = int(raw.get("max_rounds", 4))
        if r > mx + 2:
            warnings.append(f"refinement/{sid}: current_round {r} >> max_rounds {mx} (suspicious)")

        # Orphaned / inconsistent round files (drafts → reviews → synthesis → convergence)
        r_err, r_warn = scan_round_chain(sd)
        for msg in r_err:
            errors.append(f"refinement/{sid}: {msg}")
        for msg in r_warn:
            warnings.append(f"refinement/{sid}: {msg}")

        # Terminal without convergence outcome pointer
        if st in ("approved", "approved_with_risks", "rejected", "human_review_required"):
            out = sd / "outcomes" / "latest.json"
            if not out.is_file():
                warnings.append(f"refinement/{sid}: terminal status but missing outcomes/latest.json")

    return errors, warnings
