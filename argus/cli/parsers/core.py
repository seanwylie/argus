from __future__ import annotations

import argparse
from pathlib import Path

from argus.cli.parser_common import add_products_dir
from argus.products.scaffold import TEMPLATE_TYPES


def register_core_commands(sub) -> None:
    # --- products ---
    prod = sub.add_parser(
        "products",
        help="Discover, validate, scaffold, and inspect product nodes (product.yaml).",
    )
    prod_sub = prod.add_subparsers(dest="products_command", required=True)
    
    def _add_common_products(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--write",
            type=Path,
            default=None,
            metavar="PATH",
            help="Write full inventory JSON snapshot to PATH",
        )
        add_products_dir(p)
    
    list_p = prod_sub.add_parser("list", help="List discovered products (valid and invalid)")
    list_p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON (summary + invalid entries)",
    )
    _add_common_products(list_p)
    
    val_p = prod_sub.add_parser(
        "validate",
        help="Validate all products; exit 1 if any invalid",
    )
    val_p.add_argument(
        "--json",
        action="store_true",
        help="Emit full inventory JSON",
    )
    _add_common_products(val_p)
    
    create_p = prod_sub.add_parser(
        "create",
        help="Scaffold a new product directory (product.yaml, scripts, app/, metrics/)",
    )
    create_p.add_argument(
        "name",
        metavar="NAME",
        help="Directory name and product id (normalized to a slug, e.g. my-app)",
    )
    create_p.add_argument(
        "--type",
        dest="template_type",
        default="content_stream",
        choices=sorted(TEMPLATE_TYPES),
        metavar="TEMPLATE_TYPE",
        help="Template preset for metrics and lifecycle hints",
    )
    create_p.add_argument(
        "--force",
        action="store_true",
        help="Replace existing products/<name> (destructive)",
    )
    create_p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable result",
    )
    create_p.add_argument(
        "--no-git",
        action="store_true",
        help="Do not run git init / initial commit under the new product directory",
    )
    add_products_dir(create_p)
    
    boot_p = prod_sub.add_parser(
        "bootstrap",
        help="Add reality wiring after scaffold: doctrine template, metrics placeholders, experiment seed",
    )
    boot_p.add_argument("product_id", help="Existing product id (products/<id>)")
    boot_p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite bootstrap files if they already exist",
    )
    boot_p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable result",
    )
    add_products_dir(boot_p)

    inst_p = prod_sub.add_parser(
        "instrument-signals",
        help=(
            "Assess signal coverage and observability readiness (argus.product_signal_instrumentation.v1); "
            "does not mutate telemetry"
        ),
    )
    inst_p.add_argument(
        "--product-id",
        required=True,
        metavar="ID",
        help="Product id (products/<id>)",
    )
    inst_p.add_argument(
        "--json",
        action="store_true",
        help="Emit instrumentation payload JSON to stdout",
    )
    inst_p.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/products/signal_instrumentation/* artifacts",
    )
    add_products_dir(inst_p)
    
    show_p = prod_sub.add_parser("show", help="Show one product by id")
    show_p.add_argument("product_id", help="Product id from product.yaml")
    show_p.add_argument(
        "--json",
        action="store_true",
        help="Emit product node JSON",
    )
    _add_common_products(show_p)

    perm_p = prod_sub.add_parser(
        "permissions",
        help="Phase 1 project permission policy (products/<id>/argus.policy.yaml).",
    )
    perm_sub = perm_p.add_subparsers(dest="permissions_command", required=True)
    perm_show = perm_sub.add_parser(
        "show",
        help="Show yes/no/confirm policy and environment alignment for one product",
    )
    perm_show.add_argument("product_id", help="Product id (products/<id>)")
    perm_show.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON (policy + alignment + mismatches)",
    )
    _add_common_products(perm_show)

    perm_resp = perm_sub.add_parser(
        "respond",
        help="Record a Phase 2 operator approval response for confirm policy (artifact-backed grants)",
    )
    perm_resp.add_argument("product_id", help="Product id (products/<id>)")
    perm_resp.add_argument(
        "--field",
        dest="phase1_field",
        required=True,
        metavar="KEY",
        help="Phase 1 policy field (e.g. mutate_nonprod)",
    )
    perm_resp.add_argument(
        "--as",
        dest="response_kind",
        required=True,
        choices=("confirm_once", "always", "no"),
        help="Operator response: confirm_once | always | no",
    )
    perm_resp.add_argument(
        "--orchestration-action",
        dest="orchestration_action_id",
        default=None,
        metavar="ACTION_ID",
        help="Required for confirm_once (orchestration action_id, e.g. findings_generate)",
    )
    perm_resp.add_argument(
        "--note",
        default=None,
        help="Optional note stored on the grant / audit row",
    )
    perm_resp.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON result",
    )
    _add_common_products(perm_resp)

    propose_p = prod_sub.add_parser(
        "propose-creation",
        help="Propose new products based on portfolio gaps and creation mission (does not scaffold)",
    )
    propose_p.add_argument(
        "--json",
        action="store_true",
        help="Emit creation proposals JSON (schema argus.product_creation_proposals.v1) to stdout",
    )
    propose_p.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/products/creation/* artifacts",
    )

    scaff_p = prod_sub.add_parser(
        "scaffold-creation",
        help="Scaffold products/<id> from a creation proposal (mission block, starter files; no overwrite)",
    )
    scaff_p.add_argument(
        "--proposal-id",
        dest="creation_proposal_id",
        required=True,
        metavar="ID",
        help="proposal_id from argus products propose-creation (runs/products/creation/latest.json)",
    )
    scaff_p.add_argument(
        "--product-id",
        dest="creation_product_id",
        default=None,
        metavar="SLUG",
        help="Override directory / product id (normalized slug); default derived from proposal concept title",
    )
    scaff_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write products/; emit full planned file contents in JSON",
    )
    scaff_p.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.product_creation_scaffold.v1 JSON to stdout",
    )
    scaff_p.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/products/creation_scaffold/* artifacts",
    )
    scaff_p.add_argument(
        "--bootstrap",
        action="store_true",
        help=(
            "After a successful scaffold (not dry-run), run argus products bootstrap-creation "
            "for the new product"
        ),
    )
    scaff_p.add_argument(
        "--bootstrap-minimal",
        action="store_true",
        help="With --bootstrap, only signals + findings (skip decisions + ideas pipeline)",
    )
    scaff_p.add_argument(
        "--no-git",
        action="store_true",
        help="Do not run git init / initial commit under the new product directory after scaffold",
    )

    boot_p = prod_sub.add_parser(
        "bootstrap-creation",
        help=(
            "Run first Argus loop on a scaffolded product: validate, signals, findings, "
            "and optionally decisions + ideas (see docs/product-creation-bootstrap.md)"
        ),
    )
    boot_p.add_argument(
        "--product-id",
        dest="creation_bootstrap_product_id",
        required=True,
        metavar="ID",
        help="Product id under products/<id>/",
    )
    boot_p.add_argument(
        "--minimal",
        action="store_true",
        help="Only signals + findings (skip decisions + ideas)",
    )
    boot_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate product only; do not run signals, findings, or write pipeline outputs",
    )
    boot_p.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.product_creation_bootstrap.v1 JSON to stdout",
    )
    boot_p.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/products/creation_bootstrap/* artifacts",
    )
    add_products_dir(boot_p)

    deprec_p = prod_sub.add_parser(
        "propose-deprecation",
        help=(
            "Propose retire/harvest/archive/repair-first candidates from outcomes, strategy, "
            "intervention inbox, queue, and patterns (advisory only)"
        ),
    )
    deprec_p.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.product_deprecation_proposals.v1 JSON to stdout",
    )
    deprec_p.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/products/deprecation/* artifacts",
    )
    add_products_dir(deprec_p)

    plan_dep = prod_sub.add_parser(
        "plan-deprecation",
        help=(
            "Build a structured retirement plan from a deprecation proposal "
            "(plan-only; no deletion or product mutation)"
        ),
    )
    plan_dep.add_argument(
        "--proposal-id",
        dest="deprecation_plan_proposal_id",
        required=True,
        metavar="ID",
        help="proposal_id from argus products propose-deprecation (runs/products/deprecation/latest.json)",
    )
    plan_dep.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.product_deprecation_plan.v1 JSON to stdout",
    )
    plan_dep.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/products/deprecation_plan/* artifacts",
    )
    add_products_dir(plan_dep)

    prom_cr = prod_sub.add_parser(
        "promote-creation",
        help="Traceable promotion: creation proposal → scaffold (optional bootstrap); writes promotion_actions/*",
    )
    prom_cr.add_argument(
        "--proposal-id",
        required=True,
        metavar="ID",
        help="proposal_id from runs/products/creation/latest.json",
    )
    prom_cr.add_argument(
        "--product-id",
        default=None,
        metavar="SLUG",
        help="Override product directory id (default: derived from proposal)",
    )
    prom_cr.add_argument(
        "--bootstrap",
        action="store_true",
        help="After scaffold, run creation bootstrap for the new product",
    )
    prom_cr.add_argument(
        "--bootstrap-minimal",
        action="store_true",
        help="With --bootstrap, signals+findings only",
    )
    prom_cr.add_argument("--dry-run", action="store_true", help="Scaffold dry-run (no products/ writes)")
    prom_cr.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.lifecycle_promotion_action.v1 JSON to stdout",
    )
    prom_cr.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/products/promotion_actions/* or stage artifacts",
    )
    add_products_dir(prom_cr)

    prom_bo = prod_sub.add_parser(
        "promote-bootstrap",
        help="Traceable promotion: scaffolded product → creation bootstrap",
    )
    prom_bo.add_argument(
        "--product-id",
        required=True,
        metavar="ID",
        help="Product id under products/<id>/",
    )
    prom_bo.add_argument("--minimal", action="store_true", help="Signals + findings only")
    prom_bo.add_argument("--dry-run", action="store_true", help="Validate only; no pipeline writes")
    prom_bo.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.lifecycle_promotion_action.v1 JSON to stdout",
    )
    prom_bo.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write promotion_actions/* or creation_bootstrap artifacts",
    )
    add_products_dir(prom_bo)

    prom_dp = prod_sub.add_parser(
        "promote-deprecation",
        help="Traceable promotion: deprecation proposal → deprecation plan (plan-only)",
    )
    prom_dp.add_argument(
        "--proposal-id",
        required=True,
        metavar="ID",
        help="proposal_id from runs/products/deprecation/latest.json",
    )
    prom_dp.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate plan JSON only; do not write runs/products/deprecation_plan/*",
    )
    prom_dp.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.lifecycle_promotion_action.v1 JSON to stdout",
    )
    prom_dp.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write promotion_actions/* (stage writes follow --dry-run)",
    )
    add_products_dir(prom_dp)

    # --- signals ---
    sig = sub.add_parser(
        "signals",
        help=(
            "Collect and inspect normalized SignalRecords (adapters → runs/signals/latest/). "
            "Declare expectations in signals.yaml or product.yaml signal_manifest; "
            "freshness sidecar runs/temporal/latest/; optional cursor-prompt|cursor-ingest writes "
            "runs/signals/review/ (interpretation only; see docs/model-contracts.md)."
        ),
    )
    sig_sub = sig.add_subparsers(dest="signals_command", required=True)
    
    coll = sig_sub.add_parser(
        "collect",
        help=(
            "Run enabled adapters per product.yaml; writes runs/signals/latest/ and temporal sidecar. "
            "Manifest rows (signals.yaml / signal_manifest) attach canonical labels at save time."
        ),
    )
    coll.add_argument(
        "product_id",
        nargs="?",
        default=None,
        metavar="PRODUCT_ID",
        help="Optional product id; if omitted, collect for every valid inventory product",
    )
    coll.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    coll.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/signals/latest/ or collections/",
    )
    coll.add_argument(
        "--merge-adapter-layer",
        action="store_true",
        help="Append argus.adapters pipeline records after classic adapters (see docs/adapters.md)",
    )
    add_products_dir(coll)

    prune_coll = sig_sub.add_parser(
        "prune-collections",
        help=(
            "Remove old timestamped JSON under runs/signals/collections/ "
            "(per-product history cap; never deletes runs/signals/latest/). "
            "Automatic pruning also runs after each signals collect save."
        ),
    )
    prune_coll.add_argument(
        "--keep",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Max collection files to keep per product "
            "(default: ARGUS_SIGNALS_COLLECTIONS_KEEP_PER_PRODUCT or 32)"
        ),
    )
    prune_coll.add_argument(
        "--dry-run",
        action="store_true",
        help="List files that would be deleted without removing them",
    )
    prune_coll.add_argument("--json", action="store_true")

    adapters_p = sig_sub.add_parser("adapters", help="List registered signal adapters")
    adapters_p.add_argument("--json", action="store_true")
    
    show_s = sig_sub.add_parser(
        "show",
        help=(
            "Show last persisted bundle + manifest/temporal hints; JSON includes operator block "
            "(worst_freshness_status, temporal presence)."
        ),
    )
    show_s.add_argument("product_id", help="Product id")
    show_s.add_argument("--json", action="store_true")
    
    real_s = sig_sub.add_parser(
        "reality",
        help=(
            "Classify latest signal bundle into reality states (manifest + canonical + recency); "
            "writes runs/signals/reality/latest/<product_id>.json"
        ),
    )
    real_s.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    real_s.add_argument("--json", action="store_true")
    real_s.add_argument(
        "--no-save",
        action="store_true",
        help="Print only; do not write runs/signals/reality/latest/<product_id>.json",
    )
    add_products_dir(real_s)
    
    sig_cursor_prompt = sig_sub.add_parser(
        "cursor-prompt",
        help=(
            "Print a Cursor prompt for repo-aware signal interpretation "
            "(argus.signal_cursor_review.v1; does not call LLMs)."
        ),
    )
    sig_cursor_prompt.add_argument(
        "--product-id",
        dest="product_id",
        required=True,
        metavar="PRODUCT_ID",
        help="Product id (requires runs/signals/latest/<id>.json from a prior collect)",
    )
    
    sig_cursor_ingest = sig_sub.add_parser(
        "cursor-ingest",
        help=(
            "Ingest structured JSON from Cursor into runs/signals/review/<product_id>.json "
            "(additive; deterministic latest bundle unchanged)."
        ),
    )
    sig_cursor_ingest.add_argument(
        "--product-id",
        dest="product_id",
        required=True,
        metavar="PRODUCT_ID",
        help="Product id",
    )
    sig_cursor_ingest.add_argument(
        "--file",
        dest="file",
        required=True,
        metavar="PATH",
        help="Path to JSON file matching argus.signal_cursor_review.v1",
    )
    sig_cursor_ingest.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Fail if a signal_review already exists for this product",
    )
    sig_cursor_ingest.add_argument("--json", action="store_true")
    
    snap_types = sig_sub.add_parser(
        "snapshot-types",
        help="List business snapshot adapters (local JSON/CSV → CUSTOM signals)",
    )
    snap_types.add_argument("--json", action="store_true")
    
    ingest_sn = sig_sub.add_parser(
        "ingest-snapshots",
        help="Ingest metrics/snapshots/*.json|csv into normalized business SignalRecords",
    )
    ingest_sn.add_argument(
        "product_id",
        nargs="?",
        default=None,
        metavar="PRODUCT_ID",
        help="Optional product id; if omitted, all valid inventory products",
    )
    ingest_sn.add_argument("--json", action="store_true")
    ingest_sn.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/signals/latest/",
    )
    ingest_sn.add_argument(
        "--no-merge",
        action="store_true",
        help="Replace latest bundle with snapshot-only records (no merge with prior collect)",
    )
    ingest_sn.add_argument(
        "--no-fixtures",
        action="store_true",
        help="Only scan products/<id>/metrics/snapshots (skip fixtures/business_snapshots)",
    )
    add_products_dir(ingest_sn)
    
    # --- temporal (time-aware snapshots + collection recency / runs/temporal/) ---
    tmp = sub.add_parser(
        "temporal",
        help=(
            "Temporal sidecar for signal bundles: freshness scores/status (deterministic). "
            "After signals collect, use freshness|summary|refresh; see docs/temporal.md."
        ),
    )
    tmp_sub = tmp.add_subparsers(dest="temporal_command", required=True)
    tmp_ing = tmp_sub.add_parser(
        "ingest",
        help="Parse runs/temporal/snapshots/** and fixtures/temporal/snapshots/**; save per product bucket",
    )
    tmp_ing.add_argument(
        "product_id",
        nargs="?",
        default=None,
        metavar="PRODUCT_ID",
        help="Optional filter: only persist records for this product id (e.g. _global_, _portfolio_, or a product)",
    )
    tmp_ing.add_argument("--json", action="store_true")
    tmp_ing.add_argument(
        "--no-merge",
        action="store_true",
        help="Do not merge with existing runs/signals/latest/<id>.json",
    )
    tmp_ing.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/signals/ (print summary only)",
    )
    tmp_adp = tmp_sub.add_parser(
        "adapters",
        help="List temporal snapshot filename patterns and the temporal_snapshots signal adapter",
    )
    tmp_adp.add_argument("--json", action="store_true")
    tmp_show = tmp_sub.add_parser(
        "show",
        help="Show latest temporal bundle for a product (runs/temporal/latest/)",
    )
    tmp_show.add_argument("product_id", metavar="PRODUCT_ID")
    tmp_show.add_argument("--json", action="store_true")
    tmp_fresh = tmp_sub.add_parser(
        "freshness",
        help="Per-product freshness: legacy buckets + freshness_status counts + worst_freshness_status",
    )
    tmp_fresh.add_argument("product_id", metavar="PRODUCT_ID")
    tmp_fresh.add_argument("--json", action="store_true")
    tmp_tsum = tmp_sub.add_parser(
        "summary",
        help="Summarize temporal state for all valid products; writes runs/temporal/summary.json",
    )
    tmp_tsum.add_argument("--json", action="store_true")
    add_products_dir(tmp_tsum)
    tmp_ref = tmp_sub.add_parser(
        "refresh",
        help="Recompute temporal bundle from runs/signals/latest/<product>.json",
    )
    tmp_ref.add_argument("product_id", metavar="PRODUCT_ID")
    tmp_ref.add_argument("--json", action="store_true")
    
    # --- adapters (layered collect → normalize → signals) ---
    adpt = sub.add_parser(
        "adapters",
        help="Adapter layer: explicit pipeline and registry (wraps local signal adapters).",
    )
    adpt_sub = adpt.add_subparsers(dest="adapters_command", required=True)
    adpt_list = adpt_sub.add_parser("list", help="List adapter ids registered in the adapter layer")
    adpt_list.add_argument("--json", action="store_true")
    adpt_run = adpt_sub.add_parser(
        "run",
        help="Run collect→normalize→to_signal_records for one adapter or all",
    )
    adpt_run.add_argument(
        "adapter_id",
        metavar="ADAPTER_ID",
        help="Adapter id (execution, filesystem, metrics) or 'all'",
    )
    adpt_run.add_argument(
        "--product-id",
        required=True,
        metavar="PRODUCT_ID",
        help="Product id to build context for",
    )
    adpt_run.add_argument("--json", action="store_true")
    add_products_dir(adpt_run)
    
