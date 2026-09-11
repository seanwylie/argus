from __future__ import annotations

from argus.cli.parser_common import add_products_dir


def register_self_confidence_advisors_commands(sub) -> None:
    # --- self (meta: critique Argus from local artifacts) ---
    self_p = sub.add_parser(
        "self",
        help="Self-audit and self-improvement planning (capabilities, cadence, meta-findings; no auto-changes).",
    )
    self_sub = self_p.add_subparsers(dest="self_command", required=True)
    self_audit = self_sub.add_parser(
        "audit",
        help="Critique Argus from local runs/ data (missing capabilities, slow cycles, escalations, experiments).",
    )
    self_audit.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.self_audit.v1 JSON",
    )
    self_improve = self_sub.add_parser(
        "improve",
        help="Self-improvement findings, proposals, and weekly-style plan (analysis only).",
    )
    self_improve_sub = self_improve.add_subparsers(dest="self_improve_command", required=True)
    si_find = self_improve_sub.add_parser(
        "findings",
        help="Generate self-findings (gaps, churn, tests, execution loop, friction).",
    )
    si_find.add_argument("--json", action="store_true", help="Emit JSON bundle")
    si_prop = self_improve_sub.add_parser(
        "propose",
        help="Map findings to improvement proposals and rank them.",
    )
    si_prop.add_argument("--json", action="store_true", help="Emit JSON bundle")
    si_plan = self_improve_sub.add_parser(
        "plan",
        help="Build full plan and write runs/self_improvement/plan_latest.{json,md}.",
    )
    si_plan.add_argument("--json", action="store_true", help="Print plan JSON to stdout")
    
    # --- confidence (decision context: evidence quality, uncertainty, risk — not emotions) ---
    conf_p = sub.add_parser(
        "confidence",
        help="Assess decision confidence, uncertainty, risk, and escalation pressure from local artifacts.",
    )
    conf_sub = conf_p.add_subparsers(dest="confidence_command", required=True)
    conf_assess = conf_sub.add_parser(
        "assess",
        help="Compute assessment for one product or all valid products",
    )
    conf_assess.add_argument(
        "product_id",
        nargs="?",
        default=None,
        metavar="PRODUCT_ID",
        help="Optional; omit to assess every valid product",
    )
    conf_assess.add_argument("--json", action="store_true", help="Emit assessment JSON")
    conf_assess.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/decision_assessment/latest/<id>.json",
    )
    add_products_dir(conf_assess)
    
    conf_summary = conf_sub.add_parser(
        "summary",
        help="One line per product: confidence, uncertainty, risk, escalation pressure",
    )
    conf_summary.add_argument("--json", action="store_true")
    add_products_dir(conf_summary)
    
    conf_explain = conf_sub.add_parser(
        "explain",
        help="Print rationale and top contributing factors for one product",
    )
    conf_explain.add_argument("product_id", metavar="PRODUCT_ID")
    conf_explain.add_argument("--json", action="store_true")
    add_products_dir(conf_explain)
    
    # --- advisors (multi-archetype scaffold; deterministic stubs) ---
    adv = sub.add_parser(
        "advisors",
        help="Consult advisor archetypes (stub responses; ready for future LLM integration).",
    )
    adv_sub = adv.add_subparsers(dest="advisors_command", required=True)
    adv_list = adv_sub.add_parser("list", help="List global or product-resolved advisor definitions")
    adv_list.add_argument(
        "--product-id",
        dest="list_product_id",
        default=None,
        metavar="ID",
        help="Resolve per-product advisors.json overrides",
    )
    adv_list.add_argument("--json", action="store_true")
    adv_run = adv_sub.add_parser("run", help="Run all advisors for one product (stub or LLM)")
    adv_run.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    adv_run.add_argument("--json", action="store_true")
    adv_cons = adv_sub.add_parser(
        "consensus",
        help="Run advisors and compute consensus + disagreement (writes runs/advisors/<id>.latest.json)",
    )
    adv_cons.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    adv_cons.add_argument("--json", action="store_true")
    adv_consult = adv_sub.add_parser(
        "consult",
        help=(
            "Full board consultation: structured prompts, optional OpenAI-compatible LLM, "
            "consensus, local prompt/response logs under runs/advisors/consultations/"
        ),
    )
    adv_consult.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    adv_consult.add_argument("--json", action="store_true")
    adv_consult.add_argument(
        "--stub-only",
        action="store_true",
        help="Never call an LLM (deterministic stubs only)",
    )
    adv_consult.add_argument(
        "--llm",
        action="store_true",
        help="Force LLM calls when ARGUS_OPENAI_API_KEY is set (overrides auto-detect)",
    )
    
