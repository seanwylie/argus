from __future__ import annotations

from argus.cli.parser_common import add_products_dir


def register_policy_commands(sub) -> None:
    pol = sub.add_parser(
        "policy",
        help="Operator control plane: tunable thresholds and weights (see config/operator_policy.yaml).",
    )
    pol_sub = pol.add_subparsers(dest="policy_command", required=True)
    show = pol_sub.add_parser(
        "show-operator",
        help="Resolve effective operator policy and write runs/policy/operator_policy_effective.{json,md}",
    )
    show.add_argument(
        "--json",
        action="store_true",
        help="Print effective policy JSON to stdout only (no files written)",
    )

    exp = pol_sub.add_parser(
        "experiment",
        help="Read-only: compare queue, quiescence, intervention, and cycle synthesis across policy profiles",
    )
    exp.add_argument(
        "--profile",
        dest="experiment_profiles",
        action="append",
        default=[],
        metavar="PATH",
        help="YAML merged over builtin defaults; repeatable for multiple profiles",
    )
    exp.add_argument(
        "--no-effective",
        action="store_true",
        help="Omit repo effective policy (compare default builtin only vs --profile files)",
    )
    exp.add_argument(
        "--json",
        action="store_true",
        help="Print experiment JSON (schema argus.operator_policy_experiment.v1) to stdout",
    )
    exp.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/policy/experiments/* artifacts",
    )

    fb = pol_sub.add_parser(
        "feedback",
        help="Correlate effective operator policy with portfolio outcomes over time (deterministic, read-only inputs)",
    )
    fb.add_argument(
        "--json",
        action="store_true",
        help="Print argus.operator_policy_feedback.v1 JSON to stdout",
    )
    fb.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/policy/feedback/latest.{json,md}",
    )
    fb.add_argument(
        "--limit-history",
        type=int,
        default=50,
        metavar="N",
        help="Max stamped portfolio/outcomes JSONs to load + limit for live outcomes evaluation (default: 50)",
    )

    eff = pol_sub.add_parser(
        "effectiveness",
        help="Mission-segmented outcome rates vs current operator policy (associative, deterministic; no tuning)",
    )
    eff.add_argument(
        "--json",
        action="store_true",
        help="Print argus.operator_policy_effectiveness.v1 JSON to stdout",
    )
    eff.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/policy/effectiveness/latest.{json,md}",
    )
    eff.add_argument(
        "--limit-history",
        type=int,
        default=50,
        metavar="N",
        help="Limit for portfolio outcomes evaluation and stamped outcomes load (default: 50)",
    )

    rec = pol_sub.add_parser(
        "recommend",
        help="Propose human-reviewable operator policy adjustments (read-only; uses feedback, outcomes, patterns, intervention trends)",
    )
    rec.add_argument(
        "--json",
        action="store_true",
        help="Print argus.operator_policy_recommendations.v1 JSON to stdout",
    )
    rec.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/policy/recommendations/latest.{json,md}",
    )
    rec.add_argument(
        "--limit-history",
        type=int,
        default=50,
        metavar="N",
        help="Passes through to feedback/patterns outcomes history limit (default: 50)",
    )
    add_products_dir(rec)

    learn = pol_sub.add_parser(
        "learning-synthesis",
        help="Mission-conditioned learning from outcomes, effectiveness, recommendations, patterns, experiments (read-only)",
    )
    learn.add_argument(
        "--json",
        action="store_true",
        help="Print argus.operator_learning_synthesis.v1 JSON to stdout",
    )
    learn.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/policy/learning_synthesis/latest.{json,md}",
    )
    learn.add_argument(
        "--limit-history",
        type=int,
        default=50,
        metavar="N",
        help="Limit for outcomes/effectiveness/feedback/patterns (default: 50)",
    )
    add_products_dir(learn)
