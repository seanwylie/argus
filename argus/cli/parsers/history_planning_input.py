from __future__ import annotations

from pathlib import Path

from argus.cli.parser_common import add_products_dir


def register_history_planning_input_commands(sub) -> None:
    # --- history (portfolio snapshots + diffs; filesystem only) ---
    hist = sub.add_parser(
        "history",
        help="Capture and compare portfolio snapshots over time (runs/history/).",
    )
    hist_sub = hist.add_subparsers(dest="history_command", required=True)
    add_products_dir(hist)
    hist_snap = hist_sub.add_parser(
        "snapshot",
        help="Record current latest signals/findings/decisions into a timestamped snapshot",
    )
    hist_snap.add_argument(
        "--label",
        default=None,
        metavar="NAME",
        help="Optional suffix for snapshot id (e.g. nightly → <ts>_nightly)",
    )
    hist_snap.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="PATH",
        help="Also write snapshot JSON to this file (or directory + <id>.json)",
    )
    hist_snap.add_argument("--json", action="store_true", help="Emit machine-readable paths")
    
    hist_diff = hist_sub.add_parser(
        "diff",
        help="Compare two snapshot files (older→newer by observed_at when swapped)",
    )
    hist_diff.add_argument(
        "snapshot_a",
        metavar="SNAPSHOT_A",
        help="Snapshot id, or path to snapshot.json",
    )
    hist_diff.add_argument(
        "snapshot_b",
        metavar="SNAPSHOT_B",
        help="Snapshot id, or path to snapshot.json",
    )
    hist_diff.add_argument("--json", action="store_true", help="Emit delta JSON")
    
    hist_prod = hist_sub.add_parser(
        "product",
        help="Show how one product changed across stored snapshots",
    )
    hist_prod.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    hist_prod.add_argument("--json", action="store_true", help="Emit timeline JSON")
    
    # --- trends (deterministic drift over history snapshots) ---
    tr = sub.add_parser(
        "trends",
        help="Trend and drift analysis from runs/history snapshots (rules-based, no AI).",
    )
    tr_sub = tr.add_subparsers(dest="trends_command", required=True)
    tr_an = tr_sub.add_parser(
        "analyze",
        help="Per-product trend/drift analysis (writes runs/trends/latest.{json,txt})",
    )
    tr_an.add_argument(
        "product_id",
        nargs="?",
        default=None,
        metavar="PRODUCT_ID",
        help="Optional product id; default: all products that appear in snapshot history",
    )
    tr_an.add_argument("--json", action="store_true", help="Emit full JSON payload")
    tr_sum = tr_sub.add_parser(
        "summary",
        help="One compact line per product with history",
    )
    tr_sum.add_argument("--json", action="store_true")
    tr_drift = tr_sub.add_parser(
        "drift",
        help="Only products with at least one drift signal",
    )
    tr_drift.add_argument("--json", action="store_true")
    
    # --- planning (weekly portfolio synthesis; read-only) ---
    plan = sub.add_parser(
        "planning",
        help="Synthesize a weekly operating plan and optional ActionContracts (not part of `loop run`).",
    )
    plan_sub = plan.add_subparsers(dest="planning_command", required=True)
    plan_week = plan_sub.add_parser(
        "weekly",
        help="Build deterministic weekly plan (JSON + Markdown under runs/planning/)",
    )
    plan_week.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="PATH",
        help="Write Markdown report to this file or directory (default: runs/planning/weekly.md)",
    )
    plan_week.add_argument(
        "--json",
        action="store_true",
        help="Print full plan JSON to stdout (files still written under runs/planning/)",
    )
    add_products_dir(plan_week)
    
    plan_act = plan_sub.add_parser(
        "actions",
        help="Emit ActionContracts from priorities, experiments, and ranked decisions (runs/planning/actions.json)",
    )
    plan_act.add_argument(
        "--json",
        action="store_true",
        help="Print ActionContracts bundle JSON to stdout",
    )
    plan_act.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="PATH",
        help="Also write JSON to this file or directory (default: runs/planning/actions.json only)",
    )
    plan_act.add_argument(
        "--no-save",
        action="store_true",
        help="Print only; do not write runs/planning/actions.json",
    )
    add_products_dir(plan_act)
    
    # --- input (human strategy / constraints; influences findings, decisions, planning) ---
    inp = sub.add_parser(
        "input",
        help="Record structured human input (global or product) that nudges prioritization and interpretation.",
    )
    inp_sub = inp.add_subparsers(dest="input_command", required=True)
    inp_add = inp_sub.add_parser("add", help="Create a new human input record (JSON on disk)")
    inp_add.add_argument(
        "--scope",
        choices=("global", "product"),
        required=True,
        help="Portfolio-wide or single product",
    )
    inp_add.add_argument(
        "--type",
        dest="type",
        required=True,
        choices=(
            "priority_override",
            "constraint_update",
            "lifecycle_override",
            "note",
            "strategy",
        ),
        help="Input category",
    )
    inp_add.add_argument("--content", default="", help="Freeform text (what the operator intends)")
    inp_add.add_argument("--product-id", default=None, help="Required when scope=product")
    inp_add.add_argument(
        "--expires-at",
        default=None,
        metavar="ISO8601",
        help="Optional expiry (UTC ISO8601); omitted means no expiry",
    )
    inp_add.add_argument(
        "--weight",
        type=float,
        default=1.0,
        help="Strength multiplier for soft nudges (default: 1.0)",
    )
    inp_add.add_argument(
        "--structured",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Structured field (repeat). Values: true/false, numbers, or JSON literals for lists/objects",
    )
    inp_add.add_argument(
        "--structured-json",
        default=None,
        metavar="JSON",
        help="Merge additional structured_fields from a JSON object",
    )
    inp_add.add_argument(
        "--hard",
        action="store_true",
        help="Shortcut for structured hard_override=true (stronger application where supported)",
    )
    inp_add.add_argument("--json", action="store_true", help="Emit created record as JSON")
    
    inp_list = inp_sub.add_parser("list", help="List stored inputs (newest last)")
    inp_list.add_argument("--product-id", default=None, help="Filter to global + this product's inputs")
    inp_list.add_argument("--json", action="store_true")
    
    inp_show = inp_sub.add_parser("show", help="Show one input by id")
    inp_show.add_argument("input_id", metavar="INPUT_ID", help="Input id (e.g. hin_20260101T120000Z_ab12cd34)")
    inp_show.add_argument("--json", action="store_true")
    
    inp_rm = inp_sub.add_parser("remove", help="Delete an input file by id")
    inp_rm.add_argument("input_id", metavar="INPUT_ID", help="Input id")
    inp_rm.add_argument("--json", action="store_true", help="Suppress confirmation line")
    
