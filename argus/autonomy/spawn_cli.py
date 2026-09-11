"""CLI handler for ``argus autonomy spawn``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.autonomy.spawn import (
    apply_spawn_proposal,
    build_spawn_proposal,
    load_latest_proposal,
    quota_remaining,
    write_proposal_file,
)
from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json, to_jsonable


def _products_dir(repo: Path, override: Path | None) -> Path | None:
    if override is None:
        return None
    return override.resolve()


def run_spawn_command(args: Any) -> int:
    r = repo_root()
    pdir = _products_dir(r, getattr(args, "products_dir", None))

    max_n = int(getattr(args, "max_per_period", 3) or 3)
    period_d = int(getattr(args, "period_days", 30) or 30)

    if getattr(args, "apply", False):
        if not getattr(args, "approve_spawn", False):
            print(
                "Refusing --apply without --approve-spawn (human confirmation of the proposal).",
                file=sys.stderr,
            )
            return 1

        remaining, qnote = quota_remaining(r, max_per_period=max_n, period_days=period_d)
        if remaining <= 0:
            print(
                f"Spawn quota exhausted ({qnote}). Increase limits or wait for the window to roll.",
                file=sys.stderr,
            )
            return 1

        proposal = load_latest_proposal(r)
        if proposal is None or not proposal.proposed_slug:
            proposal = build_spawn_proposal(r, products_dir=pdir)
            write_proposal_file(r, proposal)
            print(
                "No latest_proposal.json — rebuilt proposal from current signals.",
                file=sys.stderr,
            )

        proposal.quota_note = qnote
        code, payload = apply_spawn_proposal(r, proposal, products_dir=pdir)
        if getattr(args, "json", False):
            print(dumps_json(to_jsonable({"quota": qnote, **payload})))
        else:
            if code != 0:
                print(payload.get("message", "apply failed"), file=sys.stderr)
            else:
                print(f"Spawned product: {payload.get('product_id')}")
                print(f"Root: {payload.get('product_root')}")
                print(qnote)
        return code

    proposal = build_spawn_proposal(r, products_dir=pdir)
    remaining, qnote = quota_remaining(r, max_per_period=max_n, period_days=period_d)
    proposal.quota_note = qnote
    path = write_proposal_file(r, proposal)
    out = {
        "proposal_path": str(path),
        "quota_remaining": remaining,
        "quota_note": qnote,
        "proposed_slug": proposal.proposed_slug,
        "template_type": proposal.template_type,
        "thesis": proposal.thesis,
        "fingerprint": proposal.fingerprint,
        "requires_approval_to_apply": True,
        "next_step": "argus autonomy spawn --apply --approve-spawn",
    }
    if getattr(args, "json", False):
        print(dumps_json(to_jsonable({**out, "signals": proposal.signals})))
    else:
        print("Argus spawn proposal (no product created yet)")
        print("=" * 44)
        print(f"Proposed id: {proposal.proposed_slug}  (template: {proposal.template_type})")
        print(f"Fingerprint: {proposal.fingerprint}")
        print(f"Quota: {qnote} ({remaining} remaining this period)")
        print("")
        for t in proposal.thesis:
            print(f"  • {t}")
        print("")
        print(f"Wrote {path.relative_to(r)}")
        print("")
        print("Next: argus autonomy spawn --apply --approve-spawn")
    return 0
