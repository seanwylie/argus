"""CLI: inspect routed council profiles (refinement owns execution)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.council.models import CouncilMode, profile_to_jsonable
from argus.council.profiles import default_council_profile
from argus.council.routing import validate_council_profile
from argus.refinement.models import ArtifactType


def _atype(s: str) -> ArtifactType:
    v = str(s).strip().lower().replace("-", "_")
    return ArtifactType(v)


def run_council_command(args: Any, _repo_root: Path) -> int:
    cmd = getattr(args, "council_command", None)
    if cmd is None:
        print("council: missing subcommand", file=sys.stderr)
        return 2

    if cmd == "profiles":
        rows = []
        for at in (ArtifactType.IDEA, ArtifactType.PRODUCT_SPEC, ArtifactType.IMPLEMENTATION_PLAN):
            cp = default_council_profile(at)
            errs = validate_council_profile(cp)
            rows.append(
                {
                    "artifact_type": at.value,
                    "grounded_count": cp.grounded_count(),
                    "outsider_count": cp.outsider_count(),
                    "member_count": len(cp.members),
                    "valid": len(errs) == 0,
                    "errors": errs,
                }
            )
        if getattr(args, "json", False):
            print(dumps_json({"schema": "argus.council_profiles_list.v1", "profiles": rows}))
        else:
            for row in rows:
                print(
                    f"{row['artifact_type']}: grounded={row['grounded_count']} "
                    f"outsider={row['outsider_count']} members={row['member_count']}"
                )
        return 0

    if cmd == "show":
        at_raw = getattr(args, "artifact_type", "")
        try:
            cp = default_council_profile(_atype(at_raw))
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        payload = {
            "schema": cp.schema,
            "artifact_type": cp.artifact_type.value,
            "phase": cp.phase,
            "max_rounds": cp.max_rounds,
            "members": [profile_to_jsonable(m) for m in cp.members],
            "outsider_influence": {
                "outsider_can_hard_approve": cp.outsider_influence.outsider_can_hard_approve,
                "outsider_can_overrule_grounded_fail": cp.outsider_influence.outsider_can_overrule_grounded_fail,
                "human_review_if_outsider_blocking_count": cp.outsider_influence.human_review_if_outsider_blocking_count,
            },
        }
        if getattr(args, "json", False):
            print(dumps_json(payload))
        else:
            print(f"artifact_type: {cp.artifact_type.value}")
            print(f"grounded: {cp.grounded_count()}  outsider: {cp.outsider_count()}")
            for m in cp.members:
                mode = "G" if m.council_mode == CouncilMode.GROUNDED else "O"
                print(f"  [{mode}] {m.stakeholder_type.value:18} w={m.weight} req={m.required} ctx={m.context_policy.value} be={m.backend_type.value}")
        return 0

    if cmd == "run":
        try:
            at = ArtifactType(getattr(args, "artifact_type"))
        except ValueError:
            print("invalid --type", file=sys.stderr)
            return 2
        cp = default_council_profile(at)
        src = getattr(args, "source_id", "dry_run")
        out = {
            "schema": "argus.council_run_dry.v1",
            "source_id": src,
            "artifact_type": at.value,
            "note": "Refinement rounds are executed via `argus refine start` / `refine run`; this is inspection only.",
            "profile": {
                "grounded_count": cp.grounded_count(),
                "outsider_count": cp.outsider_count(),
                "members": [profile_to_jsonable(m) for m in cp.members],
            },
        }
        if getattr(args, "json", False):
            print(dumps_json(out))
        else:
            print(out["note"])
            print(f"artifact_type={at.value} source_id={src!r}")
            print(f"grounded={cp.grounded_count()} outsider={cp.outsider_count()} total={len(cp.members)}")
        return 0

    print("Unknown council command.", file=sys.stderr)
    return 2
