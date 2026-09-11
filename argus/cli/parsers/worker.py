"""CLI: ``argus worker`` — work orders, actions, and dry execution."""

from __future__ import annotations

from argus.cli.parser_common import add_products_dir


def _add_work_order_transition_args(p) -> None:
    p.add_argument(
        "--work-order-id",
        required=True,
        metavar="ID",
        help="Stamped work order id (runs/worker/work_orders/<id>.json)",
    )
    p.add_argument(
        "--note",
        default="",
        help="Optional steward note (stored on the action artifact)",
    )
    p.add_argument(
        "--acted-by",
        default="steward",
        metavar="WHO",
        help="Actor id recorded on the action (default: steward)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.worker.work_order_action_report.v1 JSON",
    )
    p.add_argument(
        "--no-save",
        action="store_true",
        help="Validate the transition only; do not write runs/worker/work_orders/actions/",
    )
    add_products_dir(p)


def register_worker_commands(sub) -> None:
    w = sub.add_parser(
        "worker",
        help="Worker lane: work orders from Argus core to the executor (issue, inspect, dry execute).",
    )
    w_sub = w.add_subparsers(dest="worker_command", required=True)

    show = w_sub.add_parser(
        "show-work-orders",
        help="List work orders under runs/worker/work_orders/latest/ (or one by id)",
    )
    show.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON (argus.worker.show_work_orders.v1)",
    )
    show.add_argument(
        "--work-order-id",
        default=None,
        metavar="ID",
        help="Load a single work order by id from runs/worker/work_orders/<id>.json",
    )
    show.add_argument(
        "--product-id",
        default=None,
        metavar="ID",
        help="Filter latest listings to one product id",
    )
    add_products_dir(show)

    issue = w_sub.add_parser(
        "issue-instrumentation-work-orders",
        help=(
            "Issue argus.work_order.v1 for products with weak/sparse/missing signal instrumentation "
            "(reads latest instrumentation artifacts; does not modify product code)"
        ),
    )
    issue.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.instrumentation_work_order_issuance.v1 JSON",
    )
    issue.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/worker/work_orders/*",
    )
    issue.add_argument(
        "--product-id",
        default=None,
        metavar="ID",
        help="Issue only for this product id (if it qualifies)",
    )
    add_products_dir(issue)

    approve = w_sub.add_parser(
        "approve-work-order",
        help="Record steward approval: pending_approval -> approved",
    )
    _add_work_order_transition_args(approve)

    reject = w_sub.add_parser(
        "reject-work-order",
        help="Record steward rejection: pending_approval|approved -> rejected",
    )
    _add_work_order_transition_args(reject)

    cancel = w_sub.add_parser(
        "cancel-work-order",
        help="Record cancellation: pending_approval|approved -> cancelled",
    )
    _add_work_order_transition_args(cancel)

    reopen = w_sub.add_parser(
        "reopen-work-order",
        help="Re-open a terminal decision: rejected|cancelled -> pending_approval",
    )
    _add_work_order_transition_args(reopen)

    xwo = w_sub.add_parser(
        "execute-work-order",
        help=(
            "Run the v1 dry worker skeleton for exactly one work order; writes argus.worker_execution_outcome.v1"
        ),
    )
    xwo.add_argument(
        "--work-order-id",
        default=None,
        metavar="ID",
        help="Load stamped work order from runs/worker/work_orders/<id>.json",
    )
    xwo.add_argument(
        "--product-id",
        default=None,
        metavar="ID",
        help="With --request-type: load latest runs/worker/work_orders/latest/<id>__<request_type>.json",
    )
    xwo.add_argument(
        "--request-type",
        default=None,
        metavar="TYPE",
        help="With --product-id: request_type segment for the latest work order file",
    )
    xwo.add_argument(
        "--allow-pending-approval",
        action="store_true",
        help="Allow executing pending_approval orders (non-default; for controlled environments)",
    )
    xwo.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.worker.execute_work_order_report.v1 JSON",
    )
    xwo.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/worker/executions/*",
    )
    add_products_dir(xwo)
