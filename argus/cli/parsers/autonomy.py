from __future__ import annotations

from argus.cli.parser_common import add_products_dir


def register_autonomy_commands(sub) -> None:
    # --- autonomy (spawn proposals; scaffold with approval + quota) ---
    aut = sub.add_parser(
        "autonomy",
        help="Operator safety limits (mode/policy), spawn proposals, scheduled runs, shutdown.",
    )
    aut_sub = aut.add_subparsers(dest="autonomy_command", required=True)
    
    aut_show = aut_sub.add_parser(
        "show",
        help="Show autonomy mode, effective policy, and execution counters (runs/autonomy/).",
    )
    aut_show.add_argument("--json", action="store_true", help="Emit show payload JSON")
    
    aut_status = aut_sub.add_parser(
        "status",
        help="Alias for show — mode, tier, policy, guardrail counters (bounded rollout).",
    )
    aut_status.add_argument("--json", action="store_true", help="Emit show payload JSON")
    
    aut_set = aut_sub.add_parser(
        "set",
        help="Set autonomy mode (writes runs/autonomy/autonomy.json)",
    )
    aut_set.add_argument(
        "mode",
        metavar="MODE",
        choices=("off", "manual", "supervised", "limited", "active"),
        help="Autonomy mode",
    )
    aut_set.add_argument("--json", action="store_true", help="Emit {ok, mode} JSON")
    
    aut_policy = aut_sub.add_parser(
        "policy",
        help="Print effective policy for the current mode (defaults + optional overrides)",
    )
    aut_policy.add_argument("--json", action="store_true", help="Emit policy JSON")
    
    aut_set_tier = aut_sub.add_parser(
        "set-tier",
        help="Set rollout tier 0–4 (writes mode + tier to runs/autonomy/autonomy.json; tier 4 needs ARGUS_ENABLE_TIER4)",
    )
    aut_set_tier.add_argument(
        "tier",
        type=int,
        metavar="N",
        help="Tier: 0 observe, 1 suggest, 2 safe execution, 3 bounded, 4 reserved (not enabled without opt-in)",
    )
    aut_set_tier.add_argument("--json", action="store_true", help="Emit JSON")
    
    aut_explain = aut_sub.add_parser(
        "explain",
        help="Explain whether a rollout action class (or loaded action YAML/JSON) is allowed at the current tier",
    )
    aut_explain.add_argument(
        "action_key",
        metavar="ACTION_OR_FILE",
        help="Matrix key (e.g. local_execution) or path to an action .yaml/.json contract",
    )
    aut_explain.add_argument("--json", action="store_true", help="Emit structured JSON")
    
    aut_activate = aut_sub.add_parser(
        "activate",
        help="Gate: doctor + artifact validation + policy; then set supervised mode if safe",
    )
    aut_activate.add_argument("--json", action="store_true")
    add_products_dir(aut_activate)
    
    aut_spawn = aut_sub.add_parser(
        "spawn",
        help="Emit spawn proposal; optionally scaffold product.yaml, doctrine.yaml, experiment_plan.yaml",
    )
    aut_spawn.add_argument(
        "--apply",
        action="store_true",
        help="Create products/<id>/ (requires --approve-spawn after reviewing latest_proposal.json)",
    )
    aut_spawn.add_argument(
        "--approve-spawn",
        action="store_true",
        help="Confirm human review of the proposal (required with --apply)",
    )
    aut_spawn.add_argument(
        "--max-per-period",
        type=int,
        default=3,
        metavar="N",
        help="Max successful spawns per rolling window",
    )
    aut_spawn.add_argument(
        "--period-days",
        type=int,
        default=30,
        metavar="D",
        help="Rolling window length in days for spawn quota",
    )
    aut_spawn.add_argument("--json", action="store_true")
    add_products_dir(aut_spawn)
    
    aut_run = aut_sub.add_parser(
        "run",
        help="Full pipeline: signals → findings → decisions → experiments → advisors → simulation → planning → execution",
    )
    aut_run.add_argument(
        "--product",
        dest="product_id",
        default=None,
        metavar="ID",
        help="Optional product id; default: all valid inventory products",
    )
    aut_run.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue after a failed stage (partial run)",
    )
    aut_run.add_argument(
        "--execution",
        action="store_true",
        help="Record execution stage intent when ARGUS_EXECUTION_ENABLED is set (never auto-runs shell)",
    )
    aut_run.add_argument(
        "--json",
        action="store_true",
        help="Emit manifest JSON to stdout",
    )
    add_products_dir(aut_run)
    
    aut_start = aut_sub.add_parser(
        "start",
        help="Run the autonomy loop in the background on an interval or cron-like schedule",
    )
    aut_start.add_argument(
        "--interval",
        type=float,
        default=None,
        metavar="SEC",
        help="Run every SEC seconds (mutually exclusive with --cron)",
    )
    aut_start.add_argument(
        "--cron",
        default=None,
        metavar="EXPR",
        help='Cron-like: */N * * * * (every N min), M * * * * (hourly at minute M), M H * * * (daily UTC)',
    )
    aut_start.add_argument(
        "--product",
        dest="product_id",
        default=None,
        metavar="ID",
    )
    aut_start.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue after failed stages in each cycle",
    )
    aut_start.add_argument(
        "--execution",
        action="store_true",
        help="Pass --execution to each cycle (see autonomy run)",
    )
    add_products_dir(aut_start)
    
    aut_stop = aut_sub.add_parser("stop", help="Stop the background autonomy scheduler")
    aut_stop.add_argument("--json", action="store_true", help="No-op for symmetry; exit 0")
    
    aut_shut = aut_sub.add_parser(
        "shutdown",
        help=(
            "Kill-candidate wind-down: kill score, trends, cost, inactivity; "
            "optionally mark deprecated, archive under archive/products/, cleanup stub."
        ),
    )
    aut_shut.add_argument(
        "--product",
        dest="shutdown_product_id",
        metavar="PRODUCT_ID",
        default=None,
        help="Single product id to evaluate (or use --all-candidates)",
    )
    aut_shut.add_argument(
        "--all-candidates",
        action="store_true",
        help="Every product whose kill_score ≥ --min-kill-score",
    )
    aut_shut.add_argument(
        "--min-kill-score",
        type=int,
        default=75,
        metavar="N",
        help="Minimum kill score (0–100) to treat as shutdown candidate (default: 75)",
    )
    aut_shut.add_argument(
        "--apply",
        action="store_true",
        help="Perform deprecation + archive (requires --approve)",
    )
    aut_shut.add_argument(
        "--approve",
        action="store_true",
        help="Acknowledge irreversible move out of products/ (required with --apply)",
    )
    aut_shut.add_argument("--json", action="store_true")
    add_products_dir(aut_shut)
    
