from __future__ import annotations


def register_mission_commands(sub) -> None:
    miss = sub.add_parser(
        "mission",
        help="Mission / ethos profiles — operator intent (config/mission_profiles.yaml); does not alter signals or findings.",
    )
    miss_sub = miss.add_subparsers(dest="mission_command", required=True)
    show = miss_sub.add_parser(
        "show",
        help="Resolve effective mission and write runs/mission/mission_effective.json",
    )
    show.add_argument(
        "--json",
        action="store_true",
        help="Print argus.mission_effective.v1 JSON to stdout only (no file write)",
    )

    test = miss_sub.add_parser(
        "test",
        help="Read-only mission experiment for one or more products (targeted overrides; compares single profiles and/or structured compositions).",
    )
    test.add_argument(
        "--product-id",
        required=True,
        help="Product id to evaluate (canonical mission in product.yaml is overridden only inside experiment scope).",
    )
    test.add_argument(
        "--profile",
        dest="profiles",
        nargs="*",
        default=[],
        metavar="PROFILE",
        help="One or more mission profile ids from config/mission_profiles.yaml (mutually exclusive with --composition).",
    )
    test.add_argument(
        "--composition",
        dest="compositions",
        action="append",
        default=[],
        metavar="SPEC",
        help=(
            "Structured mission composition (repeat for multiple variants). "
            "Format: objective=<id> drivers=<id>,<id> guardrails=<id>,<id> "
            "(space-separated key=value; ids are registry profile keys). "
            "Mutually exclusive with --profile."
        ),
    )
    test.add_argument(
        "--json",
        action="store_true",
        help="Print argus.mission_experiment.v2 JSON to stdout",
    )
    test.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/mission/experiments/latest.{json,md}",
    )

    sweep = miss_sub.add_parser(
        "sweep",
        help="Read-only mission sweep across profiles or structured compositions (portfolio or filtered products).",
    )
    sweep.add_argument(
        "--profiles",
        default="",
        help="Comma-separated mission profile ids (e.g. revenue,education,engagement). Mutually exclusive with --composition.",
    )
    sweep.add_argument(
        "--composition",
        dest="compositions",
        action="append",
        default=[],
        metavar="SPEC",
        help=(
            "Structured composition string (repeat for multiple variants). "
            "Same format as ``mission test --composition``. Mutually exclusive with --profiles."
        ),
    )
    sweep.add_argument(
        "--product-id",
        action="append",
        default=None,
        help="Optional: limit forced mission overrides to these products (mixed portfolio). Repeat flag for multiple.",
    )
    sweep.add_argument(
        "--json",
        action="store_true",
        help="Print argus.mission_experiment.v2 JSON to stdout",
    )
    sweep.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/mission/experiments/latest.{json,md}",
    )
