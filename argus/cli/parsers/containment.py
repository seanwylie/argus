"""Parser: ``argus containment``."""

from __future__ import annotations


def register_containment_commands(sub) -> None:
    c = sub.add_parser(
        "containment",
        help="Credential containment v0: policy, capability map, escalation report.",
    )
    c_sub = c.add_subparsers(dest="containment_command", required=True)

    st = c_sub.add_parser("status", help="Show containment enforcement and capability map")
    st.add_argument(
        "--json",
        action="store_true",
        help="Emit capability map JSON",
    )
    st.add_argument(
        "--save",
        action="store_true",
        help="Write runs/debug/containment/latest.json",
    )

    er = c_sub.add_parser(
        "escalation-report",
        help="Print verbose GitHub/AWS least-privilege guidance (human escalation)",
    )
    er.add_argument(
        "--save",
        action="store_true",
        help="Write runs/debug/containment/escalation_report.md",
    )
