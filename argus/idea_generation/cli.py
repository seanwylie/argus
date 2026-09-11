"""CLI: ``argus ideas generate`` and ``argus ideas list``."""

from __future__ import annotations

import sys
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json, to_jsonable
from argus.idea_generation.pipeline import ideas_dir, run_pipeline


def run_ideas_command(args: Any) -> int:
    repo = repo_root()
    cmd = getattr(args, "ideas_command", None)

    if cmd == "list":
        base = ideas_dir(repo)
        if not base.is_dir():
            print("(No runs/ideas/ directory yet.)", file=sys.stderr)
            return 0
        files = sorted(base.glob("*.json"), key=lambda p: p.name, reverse=True)
        # Prefer listing timestamped files; skip duplicate read of latest if only latest exists
        ts_files = [p for p in files if p.name != "latest.json"]
        if getattr(args, "json", False):
            rows = [{"path": str(p.relative_to(repo)), "name": p.name} for p in ts_files[:50]]
            print(dumps_json({"schema": "argus.ideas_list.v1", "files": rows}))
            return 0
        print("Idea bundles under runs/ideas/ (newest first):")
        for p in ts_files[:40]:
            print(f"  {p.name}")
        if not ts_files:
            for p in files:
                print(f"  {p.name}")
        return 0

    if cmd == "generate":
        pid = getattr(args, "product_id", None)
        seed = str(getattr(args, "seed", "") or "")
        mutation = not getattr(args, "no_mutation", False)
        advisor_expansion = not getattr(args, "no_advisor_expansion", False)
        llm_idea_expansion = not getattr(args, "no_llm_expansion", False)
        path, bundle = run_pipeline(
            repo,
            pid,
            seed=seed,
            include_mutation=mutation,
            advisor_expansion=advisor_expansion,
            llm_idea_expansion=llm_idea_expansion,
        )
        if getattr(args, "json", False):
            print(
                dumps_json(
                    {
                        "schema": "argus.ideas_generate_result.v1",
                        "written": str(path.relative_to(repo)),
                        "idea_count": len(bundle.ideas),
                        "ideas": [to_jsonable(i) for i in bundle.ideas],
                    }
                )
            )
            return 0
        print(f"Wrote {path.relative_to(repo)} ({len(bundle.ideas)} ideas)")
        for i, idea in enumerate(bundle.ideas[:12]):
            print(f"  {i + 1}. [{idea.type.value}] {idea.title[:72]}")
        if len(bundle.ideas) > 12:
            print(f"  ... and {len(bundle.ideas) - 12} more (see JSON file)")
        return 0

    print("Unknown ideas command.", file=sys.stderr)
    return 2
