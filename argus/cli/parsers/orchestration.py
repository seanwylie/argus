from __future__ import annotations

from argus.cli.parser_common import add_products_dir


def register_orchestration_commands(sub) -> None:
    # --- orchestration (state snapshot from durable artifacts) ---
    orch = sub.add_parser(
        "orchestration",
        help=(
            "Eligibility-driven orchestration: read durable runs/* snapshots; write state/tasks/advancements "
            "(not part of `loop run`)."
        ),
    )
    orch_sub = orch.add_subparsers(dest="orchestration_command", required=True)
    orch_p1map = orch_sub.add_parser(
        "phase1-mapping",
        help=(
            "List orchestration action_id → Phase 1 policy keys (STEP_EXECUTION_REGISTRY vs "
            "ORCHESTRATION_ACTION_PHASE1_KEYS). Exits non-zero if mapping is inconsistent."
        ),
    )
    orch_p1map.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.orchestration_phase1_mapping_report.v1 JSON to stdout",
    )
    orch_state = orch_sub.add_parser(
        "state",
        help=(
            "Inspect eligibility from runs/*; write latest/<id>.json, tasks/latest/<id>.json when next_action "
            "is set, and index.json for --all / multi-id. Does not run signals/audit/refine. "
            "Use advance to record next-step intent."
        ),
    )
    orch_scope = orch_state.add_mutually_exclusive_group(required=True)
    orch_scope.add_argument(
        "--product-id",
        dest="product_ids",
        nargs="+",
        metavar="ID",
        help="One or more product ids (products/<id>/product.yaml)",
    )
    orch_scope.add_argument(
        "--all",
        dest="inventory_all",
        action="store_true",
        help="All valid products from inventory (same scan as products list)",
    )
    orch_state.add_argument(
        "--no-write",
        action="store_true",
        help="Print evaluation only; do not write latest/, tasks/, or index under runs/orchestration/",
    )
    orch_state.add_argument(
        "--json",
        action="store_true",
        help="Emit full state (one product) or index payload (multiple / --all) to stdout",
    )
    add_products_dir(orch_state)

    orch_replay = orch_sub.add_parser(
        "replay",
        help=(
            "Read-only forensic replay from stored orchestration artifacts (no eligibility re-run); "
            "writes runs/orchestration/replay/<product_id>/ by default."
        ),
    )
    orch_replay.add_argument(
        "--product-id",
        dest="product_id",
        required=True,
        metavar="ID",
        help="Product id",
    )
    orch_replay.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.orchestration_replay.v1 JSON to stdout",
    )
    orch_replay.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/orchestration/replay/<product_id>/*",
    )
    add_products_dir(orch_replay)

    orch_portfolio_pri = orch_sub.add_parser(
        "portfolio-priorities",
        help=(
            "Rank valid products by deterministic portfolio priority score (strategy/planning/orchestration); "
            "write runs/orchestration/latest/portfolio_priorities.json (argus.portfolio_priorities.v1)."
        ),
    )
    orch_pp_scope = orch_portfolio_pri.add_mutually_exclusive_group(required=True)
    orch_pp_scope.add_argument(
        "--product-id",
        dest="product_ids",
        nargs="+",
        metavar="ID",
        help="One or more product ids",
    )
    orch_pp_scope.add_argument(
        "--all",
        dest="inventory_all",
        action="store_true",
        help="All valid products from inventory",
    )
    orch_portfolio_pri.add_argument(
        "--no-write",
        action="store_true",
        help="Print evaluation only; do not write portfolio_priorities.json",
    )
    orch_portfolio_pri.add_argument(
        "--json",
        action="store_true",
        help="Emit portfolio_priorities payload to stdout",
    )
    add_products_dir(orch_portfolio_pri)

    orch_pp_trends = orch_sub.add_parser(
        "portfolio-priority-trends",
        help=(
            "Aggregate ranks from recent runs/orchestration/generations/portfolio_priorities_*.json "
            "and write runs/orchestration/latest/portfolio_priority_trends.json "
            "(argus.portfolio_priority_trends.v1)."
        ),
    )
    orch_pp_trends.add_argument(
        "--window",
        dest="trend_window",
        type=int,
        default=5,
        metavar="N",
        help="Rolling window size: last N generation files (default: 5)",
    )
    orch_pp_trends.add_argument(
        "--no-write",
        action="store_true",
        help="Print evaluation only; do not write portfolio_priority_trends.json",
    )
    orch_pp_trends.add_argument(
        "--json",
        action="store_true",
        help="Emit portfolio_priority_trends JSON to stdout",
    )

    orch_batch_adv = orch_sub.add_parser(
        "batch-advance",
        help=(
            "Prioritize products (same as multi-product state), select the first-ranked id, "
            "and run one orchestration advance for that product. Writes batch_advancement.json "
            "(fairness: repeat-top rotation + short rolling recent_selected_product_ids dominance skip) "
            "and operator_summary.json (compact latest selection + artifact links)."
        ),
    )
    orch_batch_scope = orch_batch_adv.add_mutually_exclusive_group(required=True)
    orch_batch_scope.add_argument(
        "--product-id",
        dest="product_ids",
        nargs="+",
        metavar="ID",
        help="One or more product ids to consider for prioritization",
    )
    orch_batch_scope.add_argument(
        "--all",
        dest="inventory_all",
        action="store_true",
        help="All valid products from inventory (same scan as orchestration state --all)",
    )
    orch_batch_adv.add_argument(
        "--no-write",
        action="store_true",
        help="Evaluate and prioritize in memory only; do not write latest/*.json or index.json",
    )
    orch_batch_adv.add_argument(
        "--no-execute",
        action="store_true",
        help="Record advancement only for the selected product (no in-process step execution)",
    )
    orch_batch_adv.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.orchestration_batch_advancement.v1 JSON to stdout",
    )
    add_products_dir(orch_batch_adv)
    
    orch_advance = orch_sub.add_parser(
        "advance",
        help=(
            "Record one deterministic next-step under latest/advancements/<id>.json "
            "(queued/blocked/skipped). Without --execute, does not invoke signals collect, "
            "audit run, refine, or loop."
        ),
    )
    orch_advance.add_argument(
        "--product-id",
        dest="product_id",
        required=True,
        metavar="ID",
        help="Product id (same as orchestration state --product-id)",
    )
    orch_advance.add_argument(
        "--execute",
        dest="execute",
        action="store_true",
        help=(
            "When the advancement is queued, run the in-process step executor for selected_action "
            "(signals_collect, audit_run, orchestration_state_refresh, temporal_refresh, "
            "refinement_start_product_spec, refinement_start_idea, implementation_plan_generate, refinement_run, "
            "refinement_submit_reviews_in, escalation_consider, escalation_packet_generate, execution_outcomes_apply, findings_generate, decisions_generate, ideas_generate); other actions are "
            "queued_unhandled with a reason"
        ),
    )
    orch_advance.add_argument(
        "--no-refresh-state",
        dest="no_refresh_state",
        action="store_true",
        help="Do not rewrite runs/orchestration/latest/<id>.json after recording advancement",
    )
    orch_advance.add_argument("--json", action="store_true", help="Emit advancement JSON to stdout")
    
    orch_progression = orch_sub.add_parser(
        "run-progression",
        help=(
            "Bounded loop: evaluate eligibility → advance (with in-process execution by default) → "
            "re-evaluate until blocked/terminal/unchanged/queued_unhandled/execution_failed or "
            "--max-steps. Use --no-execute for intent-only advances."
        ),
    )
    orch_progression.add_argument(
        "--product-id",
        dest="product_id",
        required=True,
        metavar="ID",
        help="Product id",
    )
    orch_progression.add_argument(
        "--max-steps",
        dest="max_steps",
        type=int,
        default=8,
        metavar="N",
        help="Maximum advance iterations (default: 8)",
    )
    orch_progression.add_argument(
        "--no-refresh-state",
        dest="no_refresh_state",
        action="store_true",
        help="Pass through to each advance: do not refresh runs/orchestration/latest/<id>.json",
    )
    orch_progression.add_argument(
        "--no-execute",
        dest="execute",
        action="store_false",
        default=True,
        help="Record advancement intent only (do not run the in-process step executor each step)",
    )
    orch_progression.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.orchestration_progression_run.v1 JSON to stdout (includes artifact_paths when written)",
    )
    orch_progression.add_argument(
        "--no-write-artifact",
        dest="no_write_artifact",
        action="store_true",
        help=(
            "Do not write argus.orchestration_progression_run_artifact.v1 under "
            "runs/orchestration/latest/progression_runs/ and progression_runs/generations/"
        ),
    )
    
    orch_cursor_prompt = orch_sub.add_parser(
        "cursor-prompt",
        help="Print a Cursor prompt for repo-aware orchestration review (stdout; no API calls).",
    )
    orch_cursor_prompt.add_argument(
        "--product-id",
        dest="product_id",
        required=True,
        metavar="ID",
        help="Product id",
    )
    
    orch_cursor_ingest = orch_sub.add_parser(
        "cursor-ingest",
        help="Merge validated argus.orchestration_cursor_review.v1 JSON into runs/orchestration/review/",
    )
    orch_cursor_ingest.add_argument(
        "--product-id",
        dest="product_id",
        required=True,
        metavar="ID",
        help="Product id",
    )
    orch_cursor_ingest.add_argument(
        "--file",
        dest="file",
        required=True,
        metavar="PATH",
        help="Path to JSON file from Cursor",
    )
    orch_cursor_ingest.add_argument(
        "--no-overwrite",
        dest="no_overwrite",
        action="store_true",
        help="Fail if orchestration_review already exists (unless replacing manually)",
    )
    orch_cursor_ingest.add_argument("--json", action="store_true", help="Emit bundle JSON to stdout")
    
