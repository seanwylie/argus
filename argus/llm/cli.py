"""CLI: ``argus llm`` — test connectivity and optional idea expansion."""

from __future__ import annotations

import sys
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json, to_jsonable
from argus.idea_generation.pipeline import run_pipeline
from argus.llm.advisor_runner import persist_council_result, run_advisor_council
from argus.llm.client import LLMCompletionStatus, complete, is_llm_enabled, llm_client_from_env
from argus.products.inventory import build_inventory


def run_llm_command(args: Any) -> int:
    r = repo_root()
    sub = getattr(args, "llm_command", None)

    if sub == "test":
        res = complete(
            'Reply with a single JSON object: {"pong": true} only, no other text.',
            temperature=0.0,
        )
        if getattr(args, "json", False):
            print(
                dumps_json(
                    {
                        "schema": "argus.llm_test.v1",
                        "status": res.status.value,
                        "ok": res.status == LLMCompletionStatus.OK,
                    }
                )
            )
            return 0
        print(f"status={res.status.value}")
        if res.text:
            print(res.text[:2000])
        if res.status != LLMCompletionStatus.OK:
            print(
                "LLM test did not succeed (disabled, missing key, or API error).",
                file=sys.stderr,
            )
        return 0 if res.status == LLMCompletionStatus.OK else 1

    if sub == "expand-ideas":
        pid = getattr(args, "product_id", None)
        if not pid:
            print("product_id required", file=sys.stderr)
            return 2
        inv = build_inventory(r)
        if pid not in inv.valid:
            print(f"Unknown or invalid product: {pid!r}", file=sys.stderr)
            return 1
        seed = str(getattr(args, "seed", "") or "")
        path, bundle = run_pipeline(
            r,
            pid,
            seed=seed,
            include_mutation=not getattr(args, "no_mutation", False),
            advisor_expansion=not getattr(args, "no_advisor_expansion", False),
            llm_idea_expansion=True,
        )
        if getattr(args, "json", False):
            print(
                dumps_json(
                    {
                        "schema": "argus.llm_expand_ideas.v1",
                        "written": str(path.relative_to(r)),
                        "ideas": [to_jsonable(i) for i in bundle.ideas],
                    }
                )
            )
            return 0
        n = sum(1 for i in bundle.ideas if i.llm_expansion)
        print(f"Wrote {path.relative_to(r)} — {n}/{len(bundle.ideas)} ideas have llm_expansion")
        return 0

    if sub == "council":
        pid = getattr(args, "product_id", None)
        if not pid:
            print("product_id required", file=sys.stderr)
            return 2
        inv = build_inventory(r)
        if pid not in inv.valid:
            print(f"Unknown or invalid product: {pid!r}", file=sys.stderr)
            return 1
        focus = getattr(args, "focus", None) or None
        if not is_llm_enabled() or llm_client_from_env() is None:
            msg = "LLM disabled or ARGUS_OPENAI_API_KEY missing — not running council."
            if getattr(args, "json", False):
                print(dumps_json({"schema": "argus.llm_council.v1", "ok": False, "error": msg}))
            else:
                print(msg, file=sys.stderr)
            return 1
        result = run_advisor_council(r, pid, focus=focus)
        path = persist_council_result(r, result)
        if getattr(args, "json", False):
            from argus.llm.advisor_runner import council_result_to_jsonable

            print(
                dumps_json(
                    {
                        "schema": "argus.llm_council.v1",
                        "ok": True,
                        "written": str(path.relative_to(r)),
                        "council": council_result_to_jsonable(result),
                    }
                )
            )
            return 0
        print(f"Wrote {path.relative_to(r)}")
        print(f"sentiment={result.aggregated_sentiment} agreement={result.agreement_signal}")
        return 0

    print("Unknown llm command.", file=sys.stderr)
    return 2
