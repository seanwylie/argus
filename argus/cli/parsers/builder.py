"""CLI: ``argus builder`` — work orders and implementation briefs (no execution)."""

from __future__ import annotations

from pathlib import Path

from argus.builder.contract_registry import manual_set_target_kinds
from argus.cli.parser_common import add_products_dir
from argus.products.scaffold import TEMPLATE_TYPES


def register_builder_commands(sub) -> None:
    b = sub.add_parser(
        "builder",
        help=(
            "Builder: next-expansion (catalog → next_expansion.json), "
            "set-target (write primary_target for bug_fix/signal_instrumentation), "
            "prepare (→ prompt + builder_task.json), invoke, "
            "reconcile (+ optional prepare-next), status / portfolio-view / review / merge "
            "(local merge-readiness + optional merge), "
            "doctor (host readiness for agent containment), "
            "work orders / briefs / creation (inspectable; no auto-exec unless invoke --execute)"
        ),
    )
    b_sub = b.add_subparsers(dest="builder_command", required=True)

    prep = b_sub.add_parser(
        "prepare",
        help=(
            "Generate builder_next_prompt.md + builder_task.json from "
            "products/<id>/content/next_expansion.json (content_slot, bug_fix, signal_instrumentation; "
            "no execution)"
        ),
    )
    prep.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    prep.add_argument(
        "--output",
        choices=["product", "runs"],
        default="product",
        help=(
            "Where to write artifacts: "
            "product → products/<id>/generated/; runs → runs/builder/prepare/<id>/ (default: product)"
        ),
    )
    prep.add_argument(
        "--json",
        action="store_true",
        help="Print builder_task.json payload to stdout (files still written unless --no-save)",
    )
    prep.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write builder_next_prompt.md / builder_task.json to disk",
    )
    add_products_dir(prep)

    stt = b_sub.add_parser(
        "set-target",
        help=(
            "Write content/next_expansion.json primary_target for bug_fix or signal_instrumentation "
            "(replaces primary_target only; preserves other top-level keys). "
            "Optional --prepare runs `argus builder prepare` immediately after a successful write."
        ),
    )
    stt.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    stt.add_argument(
        "--kind",
        choices=sorted(manual_set_target_kinds()),
        required=True,
        help=(
            "Manual Builder contract kind (non-story; from contract registry "
            "manual_set_target_eligible)"
        ),
    )
    stt.add_argument(
        "--id",
        dest="target_id",
        required=True,
        metavar="TARGET_ID",
        help="Increment id (bug id or signal id)",
    )
    stt.add_argument(
        "--allow-path",
        dest="allow_paths",
        action="append",
        metavar="REL_PATH",
        help="Allowed path under the product directory (repeatable; required at least once)",
    )
    stt.add_argument(
        "--bug-statement",
        default=None,
        metavar="TEXT",
        help="Required when --kind bug_fix: what is wrong / objective",
    )
    stt.add_argument(
        "--signal-statement",
        default=None,
        metavar="TEXT",
        help="Required when --kind signal_instrumentation: instrumentation objective",
    )
    stt.add_argument(
        "--pattern",
        dest="path_patterns",
        action="append",
        metavar="GLOB",
        help="Optional allowed_path_patterns entry (repeatable)",
    )
    stt.add_argument(
        "--success-condition",
        default=None,
        metavar="TEXT",
        help="Optional success_condition on primary_target",
    )
    stt.add_argument(
        "--stop-condition",
        default=None,
        metavar="TEXT",
        help="Optional stop_condition on primary_target",
    )
    stt.add_argument(
        "--expect-path",
        dest="expect_paths",
        action="append",
        metavar="REL_PATH",
        help="signal_instrumentation only: expected_product_paths_exist (repeatable)",
    )
    stt.add_argument(
        "--touch-path",
        dest="touch_paths",
        action="append",
        metavar="GLOB",
        help="signal_instrumentation only: instrumentation_touch_paths (repeatable)",
    )
    stt.add_argument(
        "--no-stamp-time",
        action="store_true",
        help="Do not overwrite as_of_utc; preserve existing when present",
    )
    stt.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print payload; do not write next_expansion.json",
    )
    stt.add_argument(
        "--json",
        action="store_true",
        help="Print full next_expansion JSON to stdout (after validation)",
    )
    stt.add_argument(
        "--prepare",
        action="store_true",
        help=(
            "After a successful write (not with --dry-run), run the same prepare step as "
            "`argus builder prepare` for this product (writes builder_next_prompt.md + builder_task.json)"
        ),
    )
    stt.add_argument(
        "--prepare-output",
        dest="prepare_output",
        choices=["product", "runs"],
        default="product",
        metavar="WHERE",
        help=(
            "With --prepare: where to write artifacts (default: product → products/<id>/generated/; "
            "runs → runs/builder/prepare/<id>/)"
        ),
    )
    add_products_dir(stt)

    inv = b_sub.add_parser(
        "invoke",
        help=(
            "Builder v0.5: resolve prepared prompt + task, optional Cursor CLI (--execute), "
            "write runs/builder/invoke/<id>/latest.json"
        ),
    )
    inv.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    inv.add_argument(
        "--prepare-first",
        action="store_true",
        help=(
            "Run builder prepare first (writes products/<id>/generated/ by default). "
            "Use after next-expansion when invoke --execute would otherwise fail with a stale contract."
        ),
    )
    inv.add_argument(
        "--prepare-output",
        choices=["product", "runs"],
        default="product",
        help="Where prepare writes when using --prepare-first (default: product)",
    )
    inv.add_argument(
        "--prompt-path",
        type=Path,
        default=None,
        metavar="PATH",
        help="Explicit builder_next_prompt.md (requires --task-path)",
    )
    inv.add_argument(
        "--task-path",
        type=Path,
        default=None,
        metavar="PATH",
        help="Explicit builder_task.json (requires --prompt-path)",
    )
    inv.add_argument(
        "--backend",
        choices=["cursor", "agent"],
        default="cursor",
        help=(
            "Execution backend for --execute: cursor opens builder_next_prompt.md in the IDE; "
            "agent runs headless `agent -p --output-format json --workspace <repo> <prompt>` "
            "(ARGUS_AGENT_CLI, ARGUS_AGENT_EXTRA_ARGS; no --mode=plan)."
        ),
    )
    inv.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Run subprocess for the chosen --backend: cursor opens the prompt file "
            "(ARGUS_CURSOR_CLI or CURSOR_CLI); agent runs headless print mode with JSON output "
            "(ARGUS_AGENT_CLI). "
            "Refuses if builder_task.json is stale vs content/next_expansion.json unless --prepare-first. "
            "Default is review-only (no subprocess)."
        ),
    )
    inv.add_argument(
        "--dry-run",
        action="store_true",
        help="Tag record mode as dry_run (still no subprocess; mutually exclusive with --execute)",
    )
    inv.add_argument(
        "--no-record",
        action="store_true",
        help="Do not write runs/builder/invoke/<id>/latest.json",
    )
    inv.add_argument(
        "--sandbox",
        dest="agent_sandbox",
        choices=["auto", "bwrap", "none"],
        default=None,
        help=(
            "Agent backend only: outer containment — auto (default: use bubblewrap when available), "
            "bwrap (require bwrap), none (explicit unsandboxed; degraded trust). "
            "Also: ARGUS_BUILDER_AGENT_SANDBOX."
        ),
    )
    inv.add_argument(
        "--allow-unsandboxed",
        action="store_true",
        help=(
            "Agent backend: if bubblewrap is missing, still run the agent (degraded trust). "
            "Also: ARGUS_BUILDER_ALLOW_UNSANDBOXED=1."
        ),
    )
    inv.add_argument(
        "--network",
        dest="agent_network_mode",
        choices=["default", "allow_all", "disabled"],
        default=None,
        metavar="MODE",
        help=(
            "Agent backend + --execute: network policy for bubblewrap. "
            "default (recommended) — unrestricted outbound/inbound vs host net (not degraded); "
            "allow_all — same behavior, explicit wide-open (trust_degraded_network_open); "
            "disabled — add bwrap --unshare-net (offline; may break agent). "
            "Overrides ARGUS_BUILDER_NETWORK_MODE when set."
        ),
    )
    inv.add_argument("--json", action="store_true", help="Print invocation record JSON to stdout")
    add_products_dir(inv)

    rec = b_sub.add_parser(
        "reconcile",
        help=(
            "Builder v0.9: refresh signals + findings; optional --generate-next-expansion then "
            "compare targets vs task/invoke; optional --prepare-next; "
            "write runs/builder/reconcile/<id>/latest.json (does not assert task success or auto-invoke)"
        ),
    )
    rec.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    rec.add_argument(
        "--prompt-path",
        type=Path,
        default=None,
        metavar="PATH",
        help="Explicit builder_next_prompt.md (requires --task-path)",
    )
    rec.add_argument(
        "--task-path",
        type=Path,
        default=None,
        metavar="PATH",
        help="Explicit builder_task.json (optional; with --prompt-path overrides resolution)",
    )
    rec.add_argument(
        "--invoke-record-path",
        type=Path,
        default=None,
        metavar="PATH",
        help="Override invoke record (default: runs/builder/invoke/<id>/latest.json)",
    )
    rec.add_argument(
        "--skip-signals",
        action="store_true",
        help="Do not run argus signals collect",
    )
    rec.add_argument(
        "--skip-findings",
        action="store_true",
        help="Do not run argus findings generate",
    )
    rec.add_argument(
        "--no-record",
        action="store_true",
        help="Do not write runs/builder/reconcile/<id>/latest.json",
    )
    rec.add_argument(
        "--no-escalation",
        action="store_true",
        help=(
            "Do not write runs/escalations/ when Builder reconcile detects serious scope/outcome/trust "
            "anomalies (default: emit a packet; deduped within 24h per product+rule set)"
        ),
    )
    rec.add_argument("--json", action="store_true", help="Print reconciliation record JSON to stdout")
    rec.add_argument(
        "--generate-next-expansion",
        action="store_true",
        help=(
            "After signals/findings, regenerate products/<id>/content/next_expansion.json from "
            "content_catalog; then compare prior vs current for transition / --prepare-next"
        ),
    )
    rec.add_argument(
        "--prepare-next",
        action="store_true",
        help=(
            "If target_transition_status is changed, run builder prepare to refresh generated/ contract; "
            "often combined with --generate-next-expansion so the current target is fresh"
        ),
    )
    rec.add_argument(
        "--prepare-output",
        choices=["product", "runs"],
        default="product",
        help="Where to write prepare artifacts when --prepare-next runs (default: product)",
    )
    add_products_dir(rec)

    st = b_sub.add_parser(
        "status",
        help=(
            "Builder v1.3: declared vs prepared target and latest invoke/reconcile records "
            "(read-only; one-screen alignment summary)"
        ),
    )
    st.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    st.add_argument(
        "--brief",
        action="store_true",
        help="Only print the compact operator summary (Phase 2B); omit full status text",
    )
    st.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.builder_status.v1 JSON to stdout (includes operator_summary)",
    )
    add_products_dir(st)

    mpv = b_sub.add_parser(
        "portfolio-view",
        help=(
            "Multi-product Builder snapshot: one row per inventory product with invoke/reconcile "
            "or Builder escalation (read-only; same data as builder status)"
        ),
    )
    mpv.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.builder_multi_product_view.v1 JSON to stdout",
    )
    add_products_dir(mpv)

    rev = b_sub.add_parser(
        "review",
        help=(
            "Merge-readiness summary for the latest Builder branch (local signals from reconcile; "
            "no merge, push, or PR)"
        ),
    )
    rev.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    rev.add_argument(
        "--json",
        action="store_true",
        help="Emit review fields as JSON (subset of latest reconcile)",
    )
    add_products_dir(rev)

    doc = b_sub.add_parser(
        "doctor",
        help=(
            "Host readiness for trustworthy Builder agent execution: bubblewrap, setpriv, git, "
            "and effective sandbox env (static PATH/env check; not a runtime guarantee)"
        ),
    )
    doc.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.builder.host_readiness.v1 JSON to stdout",
    )
    doc.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 when overall status is degraded (default: exit 1 only for not_ready)",
    )

    mg = b_sub.add_parser(
        "merge",
        help=(
            "Local git merge only: merge builder branch into integration branch in products/<id>/.git "
            "when review_status is merge_candidate (no push, no PR, no branch delete)"
        ),
    )
    mg.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    mg.add_argument(
        "--into",
        dest="merge_into",
        default=None,
        metavar="BRANCH",
        help="Integration branch to merge into (default: main if present, else master)",
    )
    mg.add_argument(
        "--dry-run",
        dest="merge_dry_run",
        action="store_true",
        help="Validate merge_candidate and repo state; do not run git merge",
    )
    mg.add_argument(
        "--no-record",
        dest="merge_no_record",
        action="store_true",
        help="Do not write runs/builder/merge/<id>/latest.json",
    )
    mg.add_argument(
        "--json",
        action="store_true",
        help="Print merge record JSON to stdout",
    )
    add_products_dir(mg)

    nex = b_sub.add_parser(
        "next-expansion",
        help=(
            "Builder v0.8: write products/<id>/content/next_expansion.json from content_catalog.json "
            "(content-catalog heuristic; deterministic; not a planner)"
        ),
    )
    nex.add_argument("product_id", metavar="PRODUCT_ID", help="Product id (must ship a content catalog)")
    nex.add_argument("--json", action="store_true", help="Print generated payload JSON to stdout")
    nex.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write content/next_expansion.json",
    )
    add_products_dir(nex)

    wo = b_sub.add_parser(
        "work-orders",
        help="Generate work orders from signal contract (argus.builder_work_orders_bundle.v1)",
    )
    wo.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    wo.add_argument("--json", action="store_true", help="Print bundle JSON to stdout")
    wo.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/builder/work_orders/<product_id>/latest.{json,md}",
    )
    wo.add_argument(
        "--backend",
        choices=["cursor", "filesystem"],
        default=None,
        help="Include only work orders with this suggested_backend",
    )

    br = b_sub.add_parser(
        "render-brief",
        help="Render a Cursor/filesystem implementation brief for a work order",
    )
    br.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    br.add_argument(
        "--work-order-id",
        dest="work_order_id",
        default=None,
        metavar="ID",
        help="Work order id (default: first in latest bundle)",
    )
    br.add_argument("--json", action="store_true", help="Emit brief as JSON wrapper only (debug)")
    br.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/builder/work_orders/<product_id>/latest_brief.md",
    )

    cp = b_sub.add_parser(
        "creation-propose",
        help="Build inspectable proposal from a creation candidate (writes runs/builder/creation_phase1/latest_proposal.*)",
    )
    cp.add_argument(
        "--candidate-id",
        required=True,
        metavar="ID",
        help="candidate_id from runs/world_context/creation_candidates/latest.json",
    )
    cp.add_argument(
        "--product-id",
        default=None,
        metavar="SLUG",
        help="Target product id / directory name (default: normalize first entity on the candidate)",
    )
    cp.add_argument(
        "--template",
        dest="template_type",
        default=None,
        choices=sorted(TEMPLATE_TYPES),
        help="Scaffold template (default: infer from candidate)",
    )
    cp.add_argument("--json", action="store_true", help="Emit proposal JSON to stdout")
    cp.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/builder/creation_phase1/latest_proposal.{json,md}",
    )

    ca = b_sub.add_parser(
        "creation-apply",
        help="Apply latest (or explicit) creation proposal — dry-run unless --write",
    )
    ca.add_argument(
        "--from-proposal",
        dest="proposal_path",
        type=str,
        default=None,
        metavar="PATH",
        help="Proposal JSON path (default: runs/builder/creation_phase1/latest_proposal.json)",
    )
    ca.add_argument(
        "--write",
        action="store_true",
        help="Create bounded scaffold under products/<id>/ (omit for dry-run only)",
    )
    ca.add_argument("--json", action="store_true", help="Emit result JSON to stdout")
    ca.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/builder/creation_phase1/latest_result.json",
    )
    add_products_dir(ca)
