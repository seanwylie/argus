from __future__ import annotations

from argus.cli.parser_common import add_products_dir


def register_loop_run_economics_validate_doctor_scan_commands(sub) -> None:
    # --- loop (orchestrated analysis) ---
    loop = sub.add_parser(
        "loop",
        help="Run the orchestrated analysis loop; artifacts under runs/loop/<run_id>/.",
    )
    loop_sub = loop.add_subparsers(dest="loop_command", required=True)
    loop_run = loop_sub.add_parser(
        "run",
        help=(
            "Analysis spine only: discovery → signals → findings → decisions "
            "(not orchestration state, planning, or escalation packets—separate commands)"
        ),
    )
    loop_run.add_argument(
        "--product",
        dest="product_id",
        default=None,
        metavar="ID",
        help="Optional product id; default: all valid inventory products",
    )
    loop_run.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Stop after a failed stage but keep prior stage artifacts (partial run)",
    )
    loop_run.add_argument(
        "--json",
        action="store_true",
        help="Emit manifest JSON to stdout (paths also on stderr unless only JSON desired)",
    )
    add_products_dir(loop_run)
    
    loop_full = loop_sub.add_parser(
        "full",
        help=(
            "Full local harness: 16 stages (discovery→dashboard) incl. ideas + planning/actions; "
            "escalation separate (see summary.json chain); not simulation/portfolio read"
        ),
    )
    loop_full.add_argument(
        "--product",
        dest="product_id",
        default=None,
        metavar="ID",
        help="Optional product id; default: all valid inventory products",
    )
    loop_full.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop the harness on the first failed stage (default: continue after errors)",
    )
    loop_full.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Execution stage uses static dry-run only (default)",
    )
    loop_full.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Mark execution stage as non-dry-run (subprocess execution still gated elsewhere)",
    )
    loop_full.add_argument(
        "--json",
        action="store_true",
        help="Emit summary JSON to stdout",
    )
    add_products_dir(loop_full)
    
    # --- run (loop summaries, safe first-run profile) ---
    run_p = sub.add_parser(
        "run",
        help="Loop run human summaries and Tier 1 safe-profile helper (first-run supervised mode).",
    )
    run_sub = run_p.add_subparsers(dest="run_command", required=True)
    run_sum = run_sub.add_parser(
        "summary",
        help="Print human-readable summary for runs/loop/<run_id>/ (default: latest loop run)",
    )
    run_sum.add_argument(
        "run_id",
        nargs="?",
        default=None,
        metavar="RUN_ID",
        help="Loop harness run id directory under runs/loop/; omit to use latest",
    )
    run_sum.add_argument(
        "--write",
        action="store_true",
        help="Write runs/loop/<run_id>/summary.txt (same content as stdout)",
    )
    run_sum.add_argument("--json", action="store_true", help="Emit JSON with text body")
    run_sp = run_sub.add_parser(
        "safe-profile",
        help="Apply autonomy Tier 1 (suggest-only) + manual mode for supervised first runs",
    )
    run_sp.add_argument("--json", action="store_true", help="Emit JSON with written path")
    
    # --- economics (cost / revenue / ROI from local manifests + signals) ---
    econ = sub.add_parser(
        "economics",
        help="Portfolio economics from product.yaml cost and local signal snapshots (MRR, AWS cost).",
    )
    econ_sub = econ.add_subparsers(dest="economics_command", required=True)
    econ_an = econ_sub.add_parser(
        "analyze",
        help="Per-product monthly cost, estimated revenue, ROI, burn, growth (writes runs/economics/latest.json)",
    )
    econ_an.add_argument("--json", action="store_true", help="Emit full economics JSON")
    add_products_dir(econ_an)
    econ_pf = econ_sub.add_parser(
        "portfolio",
        help="Aggregate totals, top/worst performers, economics signals",
    )
    econ_pf.add_argument("--json", action="store_true", help="Emit portfolio + products JSON")
    add_products_dir(econ_pf)
    econ_res = econ_sub.add_parser(
        "resources",
        help="Resource registry + cost ingest + product mapping; orphans and high-cost/low-value detection",
    )
    econ_res.add_argument(
        "--json",
        action="store_true",
        help="Emit ResourceReport JSON (also writes runs/economics/resources_latest.json)",
    )
    add_products_dir(econ_res)
    
    # --- validate (artifact structure) ---
    val = sub.add_parser(
        "validate",
        help="Validate JSON artifacts under runs/ (execution, findings, decisions, experiments, …).",
    )
    val_sub = val.add_subparsers(dest="validate_command", required=True)
    val_art = val_sub.add_parser(
        "artifacts",
        help="Check expected fields for local run artifacts (optionally one loop run id)",
    )
    val_art.add_argument(
        "run_id",
        nargs="?",
        default=None,
        metavar="RUN_ID",
        help="Optional loop run id — also validates runs/loop/<id>/manifest.json + summary.json",
    )
    val_art.add_argument("--json", action="store_true", help="Emit validation report JSON")
    
    # --- doctor ---
    doc_p = sub.add_parser(
        "doctor",
        help="Health check: inventory, staleness, portfolio vs findings, doctrine YAML, autonomy config, "
        "pending approvals, capability requests, economics linkage, trends vs history.",
    )
    doc_p.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable report (errors, warnings, inventory counts)",
    )
    doc_p.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as failures (exit 1)",
    )
    doc_p.add_argument(
        "--validate-artifacts",
        action="store_true",
        help="Also validate runs/ JSON artifacts (same checks as `argus validate artifacts`)",
    )
    add_products_dir(doc_p)
    
    # --- legacy scan ---
    scan = sub.add_parser(
        "scan",
        help="List product IDs only (legacy; prefer: argus products list)",
    )
    add_products_dir(scan)
