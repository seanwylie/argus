"""CLI handlers for ``argus history``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json, to_jsonable
from argus.history.snapshot import (
    build_portfolio_snapshot,
    compute_snapshot_delta,
    new_snapshot_id_and_time,
)
from argus.history.storage import load_snapshot_file, resolve_snapshot_ref, write_snapshot_json
from argus.history.summarize import format_product_history_text


def _products_dir(repo: Path, override: Path | None) -> Path | None:
    if override is None:
        return None
    return override.resolve()


def run_history_command(args: Any) -> int:
    r = repo_root()
    sub = args.history_command
    pdir = _products_dir(r, getattr(args, "products_dir", None))

    if sub == "snapshot":
        sid, iso = new_snapshot_id_and_time(label=getattr(args, "label", None))
        snap = build_portfolio_snapshot(
            r,
            snapshot_id=sid,
            observed_at_utc=iso,
            label=args.label,
            products_dir=pdir,
        )
        extra = getattr(args, "out", None)
        extra_path = Path(extra) if extra else None
        path = write_snapshot_json(r, snap, extra_copy_path=extra_path)
        if args.json:
            print(
                dumps_json(
                    {
                        "snapshot_id": snap.snapshot_id,
                        "observed_at_utc": snap.observed_at_utc,
                        "written_path": str(path),
                        "relative_to_repo": path.relative_to(r.resolve()).as_posix(),
                        "product_count": len(snap.products),
                    }
                )
            )
        else:
            print(f"Wrote snapshot {snap.snapshot_id}")
            print(f"  {path.relative_to(r.resolve()).as_posix()}")
            print(f"  products: {len(snap.products)}")
            if extra_path:
                print(f"  copy: {extra_path}")
        return 0

    if sub == "diff":
        try:
            pa = resolve_snapshot_ref(r, args.snapshot_a)
            pb = resolve_snapshot_ref(r, args.snapshot_b)
        except (OSError, ValueError) as e:
            print(str(e), file=sys.stderr)
            return 1
        older = load_snapshot_file(pa)
        newer = load_snapshot_file(pb)
        # Interpret paths so older is earlier in time when possible
        if older.observed_at_utc > newer.observed_at_utc:
            older, newer = newer, older
        delta = compute_snapshot_delta(older, newer)
        if args.json:
            print(dumps_json(to_jsonable(delta)))
        else:
            print(
                f"Delta {delta.from_snapshot_id} → {delta.to_snapshot_id}\n"
                f"  {delta.from_observed_at_utc} → {delta.to_observed_at_utc}\n"
            )
            for d in delta.product_deltas:
                bits: list[str] = []
                if d.active_findings_count_delta:
                    bits.append(f"findings {d.active_findings_count_delta:+d}")
                if d.monthly_cost_usd_delta is not None and d.monthly_cost_usd_delta != 0:
                    bits.append(f"cost Δ ${d.monthly_cost_usd_delta:.2f}")
                if d.lifecycle_stage_changed:
                    bits.append(f"stage {d.previous_lifecycle_stage!r} → {d.current_lifecycle_stage!r}")
                if d.top_recommended_action_changed:
                    bits.append("top action changed")
                if d.escalation_count_delta:
                    bits.append(f"escalations {d.escalation_count_delta:+d}")
                if d.kill_candidate_changed:
                    bits.append(
                        f"kill_candidate {d.previous_kill_candidate} → {d.current_kill_candidate}"
                    )
                if d.top_confidence_delta is not None and d.top_confidence_delta != 0:
                    bits.append(f"confidence Δ {d.top_confidence_delta:+.3f}")
                if d.last_signal_changed:
                    bits.append("last_signal_at changed")
                if not bits:
                    bits.append("(no tracked changes)")
                print(f"  {d.product_id}: {', '.join(bits)}")
        return 0

    if sub == "product":
        text = format_product_history_text(r, args.product_id)
        if args.json:
            from argus.history.summarize import load_product_timeline

            tl = load_product_timeline(r, args.product_id)
            print(
                dumps_json(
                    {
                        "product_id": args.product_id,
                        "entries": [
                            {
                                "snapshot_id": sid,
                                "observed_at_utc": obs,
                                "row": to_jsonable(p),
                            }
                            for sid, obs, p in tl
                        ],
                    }
                )
            )
        else:
            print(text, end="")
        return 0

    print(f"Unknown history subcommand: {sub}", file=sys.stderr)
    return 2
