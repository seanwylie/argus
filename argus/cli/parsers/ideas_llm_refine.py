from __future__ import annotations

import argparse


def register_ideas_llm_refine_commands(sub) -> None:
    # --- ideas (structured idea generation engine) ---
    ideas_p = sub.add_parser(
        "ideas",
        help="Generate and list structured ideas (products, experiments, content, monetization) under runs/ideas/.",
    )
    ideas_sub = ideas_p.add_subparsers(dest="ideas_command", required=True)
    ideas_gen = ideas_sub.add_parser(
        "generate",
        help="Generate ideas from signals, findings, synthesis, mutation; advisors expand in bundle meta only",
    )
    ideas_gen.add_argument(
        "product_id",
        nargs="?",
        default=None,
        metavar="PRODUCT_ID",
        help="Optional; omit for portfolio-level synthesis (+ mutation) only",
    )
    ideas_gen.add_argument(
        "--seed",
        default="",
        help="Deterministic seed for scoring/mutation",
    )
    ideas_gen.add_argument(
        "--no-mutation",
        action="store_true",
        help="Skip mutation variants",
    )
    ideas_gen.add_argument(
        "--no-advisor-expansion",
        action="store_true",
        help="Skip advisor critique/expansion sidecar (advisors never add primary ideas)",
    )
    ideas_gen.add_argument(
        "--no-llm-expansion",
        action="store_true",
        help="Skip optional OpenAI idea expansion (ARGUS_LLM_ENABLED + API key)",
    )
    ideas_gen.add_argument("--json", action="store_true")
    ideas_list = ideas_sub.add_parser("list", help="List timestamped idea bundles under runs/ideas/")
    ideas_list.add_argument("--json", action="store_true")
    
    # --- llm (optional OpenAI augmentation; gated by ARGUS_LLM_ENABLED) ---
    llm_p = sub.add_parser(
        "llm",
        help="Optional LLM helpers: connectivity test, idea expansion, advisor council artifact.",
    )
    llm_sub = llm_p.add_subparsers(dest="llm_command", required=True)
    llm_test = llm_sub.add_parser("test", help="Minimal OpenAI call (JSON pong); no repo writes")
    llm_test.add_argument("--json", action="store_true")
    llm_exp = llm_sub.add_parser(
        "expand-ideas",
        help="Run idea pipeline with LLM expansion on (same as ideas generate + LLM)",
    )
    llm_exp.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    llm_exp.add_argument("--seed", default="", help="Deterministic seed for scoring/mutation")
    llm_exp.add_argument("--no-mutation", action="store_true", help="Skip mutation variants")
    llm_exp.add_argument(
        "--no-advisor-expansion",
        action="store_true",
        help="Skip deterministic advisor expansion sidecar",
    )
    llm_exp.add_argument("--json", action="store_true")
    llm_council = llm_sub.add_parser(
        "council",
        help="Run five-perspective LLM council and write runs/advisors/llm_council_<id>.latest.json",
    )
    llm_council.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    llm_council.add_argument(
        "--focus",
        default=None,
        help="Optional focus question for all advisors",
    )
    llm_council.add_argument("--json", action="store_true")
    
    # --- refine (artifact refinement loops: draft → council → synthesize → regenerate) ---
    ref_p = sub.add_parser(
        "refine",
        help="Staged refinement sessions for ideas, product specs, and implementation plans.",
        epilog="Context packets: set ARGUS_CONTEXT_PACKETS=1 to use structured argus.context bundles in draft/review prompts (see docs/plans/reasoning-context-refinement-architecture.md).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ref_sub = ref_p.add_subparsers(dest="refine_command", required=True)
    ref_start = ref_sub.add_parser("start", help="Create a refinement session (draft generated on first run)")
    ref_start.add_argument(
        "--type",
        dest="artifact_type",
        required=True,
        choices=("idea", "product_spec", "implementation_plan"),
        help="Artifact kind",
    )
    ref_start.add_argument(
        "--source",
        dest="source_id",
        required=True,
        metavar="ID",
        help="idea_id, product_id anchor, or plan key (depends on type)",
    )
    ref_start.add_argument("--product-id", default=None, metavar="PRODUCT_ID", help="Scope for doctrine / product context")
    ref_start.add_argument("--max-rounds", type=int, default=4, metavar="N", help="Max review rounds (default 4)")
    ref_start.add_argument("--json", action="store_true")
    ref_run = ref_sub.add_parser(
        "run",
        help="Execute one refinement cycle for a session",
        description=(
            "CURSOR-backed grounded reviewers require a file at "
            "runs/refinement/<SESSION_ID>/reviews_in/round_<n>.json before running "
            "(n matches the draft round; use `argus refine show SESSION_ID --json` for current_round)."
        ),
    )
    ref_run.add_argument("session_id", metavar="SESSION_ID")
    ref_run.add_argument("--json", action="store_true")
    ref_show = ref_sub.add_parser("show", help="Show session state and latest draft preview")
    ref_show.add_argument("session_id", metavar="SESSION_ID")
    ref_show.add_argument("--json", action="store_true")
    ref_list = ref_sub.add_parser("list", help="List sessions from runs/refinement/index.json")
    ref_list.add_argument("--json", action="store_true")
    ref_appr = ref_sub.add_parser("approve", help="Manual operator approval (override)")
    ref_appr.add_argument("session_id", metavar="SESSION_ID")
    ref_appr.add_argument("--json", action="store_true")
    ref_retry = ref_sub.add_parser(
        "retry",
        help="Re-open a terminal session for another refinement round",
    )
    ref_retry.add_argument("session_id", metavar="SESSION_ID")
    ref_retry.add_argument("--json", action="store_true")
    
