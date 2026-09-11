from __future__ import annotations

from argus.cli.parser_common import add_products_dir


def register_council_capabilities_commands(sub) -> None:
    # --- council (routed grounded vs outsider profiles; refinement consumes these) ---
    cou = sub.add_parser(
        "council",
        help="Inspect routed council profiles (grounded vs outsider). Execution stays in `argus refine`.",
    )
    cou_sub = cou.add_subparsers(dest="council_command", required=True)
    cou_pf = cou_sub.add_parser("profiles", help="List default council profiles by artifact type")
    cou_pf.add_argument("--json", action="store_true")
    cou_show = cou_sub.add_parser(
        "show",
        help="Show members for one artifact type (grounded vs outsider, context policy, backend)",
    )
    cou_show.add_argument(
        "artifact_type",
        metavar="ARTIFACT_TYPE",
        help="idea | product_spec | implementation_plan",
    )
    cou_show.add_argument("--json", action="store_true")
    cou_run = cou_sub.add_parser(
        "run",
        help="Dry-run: resolve profile for --type (refinement owns real rounds; use refine start/run)",
    )
    cou_run.add_argument(
        "--type",
        dest="artifact_type",
        required=True,
        choices=("idea", "product_spec", "implementation_plan"),
        metavar="TYPE",
    )
    cou_run.add_argument(
        "--source",
        dest="source_id",
        default="dry_run",
        metavar="SOURCE_ID",
        help="Opaque source id label (informational only for this command)",
    )
    cou_run.add_argument("--json", action="store_true")
    
    # --- capabilities (self-awareness scaffold) ---
    cap = sub.add_parser(
        "capabilities",
        help="Declared capabilities, gaps, evaluation, and human capability requests.",
    )
    cap_sub = cap.add_subparsers(dest="capabilities_command", required=True)
    cap_list = cap_sub.add_parser("list", help="Show current capability registry")
    cap_list.add_argument("--json", action="store_true")
    cap_eval = cap_sub.add_parser(
        "evaluate",
        help="Scan findings + merge gaps; write runs/capabilities/latest.json",
    )
    cap_eval.add_argument("--json", action="store_true", help="Print full evaluation JSON")
    cap_gaps = cap_sub.add_parser("gaps", help="Print merged gaps and suggested next capability")
    cap_gaps.add_argument("--json", action="store_true")
    
    cap_resume = cap_sub.add_parser(
        "resume",
        help="After capability requests resolve: clear pauses, re-validate actions, enqueue executable work",
    )
    cap_resume.add_argument(
        "product_id",
        nargs="?",
        default=None,
        metavar="PRODUCT_ID",
        help="Optional: only resume actions for this product",
    )
    cap_resume.add_argument(
        "--no-queue",
        action="store_true",
        help="Re-validate only; do not append to executable_queue in autonomy state",
    )
    cap_resume.add_argument("--json", action="store_true")
    add_products_dir(cap_resume)
    
    cap_req = cap_sub.add_parser(
        "request",
        help="Create and manage capability requests (Argus asks humans for what it cannot do)",
    )
    cap_req_sub = cap_req.add_subparsers(dest="request_command", required=True)
    cap_req_list = cap_req_sub.add_parser("list", help="List capability requests")
    cap_req_list.add_argument("--json", action="store_true")
    cap_req_list.add_argument(
        "--status",
        default=None,
        choices=("open", "acknowledged", "fulfilled", "rejected", "cancelled"),
        help="Filter by status",
    )
    cap_req_create = cap_req_sub.add_parser(
        "create",
        help="Create a manual capability request",
    )
    cap_req_create.add_argument("--title", required=True)
    cap_req_create.add_argument("--description", required=True)
    cap_req_create.add_argument(
        "--source",
        default="manual",
        choices=("manual", "execution", "experiment", "advisor"),
        help="Origin label (default: manual)",
    )
    cap_req_create.add_argument("--product-id", default=None, dest="product_id")
    cap_req_create.add_argument("--capability-hint", default="", dest="capability_hint")
    cap_req_create.add_argument(
        "--ref",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Extra source_ref entry (repeatable)",
    )
    cap_req_create.add_argument("--json", action="store_true")
    cap_req_show = cap_req_sub.add_parser("show", help="Show one request by id")
    cap_req_show.add_argument("request_id", metavar="REQUEST_ID")
    cap_req_show.add_argument("--json", action="store_true")
    cap_req_set = cap_req_sub.add_parser(
        "set-status",
        help="Move request status (open → acknowledged → fulfilled / rejected / cancelled)",
    )
    cap_req_set.add_argument("request_id", metavar="REQUEST_ID")
    cap_req_set.add_argument(
        "--status",
        required=True,
        choices=("open", "acknowledged", "fulfilled", "rejected", "cancelled"),
    )
    cap_req_set.add_argument("--note", default="", help="Resolution note / rationale")
    cap_req_set.add_argument("--json", action="store_true")
    
