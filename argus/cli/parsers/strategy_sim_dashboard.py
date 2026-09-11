from __future__ import annotations

from pathlib import Path

from argus.cli.parser_common import add_products_dir


def register_strategy_sim_dashboard_commands(sub) -> None:
    # --- strategy (portfolio posture: weights, kill thresholds, experiment bias) ---
    strat = sub.add_parser(
        "strategy",
        help="Set or show operating strategy (decision weights, kill thresholds, experiment bias).",
    )
    strat_sub = strat.add_subparsers(dest="strategy_command", required=True)
    strat_set = strat_sub.add_parser("set", help="Persist mode to runs/strategy/current.json")
    strat_set.add_argument(
        "mode",
        choices=("growth", "profit", "exploration", "survival"),
        metavar="MODE",
        help="growth | profit | exploration | survival",
    )
    strat_show = strat_sub.add_parser("show", help="Print effective strategy parameters")
    strat_show.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (persisted record + effective profile)",
    )
    
    # --- simulation (deterministic scenario preview) ---
    sim_p = sub.add_parser(
        "simulate",
        help="Preview best / expected / worst outcomes for a product or saved experiment (heuristics, no AI).",
    )
    sim_p.add_argument(
        "product_id",
        nargs="?",
        default=None,
        metavar="PRODUCT_ID",
        help="Product id; omit when using --experiment (product is read from the experiment file).",
    )
    sim_p.add_argument(
        "--experiment",
        dest="experiment_id",
        default=None,
        metavar="ID",
        help="Load experiment from runs/experiments/<ID>.json (overrides product choice).",
    )
    sim_p.add_argument(
        "--json",
        action="store_true",
        help="Emit SimulationResult JSON",
    )
    add_products_dir(sim_p)
    
    # --- dashboard ---
    dash = sub.add_parser(
        "dashboard",
        help="Generate a static HTML portfolio view from local runs/ artifacts (no server).",
    )
    dash.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="PATH",
        help="Output HTML file (default: runs/dashboard/index.html)",
    )
    dash.add_argument(
        "--open",
        action="store_true",
        help="Open the generated file in the default browser (file://)",
    )
    dash.add_argument(
        "--strict",
        action="store_true",
        help="Treat invalid JSON artifacts as errors (exit 1 if any); still writes HTML with diagnostics.",
    )
    add_products_dir(dash)
    dash_sub = dash.add_subparsers(dest="dashboard_command", required=False)
    dash_sum = dash_sub.add_parser(
        "summary",
        help="One-page operator summary (queue, portfolio, inbox, outcomes, patterns, lifecycle, learning synthesis) — JSON + Markdown",
    )
    dash_sum.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.operator_summary.v1 JSON to stdout",
    )
    dash_sum.add_argument(
        "--no-save",
        action="store_true",
        help=(
            "Do not write runs/dashboard/operator_summary/latest.{json,md} or "
            "runs/portfolio/builder_activity/* (portfolio Builder activity rollup)"
        ),
    )
    dash_sum.add_argument(
        "--limit-history",
        type=int,
        default=30,
        metavar="N",
        help="History window when outcomes latest.json is missing (default: 30)",
    )
    add_products_dir(dash_sum)

    dash_nar = dash_sub.add_parser(
        "narrative",
        help="Cross-cycle human-readable story (history, outcomes, deltas, patterns, interventions, cycles)",
    )
    dash_nar.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.operator_narrative.v1 JSON to stdout",
    )
    dash_nar.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/dashboard/narrative/latest.{json,md}",
    )
    dash_nar.add_argument(
        "--limit-history",
        type=int,
        default=30,
        metavar="N",
        help="Window for history/outcomes/patterns and recent stamped artifact counts (default: 30)",
    )
    add_products_dir(dash_nar)

