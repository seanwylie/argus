from __future__ import annotations

from pathlib import Path

from argus.cli.parser_common import add_products_dir


def register_escalation_execution_commands(sub) -> None:
    # --- escalation ---
    esc = sub.add_parser(
        "escalation",
        help="Durable halt-and-handoff packets when automation must stop (readable + JSON; separate from loop run).",
    )
    esc_sub = esc.add_subparsers(dest="escalation_command", required=True)
    
    gen_e = esc_sub.add_parser(
        "generate",
        help="Evaluate trigger rules; write a packet if escalation is required",
    )
    gen_e.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    gen_e.add_argument("--json", action="store_true", help="Emit full packet JSON")
    gen_e.add_argument(
        "--markdown",
        action="store_true",
        help="Emit Markdown document",
    )
    gen_e.add_argument(
        "--no-save",
        action="store_true",
        help="Print packet only; do not write runs/escalations/",
    )
    gen_e.add_argument(
        "--force-save",
        action="store_true",
        dest="force_save",
        help="Write even when an identical trigger set exists for this product within --dedupe-hours",
    )
    gen_e.add_argument(
        "--dedupe-hours",
        type=float,
        default=24.0,
        dest="dedupe_hours",
        metavar="H",
        help="Skip writing if same product+rules packet exists within this window (default: 24)",
    )
    add_products_dir(gen_e)
    
    show_e = esc_sub.add_parser("show", help="Show a saved packet by id")
    show_e.add_argument(
        "packet_id",
        metavar="PACKET_ID",
        help="Packet id (e.g. esc_20260101T120000Z_myproduct)",
    )
    show_e.add_argument("--json", action="store_true", help="Emit full packet JSON")
    show_e.add_argument("--markdown", action="store_true", help="Emit Markdown document")
    
    list_e = esc_sub.add_parser(
        "list",
        help="List saved packets (newest first)",
    )
    list_e.add_argument("--json", action="store_true", help="Machine-readable list")
    
    # --- actions (contracts; validation, dry-run, gated execute) ---
    act = sub.add_parser(
        "actions",
        help="Load action contracts: validate, dry-run, execute (approval or auto-rules).",
    )
    act_sub = act.add_subparsers(dest="actions_command", required=True)
    add_products_dir(act)
    for name, help_text in (
        ("validate", "Validate an action contract file against inventory and paths"),
        ("dry-run", "Render and analyze what would run (no shell execution)"),
        ("show", "Print an action contract file in a readable form"),
    ):
        p = act_sub.add_parser(name, help=help_text)
        p.add_argument(
            "file",
            type=Path,
            metavar="FILE",
            help="Path to action YAML or JSON",
        )
        p.add_argument("--json", action="store_true", help="Machine-readable output")
    
    act_exec = act_sub.add_parser(
        "execute",
        help="Run after dry-run validation and approval (stored record or auto-approval rules)",
    )
    act_exec.add_argument(
        "file",
        type=Path,
        metavar="FILE",
        help="Path to action YAML or JSON",
    )
    act_exec.add_argument("--json", action="store_true", help="Machine-readable output")
    act_exec.add_argument(
        "--timeout",
        type=float,
        default=3600.0,
        metavar="SEC",
        help="Subprocess timeout in seconds (default: 3600)",
    )
    
    # --- approval (human gate before action execution) ---
    appr = sub.add_parser(
        "approval",
        help="Request, list, approve, reject, or evaluate auto-approval for action files (runs/approval/).",
    )
    appr_sub = appr.add_subparsers(dest="approval_command", required=True)
    appr_list = appr_sub.add_parser("list", help="List approval records")
    appr_list.add_argument("--json", action="store_true")
    appr_list.add_argument(
        "--status",
        default=None,
        choices=("pending", "approved", "rejected"),
        help="Filter by status",
    )
    appr_req = appr_sub.add_parser(
        "request",
        help="Create a pending approval for an action/product pair",
    )
    appr_req.add_argument("--action-id", required=True, dest="action_id")
    appr_req.add_argument("--product", required=True, dest="product_id")
    appr_req.add_argument("--reason", default="")
    appr_req.add_argument("--json", action="store_true")
    appr_ap = appr_sub.add_parser("approve", help="Approve a pending approval by id")
    appr_ap.add_argument("approval_id", metavar="APPROVAL_ID")
    appr_ap.add_argument("--note", default="")
    appr_ap.add_argument("--json", action="store_true")
    appr_rj = appr_sub.add_parser(
        "reject",
        help="Reject a pending approval (--reason required)",
    )
    appr_rj.add_argument("approval_id", metavar="APPROVAL_ID")
    appr_rj.add_argument("--reason", required=True)
    appr_rj.add_argument("--json", action="store_true")
    appr_eval = appr_sub.add_parser(
        "evaluate",
        help="Evaluate whether auto-approval rules allow executing an action file without manual approval",
    )
    appr_eval.add_argument(
        "action_file",
        type=Path,
        metavar="ACTION_FILE",
        help="Path to action YAML or JSON",
    )
    appr_eval.add_argument("--json", action="store_true")
    
    # --- execution (validated subprocess; opt-in) ---
    exe = sub.add_parser(
        "execution",
        help="Run validated action contracts (stored or auto-approved); logs under runs/execution/.",
    )
    exe_sub = exe.add_subparsers(dest="execution_command", required=True)
    exe_run = exe_sub.add_parser(
        "run",
        help="Validate, require approval (stored or auto-rules), then execute (opt-in)",
    )
    exe_run.add_argument(
        "action_file",
        type=Path,
        metavar="ACTION_FILE",
        help="Path to action YAML or JSON",
    )
    exe_run.add_argument(
        "--enable-execution",
        action="store_true",
        help="Explicit opt-in for this invocation (or set ARGUS_EXECUTION_ENABLED=1)",
    )
    exe_run.add_argument(
        "--autonomous",
        action="store_true",
        help="Allow subprocess when the action passes autonomous safety policy (or set ARGUS_AUTONOMOUS_SAFE_EXECUTION=1); still requires auto-approval or stored approval",
    )
    exe_run.add_argument("--json", action="store_true", help="Emit ExecutionRun JSON")
    add_products_dir(exe_run)
    exe_dry = exe_sub.add_parser(
        "dry-run",
        help="Validate + sandbox preview (default safety check; no subprocess or approval)",
    )
    exe_dry.add_argument(
        "action_file",
        type=Path,
        metavar="ACTION_FILE",
        help="Path to action YAML or JSON",
    )
    exe_dry.add_argument(
        "--json",
        action="store_true",
        help="Emit dry-run and sandbox fields as JSON",
    )
    add_products_dir(exe_dry)
    exe_show = exe_sub.add_parser("show", help="Show a saved execution run by run_id")
    exe_show.add_argument(
        "run_id",
        metavar="RUN_ID",
        help="Directory name under runs/execution/ (e.g. exec_20260101T120000Z_a1b2c3d4)",
    )
    exe_show.add_argument("--json", action="store_true", help="Emit ExecutionRun JSON")
    
