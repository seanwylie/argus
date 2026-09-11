"""CLI: ``argus refine`` — artifact refinement sessions."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json, to_jsonable
from argus.refinement.models import ArtifactType
from argus.refinement.persistence import list_session_entries, read_json
from argus.refinement.queries import draft_path
from argus.refinement.session import (
    approve_session,
    create_session,
    load_session,
    retry_session,
    run_refinement_cycle,
)


def run_refine_command(args: Any, repo_root: Path) -> int:
    r = repo_root.resolve()
    cmd = getattr(args, "refine_command", None)

    if cmd == "start":
        at = ArtifactType(str(getattr(args, "artifact_type", "idea")))
        src = str(getattr(args, "source_id", "") or getattr(args, "source", ""))
        if not src:
            print("--source is required", file=sys.stderr)
            return 2
        pid = getattr(args, "product_id", None) or None
        mx = int(getattr(args, "max_rounds", 4) or 4)
        sess = create_session(r, at, src, product_id=pid, max_rounds=mx)
        if getattr(args, "json", False):
            print(dumps_json(to_jsonable(sess)))
        else:
            print(f"Created session {sess.session_id} (type={at.value}, source={src})")
        return 0

    if cmd == "run":
        sid = str(getattr(args, "session_id", "") or "").strip()
        if not sid:
            print(
                "session_id required (positional SESSION_ID). "
                "Example: argus refine run ref_20260101T000000Z_a1b2c3d4 --json",
                file=sys.stderr,
            )
            return 2
        try:
            sess, conv = run_refinement_cycle(r, sid)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        if getattr(args, "json", False):
            print(dumps_json({"session": to_jsonable(sess), "convergence": to_jsonable(conv)}))
        else:
            print(f"session={sess.session_id} status={sess.status.value} round={sess.current_round}")
            print(f"converged={conv.converged} final={conv.final_status.value} pass_ratio={conv.pass_ratio}")
        return 0

    if cmd == "show":
        sid = str(getattr(args, "session_id", "") or "").strip()
        if not sid:
            return 2
        sess = load_session(r, sid)
        if sess is None:
            print(f"Unknown session {sid!r}", file=sys.stderr)
            return 1
        d0 = draft_path(r, sid, sess.current_round)
        if not d0.is_file():
            d0 = draft_path(r, sid, max(0, sess.current_round - 1))
        draft = read_json(d0) if d0.is_file() else None
        payload = {"session": to_jsonable(sess), "draft_preview": draft}
        if getattr(args, "json", False):
            print(dumps_json(payload))
        else:
            print(f"session_id: {sess.session_id}")
            print(f"status: {sess.status.value} round: {sess.current_round}/{sess.max_rounds}")
            print(f"type: {sess.artifact_type.value} source: {sess.source_id}")
            if draft and isinstance(draft, dict):
                print(f"title: {draft.get('title', '')[:120]}")
        return 0

    if cmd == "list":
        rows = list_session_entries(r)
        if getattr(args, "json", False):
            print(dumps_json({"schema": "argus.refine_list.v1", "sessions": rows}))
        else:
            for e in rows[:80]:
                print(
                    f"{e.get('session_id')}\t{e.get('artifact_type')}\t{e.get('status')}\t"
                    f"r={e.get('current_round')}\t{e.get('source_id')}"
                )
        return 0

    if cmd == "approve":
        sid = str(getattr(args, "session_id", "") or "").strip()
        try:
            sess = approve_session(r, sid)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        if getattr(args, "json", False):
            print(dumps_json(to_jsonable(sess)))
        else:
            print(f"Approved (manual): {sess.session_id}")
        return 0

    if cmd == "retry":
        sid = str(getattr(args, "session_id", "") or "").strip()
        try:
            sess = retry_session(r, sid)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        if getattr(args, "json", False):
            print(dumps_json(to_jsonable(sess)))
        else:
            print(f"Retry opened: {sess.session_id} status={sess.status.value} round={sess.current_round}")
        return 0

    print("Unknown refine command.", file=sys.stderr)
    return 2
