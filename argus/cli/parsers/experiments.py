from __future__ import annotations


def register_experiments_commands(sub) -> None:
    # --- experiments (per-product hypotheses and lifecycle) ---
    exp = sub.add_parser(
        "experiments",
        help="Define and track experiments under runs/experiments/ (filesystem only).",
    )
    exp_sub = exp.add_subparsers(dest="experiments_command", required=True)
    exp_create = exp_sub.add_parser("create", help="Create a new experiment record")
    exp_create.add_argument("--product-id", required=True, metavar="ID", help="Product id")
    exp_create.add_argument("--hypothesis", required=True, help="What you are testing")
    exp_create.add_argument(
        "--type",
        dest="experiment_type",
        required=True,
        choices=("growth", "cost_reduction", "engagement", "content", "infrastructure"),
        help="Experiment category",
    )
    exp_create.add_argument("--description", default="", help="Longer context")
    exp_create.add_argument("--expected-outcome", default="", dest="expected_outcome", help="What good looks like")
    exp_create.add_argument(
        "--success-metric",
        action="append",
        dest="success_metric",
        default=[],
        metavar="METRIC",
        help="Named metric (repeatable)",
    )
    exp_create.add_argument(
        "--start-at",
        default=None,
        help="ISO8601 start (default: now UTC)",
    )
    exp_create.add_argument("--end-at", default=None, help="Optional ISO8601 end")
    exp_create.add_argument(
        "--status",
        default="proposed",
        choices=("proposed", "active", "completed", "failed"),
        help="Initial status (default: proposed)",
    )
    exp_create.add_argument("--confidence", type=float, default=0.5, help="0..1 prior confidence")
    exp_create.add_argument("--json", action="store_true")
    exp_list = exp_sub.add_parser("list", help="List experiments (optionally filter by product)")
    exp_list.add_argument("--product-id", default=None, metavar="ID", help="Filter by product id")
    exp_list.add_argument("--json", action="store_true")
    exp_show = exp_sub.add_parser("show", help="Show one experiment by id")
    exp_show.add_argument("experiment_id", metavar="EXPERIMENT_ID", help="Experiment id (exp_...)")
    exp_show.add_argument("--json", action="store_true")
    exp_upd = exp_sub.add_parser("update-status", help="Move experiment to a new status")
    exp_upd.add_argument("experiment_id", metavar="EXPERIMENT_ID", help="Experiment id")
    exp_upd.add_argument(
        "--status",
        required=True,
        choices=("proposed", "active", "completed", "failed"),
        help="Target status",
    )
    exp_upd.add_argument("--json", action="store_true")
    exp_eval = exp_sub.add_parser(
        "evaluate",
        help="Evaluate open experiments from snapshots + trends (deterministic; may update status)",
    )
    exp_eval.add_argument(
        "evaluate_product_id",
        nargs="?",
        default=None,
        metavar="PRODUCT_ID",
        help="Optional product id; default: all products with open experiments",
    )
    exp_eval.add_argument(
        "--no-apply",
        action="store_true",
        help="Print verdicts without writing experiment status updates",
    )
    exp_eval.add_argument("--json", action="store_true")
    exp_prop = exp_sub.add_parser(
        "propose",
        help="Suggest next experiments from findings, trends, decisions, and lifecycle (no execution)",
    )
    exp_prop.add_argument(
        "propose_product_id",
        nargs="?",
        default=None,
        metavar="PRODUCT_ID",
        help="Optional product id; default: all valid products",
    )
    exp_prop.add_argument("--json", action="store_true")
    exp_rank = exp_sub.add_parser(
        "rank",
        help="Rank proposed experiments by impact, cost posture, confidence, strategy, and lifecycle",
    )
    exp_rank.add_argument(
        "rank_product_id",
        nargs="?",
        default=None,
        metavar="PRODUCT_ID",
        help="Optional product id; default: all valid products",
    )
    exp_rank.add_argument("--json", action="store_true")
    exp_apply_ex = exp_sub.add_parser(
        "apply-execution",
        help="Apply runs/execution/*.json outcomes to linked experiments (status + streaks)",
    )
    exp_apply_ex.add_argument("--json", action="store_true")
    
