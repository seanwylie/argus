from __future__ import annotations


def register_audit_commands(sub) -> None:
    # --- audit (deterministic product capability MVP) ---
    aud = sub.add_parser(
        "audit",
        help=(
            "Scoped product audit under runs/audit/: deterministic bundle + legacy Product Gap. "
            "Optional Cursor JSON (prompt/ingest) adds cursor_scan — Argus does not call LLM APIs."
        ),
    )
    aud_sub = aud.add_subparsers(dest="audit_command", required=True)
    aud_run = aud_sub.add_parser(
        "run",
        help="Deterministic angle runners; preserves existing bundle cursor_scan on re-run",
    )
    aud_run.add_argument("--product-id", dest="product_id", default=None, metavar="PRODUCT_ID")
    aud_run.add_argument(
        "--all",
        action="store_true",
        dest="all_products",
        help="Audit every valid inventory product",
    )
    aud_run.add_argument("--json", action="store_true")
    aud_show = aud_sub.add_parser(
        "show",
        help="Show bundle.json, or --cursor-scan for Cursor layer only, or --merged-text for humans",
    )
    aud_show.add_argument("product_id", metavar="PRODUCT_ID")
    aud_show.add_argument(
        "--legacy-product-gap",
        action="store_true",
        dest="legacy_product_gap",
        help="Print argus.audit_summary.v1 only (runs/audit/<id>/latest.json)",
    )
    aud_show.add_argument(
        "--merged-text",
        action="store_true",
        dest="merged_text",
        help="Human-readable per-angle summary_lines and cursor_scan flags (not JSON)",
    )
    aud_show.add_argument(
        "--cursor-scan",
        action="store_true",
        dest="cursor_scan",
        help="JSON: only bundle.angles.<id>.cursor_scan per angle (argus.audit_cursor_scan_view.v1)",
    )
    aud_show.add_argument("--json", action="store_true")
    aud_list = aud_sub.add_parser("list", help="List product ids with runs/audit/<id>/latest.json")
    aud_list.add_argument("--json", action="store_true")
    aud_sum = aud_sub.add_parser("summary", help="Summarize all product audits")
    aud_sum.add_argument("--json", action="store_true")
    aud_prompt = aud_sub.add_parser(
        "prompt",
        help="Print a Cursor codebase scan prompt (all nine angles by default, or one angle via --angle)",
    )
    aud_prompt.add_argument("--product-id", dest="product_id", required=True, metavar="PRODUCT_ID")
    aud_prompt.add_argument(
        "--angle",
        default=None,
        metavar="ANGLE_ID",
        help="Single angle: angle-specific prompt and argus.audit_cursor_scan.v1 JSON (mutually exclusive with --angles)",
    )
    aud_prompt.add_argument(
        "--angles",
        default=None,
        help="Comma-separated angle ids for batch prompt (default: all nine); not with --angle",
    )
    aud_cursor_prompt = aud_sub.add_parser(
        "cursor-prompt",
        help="Alias for `audit prompt` — batch (argus.audit_cursor_scan_batch.v1) or single-angle (argus.audit_cursor_scan.v1)",
    )
    aud_cursor_prompt.add_argument("--product-id", dest="product_id", required=True, metavar="PRODUCT_ID")
    aud_cursor_prompt.add_argument(
        "--angle",
        default=None,
        metavar="ANGLE_ID",
        help="Single angle: angle-specific prompt and argus.audit_cursor_scan.v1 JSON (mutually exclusive with --angles)",
    )
    aud_cursor_prompt.add_argument(
        "--angles",
        default=None,
        help="Comma-separated angle ids for batch prompt (default: all nine); not with --angle",
    )
    aud_ingest = aud_sub.add_parser(
        "ingest-agent",
        help="Merge agent JSON into bundle.json (requires prior audit run; preserved on next audit run)",
    )
    aud_ingest.add_argument("--product-id", dest="product_id", required=True, metavar="PRODUCT_ID")
    aud_ingest.add_argument(
        "--file",
        dest="file",
        required=True,
        metavar="PATH",
        help="Path to agent JSON (batch or single-angle)",
    )
    aud_ingest.add_argument(
        "--angle",
        dest="angle",
        default=None,
        metavar="ANGLE_ID",
        help="Treat file as one angle payload for this id (legacy or cursor_scan)",
    )
    aud_ingest.add_argument(
        "--no-overwrite",
        action="store_true",
        dest="no_overwrite",
        help="Skip angles that already have a cursor_scan",
    )
    aud_ingest.add_argument("--json", action="store_true", help="Print merged bundle JSON")
    
    aud_ingest_cursor = aud_sub.add_parser(
        "ingest-cursor",
        help="Same as ingest-agent — merge argus.audit_cursor_scan JSON from a file into bundle.json",
    )
    aud_ingest_cursor.add_argument("--product-id", dest="product_id", required=True, metavar="PRODUCT_ID")
    aud_ingest_cursor.add_argument(
        "--file",
        dest="file",
        required=True,
        metavar="PATH",
        help="Path to JSON (batch or single-angle cursor_scan contract)",
    )
    aud_ingest_cursor.add_argument(
        "--angle",
        dest="angle",
        default=None,
        metavar="ANGLE_ID",
        help="Treat file as one angle payload for this id",
    )
    aud_ingest_cursor.add_argument(
        "--no-overwrite",
        action="store_true",
        dest="no_overwrite",
        help="Skip angles that already have a cursor_scan",
    )
    aud_ingest_cursor.add_argument("--json", action="store_true", help="Print merged bundle JSON")
    
