from __future__ import annotations

from pathlib import Path

from argus.cli.parser_common import add_products_dir


def register_findings_chain_commands(sub) -> None:
    # --- findings ---
    fin = sub.add_parser(
        "findings",
        help="Generate and inspect findings from signals (rules → runs/findings/).",
    )
    fin_sub = fin.add_subparsers(dest="findings_command", required=True)
    
    gen_f = fin_sub.add_parser(
        "generate",
        help="Run finding rules on signals (all valid products, or one id)",
    )
    gen_f.add_argument(
        "product_id",
        nargs="?",
        default=None,
        metavar="PRODUCT_ID",
        help="Optional product id; if omitted, generate for every valid inventory product",
    )
    gen_f.add_argument("--json", action="store_true")
    gen_f.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/findings/latest/ or generations/",
    )
    add_products_dir(gen_f)
    gen_f.add_argument(
        "--fresh-signals",
        action="store_true",
        help="Always run signal collection; default is to use runs/signals/latest if present",
    )
    
    show_f = fin_sub.add_parser("show", help="Show last persisted findings for a product")
    show_f.add_argument("product_id", help="Product id")
    show_f.add_argument("--json", action="store_true")
    
    sum_f = fin_sub.add_parser(
        "summary",
        help="Summarize latest findings across products",
    )
    sum_f.add_argument("--json", action="store_true")
    
    # --- decisions ---
    dec = sub.add_parser(
        "decisions",
        help="Lifecycle-weighted decision candidates and cross-product portfolio ranking.",
    )
    dec_sub = dec.add_subparsers(dest="decisions_command", required=True)
    
    gen_d = dec_sub.add_parser(
        "generate",
        help="Generate lifecycle scores + ranked candidates from latest findings",
    )
    gen_d.add_argument(
        "product_id",
        nargs="?",
        default=None,
        metavar="PRODUCT_ID",
        help="Optional product id; if omitted, all valid products with findings",
    )
    gen_d.add_argument("--json", action="store_true")
    gen_d.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/decisions/latest/",
    )
    add_products_dir(gen_d)
    
    port_d = dec_sub.add_parser(
        "portfolio",
        help="Cross-product ranked recommendations (reads/regenerates from latest findings)",
    )
    port_d.add_argument("--json", action="store_true")
    port_d.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write portfolio report to runs/decisions/",
    )
    add_products_dir(port_d)
    
    show_d = dec_sub.add_parser(
        "show",
        help="Show last persisted decision bundle for a product",
    )
    show_d.add_argument("product_id", help="Product id")
    show_d.add_argument("--json", action="store_true")
    
    hist_dec = dec_sub.add_parser(
        "history",
        help="List decision generations under runs/decisions/generations/ (oldest first per product)",
    )
    hist_dec.add_argument(
        "product_id",
        nargs="?",
        default=None,
        metavar="PRODUCT_ID",
        help="Optional; omit to list counts for every product with generation files",
    )
    hist_dec.add_argument("--json", action="store_true")
    
    churn_dec = dec_sub.add_parser(
        "churn",
        help="Churn and stability analysis from decision history (no execution)",
    )
    churn_dec.add_argument(
        "product_id",
        nargs="?",
        default=None,
        metavar="PRODUCT_ID",
        help="Optional; omit to analyze every product with history",
    )
    churn_dec.add_argument("--json", action="store_true")
    
    # --- lifecycle ---
    life = sub.add_parser(
        "lifecycle",
        help="Inspect lifecycle assessment (scores, kill_candidate) without running the full pipeline.",
    )
    life_sub = life.add_subparsers(dest="lifecycle_command", required=True)
    life_show = life_sub.add_parser(
        "show",
        help="Show lifecycle for one product (saved decisions, else compute from latest findings)",
    )
    life_show.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    life_show.add_argument("--json", action="store_true")
    add_products_dir(life_show)
    life_kill = life_sub.add_parser(
        "kill-score",
        help="Kill criteria score (0–100) and continue/hold/deprecate/kill from local data",
    )
    life_kill.add_argument(
        "product_id",
        nargs="?",
        default=None,
        metavar="PRODUCT_ID",
        help="Optional; omit to score every valid product",
    )
    life_kill.add_argument("--json", action="store_true", help="Machine-readable output")
    add_products_dir(life_kill)
    life_rep = life_sub.add_parser(
        "report",
        help="Kill justification report (Markdown + optional JSON) from local signals, findings, economics, decisions",
    )
    life_rep.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    life_rep.add_argument(
        "--json",
        action="store_true",
        help="Emit structured JSON report (default: Markdown on stdout)",
    )
    life_rep.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="PATH",
        help="Also write Markdown to this file or directory (default: stdout only)",
    )
    add_products_dir(life_rep)
    
    # --- portfolio (end-to-end) ---
    port = sub.add_parser(
        "portfolio",
        help="Portfolio-level reports and one-shot refresh of the full local pipeline.",
    )
    port_sub = port.add_subparsers(dest="portfolio_command", required=True)
    
    ref_p = port_sub.add_parser(
        "refresh",
        help="Validate products → collect signals → findings → decisions → portfolio report",
    )
    ref_p.add_argument(
        "--per-product",
        action="store_true",
        help="Also write runs/portfolio/latest/products/<id>.txt per product",
    )
    ref_p.add_argument(
        "--no-save",
        action="store_true",
        help="Compute only; do not write runs/ artifacts",
    )
    ref_p.add_argument("--json", action="store_true", help="Emit refresh payload JSON")
    add_products_dir(ref_p)
    
    show_pf = port_sub.add_parser(
        "show",
        help="Print the last portfolio summary from runs/portfolio/latest/",
    )
    show_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit refresh.json contents if present, else summary text wrapped in JSON",
    )
    
    alloc_pf = port_sub.add_parser(
        "allocate",
        help="Recommend % focus per product (ignore vs push) from metrics, economics, trends, experiments",
    )
    alloc_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit portfolio allocation JSON (schema argus.portfolio_allocation.v1)",
    )
    add_products_dir(alloc_pf)

    oq_pf = port_sub.add_parser(
        "operator-queue",
        help="Rank products for operator attention from operator snapshots / orchestration state",
    )
    oq_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit operator queue JSON (schema argus.operator_queue.v1) to stdout",
    )
    oq_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/portfolio/operator_queue/latest.{json,md}",
    )
    add_products_dir(oq_pf)

    ba_pf = port_sub.add_parser(
        "builder-activity",
        help=(
            "Emit portfolio Builder activity rollup (invoke/reconcile truth; coordination only; "
            "writes runs/portfolio/builder_activity/latest.json)"
        ),
    )
    ba_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.portfolio_builder_activity.v1 JSON to stdout",
    )
    ba_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/portfolio/builder_activity/latest.{json,md} or stamped JSON",
    )
    add_products_dir(ba_pf)

    prog_pf = port_sub.add_parser(
        "progress",
        help="One bounded advance_orchestration step per top-ranked product (from operator queue)",
    )
    prog_pf.add_argument(
        "--limit",
        type=int,
        default=5,
        metavar="N",
        help="Max products to touch (default: 5)",
    )
    prog_pf.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview advancement only; no advancement files or orchestration writes",
    )
    prog_pf.add_argument(
        "--no-execute",
        action="store_true",
        help="Record advancement intent only (same as orchestration advance without --execute)",
    )
    prog_pf.add_argument(
        "--skip-import-failed",
        action="store_true",
        help="Skip products with failed importer first-pass / gating tier failed",
    )
    prog_pf.add_argument(
        "--skip-waiting",
        action="store_true",
        help="Skip products with blocked_waiting_input or blocked_waiting_approval headline status",
    )
    prog_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/portfolio/progression/* artifacts",
    )
    prog_pf.add_argument("--json", action="store_true", help="Emit progression JSON to stdout")
    add_products_dir(prog_pf)

    quies_pf = port_sub.add_parser(
        "quiescence",
        help="Compare operator queue, progression, and snapshots vs prior run — quiescence + change summary",
    )
    quies_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit portfolio quiescence JSON (schema argus.portfolio_quiescence.v1) to stdout",
    )
    quies_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/portfolio/quiescence/* artifacts",
    )

    delta_pf = port_sub.add_parser(
        "delta-report",
        help="Portfolio delta vs prior baseline (quiescence or last delta report) — JSON + Markdown",
    )
    delta_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit portfolio delta report JSON (schema argus.portfolio_delta_report.v1) to stdout",
    )
    delta_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/portfolio/delta_report/* artifacts",
    )

    int_pf = port_sub.add_parser(
        "intervention",
        help="Detect stuck loops / intervention needs from queue, snapshots, progression, quiescence, delta history",
    )
    int_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit portfolio intervention JSON (schema argus.portfolio_intervention.v1) to stdout",
    )
    int_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/portfolio/intervention/* artifacts",
    )

    inbox_pf = port_sub.add_parser(
        "intervention-inbox",
        help="Build operator inbox from latest intervention report + local ack/snooze state (file-based)",
    )
    inbox_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.intervention_inbox.v1 JSON to stdout",
    )
    inbox_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/portfolio/intervention_inbox/latest.{json,md}",
    )
    inbox_pf.add_argument(
        "--recent-limit",
        type=int,
        default=15,
        metavar="N",
        help="How many stamped intervention JSONs to scan for chronicity / recurrence (default: 15)",
    )

    def _inbox_action(name: str, help_text: str):
        p = port_sub.add_parser(name, help=help_text)
        p.add_argument("--item-id", dest="item_id", required=True, metavar="ID", help="Inbox item id (inv-…)")
        p.add_argument("--note", default=None, help="Optional operator note stored in the action record")
        p.add_argument(
            "--json",
            action="store_true",
            help="Print updated inbox JSON after the action",
        )
        p.add_argument(
            "--no-save",
            action="store_true",
            help="Append action only; do not rewrite runs/portfolio/intervention_inbox/latest.{json,md}",
        )
        return p

    _inbox_action(
        "intervention-ack",
        "Record acknowledge for an inbox item (append-only action under intervention_inbox/actions/)",
    )
    _inbox_action(
        "intervention-resolve",
        "Mark resolved (fingerprinted; reopens if the same detection persists or changes materially)",
    )
    _inbox_action(
        "intervention-ignore",
        "Mark ignored (same reopen semantics as resolve)",
    )
    sn_pf = _inbox_action(
        "intervention-snooze",
        "Snooze an item until UTC now + N days (hidden from active queue until due)",
    )
    sn_pf.add_argument(
        "--days",
        type=float,
        default=7.0,
        metavar="N",
        help="Snooze duration in days (default: 7)",
    )
    _inbox_action(
        "intervention-escalate",
        "Record escalation marker for an inbox item",
    )

    esc_pf = port_sub.add_parser(
        "escalation-inbox",
        help="Autonomy boundary inbox: human-required issues from autonomous runs, blocked promotions, scheduler/cycle stops, intervention (subset)",
    )
    esc_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.escalation_inbox.v1 JSON to stdout",
    )
    esc_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/portfolio/escalation_inbox/latest.{json,md}",
    )
    esc_pf.add_argument(
        "--recent-session-limit",
        type=int,
        default=12,
        metavar="N",
        help="How many stamped autonomous runner JSONs to scan for recurrence (default: 12)",
    )

    def _escalation_action(name: str, help_text: str, *, snooze: bool = False):
        p = port_sub.add_parser(name, help=help_text)
        p.add_argument("--item-id", dest="item_id", required=True, metavar="ID", help="Escalation item id (esc-…)")
        p.add_argument("--note", default=None, help="Optional operator note stored in the action record")
        p.add_argument(
            "--reason",
            default=None,
            metavar="CODE",
            help="Optional reason code (only used by escalation-resolve; stored in the action note, e.g. condition_no_longer_true)",
        )
        p.add_argument(
            "--json",
            action="store_true",
            help="Print updated escalation inbox JSON after the action",
        )
        p.add_argument(
            "--no-save",
            action="store_true",
            help="Append action only; do not rewrite runs/portfolio/escalation_inbox/latest.{json,md}",
        )
        if snooze:
            p.add_argument(
                "--days",
                type=float,
                default=7.0,
                metavar="N",
                help="Snooze duration in days (default: 7)",
            )
        return p

    _escalation_action(
        "escalation-ack",
        "Acknowledge an escalation inbox item (append-only under escalation_inbox/actions/)",
    )
    _escalation_action(
        "escalation-resolve",
        "Resolve an escalation item (fingerprinted; reopens if the underlying signal persists)",
    )
    _escalation_action(
        "escalation-snooze",
        "Snooze an escalation item until UTC now + N days",
        snooze=True,
    )
    _escalation_action(
        "escalation-escalate",
        "Mark an escalation item as priority-escalated for visibility",
    )

    cycle_pf = port_sub.add_parser(
        "cycle",
        help="One bounded operator cycle: queue → progress → quiescence → delta → intervention + summary",
    )
    cycle_pf.add_argument(
        "--limit",
        type=int,
        default=5,
        metavar="N",
        help="Max products for portfolio progression (default: 5)",
    )
    cycle_pf.add_argument(
        "--dry-run",
        action="store_true",
        help="Progression previews advances only; no orchestration advancement writes",
    )
    cycle_pf.add_argument(
        "--skip-import-failed",
        action="store_true",
        help="Skip progression rows for failed importer / gating tier failed",
    )
    cycle_pf.add_argument(
        "--skip-waiting",
        action="store_true",
        help="Skip progression rows blocked on waiting input/approval",
    )
    cycle_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit portfolio cycle JSON (schema argus.portfolio_cycle.v1) to stdout",
    )
    cycle_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/portfolio/cycle/* artifacts (sub-stages still write their outputs)",
    )
    cycle_pf.add_argument(
        "--artifact-coherence",
        action="store_true",
        help="After the cycle, write runs/debug/artifact_coherence/* (read-only coherence audit)",
    )
    add_products_dir(cycle_pf)

    coh_pf = port_sub.add_parser(
        "artifact-coherence",
        help="Verify alignment of canonical runs/ artifacts (outcomes, intervention, strategy inputs); optional debug report",
    )
    coh_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.artifact_coherence_report.v1 JSON to stdout",
    )
    coh_pf.add_argument(
        "--strict",
        action="store_true",
        help="Exit with non-zero status unless overall coherence is `valid` (same as --strict-mode all)",
    )
    coh_pf.add_argument(
        "--strict-mode",
        choices=["all", "invalid-only"],
        default=None,
        help="Exit policy when set (or with --strict): all=require valid; invalid-only=fail only on invalid",
    )
    coh_pf.add_argument(
        "--no-write-artifact",
        action="store_true",
        help="Evaluate only; do not write runs/debug/artifact_coherence/*",
    )
    add_products_dir(coh_pf)

    sched_pf = port_sub.add_parser(
        "schedule",
        help="Bounded repeated portfolio cycles with quiescence/intervention guardrails (session under runs/portfolio/scheduler/)",
    )
    sched_pf.add_argument(
        "--max-cycles",
        type=int,
        default=5,
        metavar="N",
        help="Hard cap on portfolio cycle iterations (default: 5)",
    )
    sched_pf.add_argument(
        "--limit-per-cycle",
        type=int,
        default=5,
        metavar="N",
        help="Max products per cycle progression (default: 5)",
    )
    sched_pf.add_argument(
        "--dry-run",
        action="store_true",
        help="Progression previews only; no orchestration advancement writes inside each cycle",
    )
    sched_pf.add_argument(
        "--skip-import-failed",
        action="store_true",
        help="Skip progression rows for failed importer / gating tier failed",
    )
    sched_pf.add_argument(
        "--skip-waiting",
        action="store_true",
        help="Skip progression rows blocked on waiting input/approval",
    )
    sched_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit portfolio scheduler session JSON (schema argus.portfolio_scheduler_session.v1) to stdout",
    )
    sched_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/portfolio/scheduler/* (each cycle still persists stage artifacts unless disabled elsewhere)",
    )
    add_products_dir(sched_pf)

    auto_pf = port_sub.add_parser(
        "run-autonomous",
        help="Bounded autonomous session: refresh → cycle → lifecycle → operator summary → narrative "
        "(runs/portfolio/autonomous_runner/)",
    )
    auto_pf.add_argument(
        "--max-cycles",
        type=int,
        default=5,
        metavar="N",
        help="Hard cap on iterations (default: 5)",
    )
    auto_pf.add_argument(
        "--limit-per-cycle",
        type=int,
        default=5,
        metavar="N",
        help="Max products per cycle progression (default: 5)",
    )
    auto_pf.add_argument(
        "--limit-history",
        type=int,
        default=30,
        metavar="N",
        help="History window for operator summary and narrative (default: 30)",
    )
    auto_pf.add_argument(
        "--dry-run",
        action="store_true",
        help="No filesystem writes from pipeline stages (refresh/cycle/lifecycle/dashboard); no promotion_actions/ JSON; session log still written unless --no-save",
    )
    auto_pf.add_argument(
        "--skip-import-failed",
        action="store_true",
        help="Skip progression rows for failed importer / gating tier failed",
    )
    auto_pf.add_argument(
        "--skip-waiting",
        action="store_true",
        help="Skip progression rows blocked on waiting input/approval",
    )
    auto_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit autonomous runner session JSON (schema argus.portfolio_autonomous_runner.v1) to stdout",
    )
    auto_pf.add_argument(
        "--no-save",
        action="store_true",
        help="No disk writes: no session log, no stage artifacts, no runs/products/promotion_actions/ (same as dry-run for stages)",
    )
    auto_pf.add_argument(
        "--allow-promotion",
        action="store_true",
        help="After the session, run bounded safe lifecycle promotions (creation scaffold, optional bootstrap, deprecation plan) via argus.products.promotion",
    )
    auto_pf.add_argument(
        "--promotion-bootstrap",
        action="store_true",
        help="With --allow-promotion: chain creation bootstrap after scaffold when not dry-run, or run one standalone bootstrap if no creation promotion applies",
    )
    add_products_dir(auto_pf)

    svc_pf = port_sub.add_parser(
        "run-service",
        help="Cadence wrapper for run-autonomous: heartbeat under runs/portfolio/runner_service/ (bounded loop, stop sentinel)",
    )
    svc_pf.add_argument(
        "--interval-seconds",
        type=float,
        default=0.0,
        metavar="SEC",
        help="Sleep between autonomous sessions; 0 = one-shot (single session, default)",
    )
    svc_pf.add_argument(
        "--max-runs",
        type=int,
        default=1,
        metavar="N",
        help="Max autonomous sessions per service invocation (default: 1; use >1 with --interval-seconds)",
    )
    svc_pf.add_argument(
        "--max-cycles-per-run",
        type=int,
        default=5,
        metavar="N",
        dest="max_cycles",
        help="Per-session max_cycles passed to run-autonomous (default: 5)",
    )
    svc_pf.add_argument(
        "--limit-per-cycle",
        type=int,
        default=5,
        metavar="N",
        help="Per-session limit-per-cycle (default: 5)",
    )
    svc_pf.add_argument(
        "--limit-history",
        type=int,
        default=30,
        metavar="N",
        help="History window for operator summary/narrative (default: 30)",
    )
    svc_pf.add_argument(
        "--dry-run",
        action="store_true",
        help="Passed to run-autonomous: no stage writes; session JSON still written unless --no-save",
    )
    svc_pf.add_argument(
        "--skip-import-failed",
        action="store_true",
        help="Passed to run-autonomous",
    )
    svc_pf.add_argument(
        "--skip-waiting",
        action="store_true",
        help="Passed to run-autonomous",
    )
    svc_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit portfolio runner service JSON (schema argus.portfolio_runner_service.v1) to stdout",
    )
    svc_pf.add_argument(
        "--no-save",
        action="store_true",
        help="No disk writes: autonomous stages/session, promotion_actions, and runner_service heartbeat",
    )
    svc_pf.add_argument(
        "--allow-promotion",
        action="store_true",
        help="Passed to run-autonomous",
    )
    svc_pf.add_argument(
        "--promotion-bootstrap",
        action="store_true",
        help="Passed to run-autonomous",
    )
    svc_pf.add_argument(
        "--include-autonomous-session",
        action="store_true",
        help="Include last full autonomous session payload on stdout JSON (large)",
    )
    add_products_dir(svc_pf)

    hist_pf = port_sub.add_parser(
        "history",
        help="Portfolio artifact history spine + trend summaries (stamped runs under runs/portfolio/)",
    )
    hist_pf.add_argument(
        "--limit-history",
        type=int,
        default=50,
        metavar="N",
        help="Max stamped artifacts per type to load, and max points per per-product series (default: 50)",
    )
    hist_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit portfolio history JSON (schema argus.portfolio_history.v1) to stdout",
    )
    hist_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/portfolio/history/* artifacts",
    )

    replay_pf = port_sub.add_parser(
        "replay",
        help="Read-only forensic replay of last portfolio cycle artifacts (no pipeline re-run)",
    )
    replay_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.portfolio_replay.v1 JSON to stdout",
    )
    replay_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/portfolio/replay/* artifacts",
    )

    out_pf = port_sub.add_parser(
        "outcomes",
        help="Measure whether recent portfolio cycles improved per-product state (delta/quiescence/progression/intervention)",
    )
    out_pf.add_argument(
        "--limit-history",
        type=int,
        default=50,
        metavar="N",
        help="Max stamped artifacts per type to load (default: 50)",
    )
    out_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.portfolio_outcomes.v1 JSON to stdout",
    )
    out_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/portfolio/outcomes/* artifacts",
    )

    pat_pf = port_sub.add_parser(
        "patterns",
        help="Cross-product pattern detection (shared import, intervention, stagnation, shape clusters)",
    )
    pat_pf.add_argument(
        "--limit-history",
        type=int,
        default=50,
        metavar="N",
        help="Max stamped artifacts per type for outcomes evaluation (default: 50)",
    )
    pat_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.portfolio_patterns.v1 JSON to stdout",
    )
    pat_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/portfolio/patterns/latest.{json,md}",
    )
    add_products_dir(pat_pf)

    strat_pf = port_sub.add_parser(
        "strategy",
        help="Derive whole-portfolio strategic posture (argus.portfolio_strategy.v1)",
    )
    strat_pf.add_argument(
        "--limit-history",
        type=int,
        default=50,
        metavar="N",
        help="Max history for outcomes/patterns/policy inputs (default: 50)",
    )
    strat_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.portfolio_strategy.v1 JSON to stdout",
    )
    strat_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/portfolio/strategy/latest.{json,md}",
    )
    add_products_dir(strat_pf)

    life_pf = port_sub.add_parser(
        "lifecycle",
        help="Portfolio-wide lifecycle positions and transitions (inventory, creation, deprecation, outcomes, strategy)",
    )
    life_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.portfolio_lifecycle.v1 JSON to stdout",
    )
    life_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/portfolio/lifecycle/latest.{json,md}",
    )
    add_products_dir(life_pf)

    am_pf = port_sub.add_parser(
        "autonomy-memory",
        help="Cross-session autonomy memory from stamped autonomous sessions (argus.portfolio_autonomy_memory.v1)",
    )
    am_pf.add_argument(
        "--limit-history",
        type=int,
        default=50,
        metavar="N",
        help="Max stamped autonomous runner session JSON files to scan (default: 50)",
    )
    am_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.portfolio_autonomy_memory.v1 JSON to stdout",
    )
    am_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/portfolio/autonomy_memory/latest.{json,md}",
    )
    add_products_dir(am_pf)

    rq_pf = port_sub.add_parser(
        "runner-quality",
        help="Debug runner quality metrics over stamped autonomous sessions (argus.debug_runner_quality.v1)",
    )
    rq_pf.add_argument(
        "--limit-history",
        type=int,
        default=50,
        metavar="N",
        help="Max stamped autonomous runner session JSON files to scan (default: 50)",
    )
    rq_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.debug_runner_quality.v1 JSON to stdout",
    )
    rq_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/debug/runner_quality/latest.{json,md}",
    )

    cu_pf = port_sub.add_parser(
        "chronic-unblock",
        help="Chronic product blockage map (queue + intervention + cycle) (argus.debug_chronic_portfolio_unblock.v1)",
    )
    cu_pf.add_argument(
        "--product",
        action="append",
        dest="chronic_products",
        metavar="PRODUCT_ID",
        help="Limit to product id(s); repeatable. Default: intervention flags + top queue ranks.",
    )
    cu_pf.add_argument(
        "--queue-rank-cap",
        type=int,
        default=8,
        metavar="N",
        help="Include queue entries with rank <= N when auto-selecting products (default: 8)",
    )
    cu_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.debug_chronic_portfolio_unblock.v1 JSON to stdout",
    )
    cu_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/debug/chronic_portfolio_unblock/latest.{json,md}",
    )

    twf_pf = port_sub.add_parser(
        "temporal-worst-freshness",
        help="Debug manifest vs collected temporal worst-freshness (argus.debug_temporal_worst_freshness.v1)",
    )
    twf_pf.add_argument(
        "--product",
        action="append",
        dest="twf_products",
        metavar="PRODUCT_ID",
        help="Product id (repeatable; required — at least one)",
    )
    twf_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON to stdout",
    )
    twf_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/debug/temporal_worst_freshness/latest.{json,md}",
    )

    og_pf = port_sub.add_parser(
        "onboard-github",
        help="Clone a github.com repo into products/<id>/app, admit manifest, bounded bootstrap (argus.portfolio_github_onboarding.v1)",
    )
    og_pf.add_argument(
        "github_url",
        metavar="URL",
        help="HTTPS or SSH: https://github.com/<owner>/<repo> or git@github.com:<owner>/<repo>",
    )
    og_pf.add_argument(
        "--product-id",
        dest="onboard_product_id",
        default=None,
        metavar="SLUG",
        help="Product id (default: normalized repo name)",
    )
    og_pf.add_argument(
        "--branch",
        default=None,
        metavar="NAME",
        help="Git branch to clone (default: remote default branch)",
    )
    og_pf.add_argument(
        "--template",
        dest="template_type",
        default="micro_saas",
        choices=("content_stream", "micro_saas", "static_site", "utility_api", "mobile_companion"),
        help="Scaffold template (default: micro_saas)",
    )
    og_pf.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate URL and inputs only; no scaffold, git, or bootstrap",
    )
    og_pf.add_argument(
        "--no-bootstrap",
        action="store_true",
        help="After admit, skip signals/findings bootstrap (still refreshes temporal/orchestration when possible)",
    )
    og_pf.add_argument(
        "--full-bootstrap",
        action="store_true",
        help="Run full creation bootstrap (decisions+ideas); default is minimal (signals+findings)",
    )
    og_pf.add_argument(
        "--force",
        action="store_true",
        help="Replace existing products/<id> directory",
    )
    og_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.portfolio_github_onboarding.v1 JSON to stdout",
    )
    og_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/products/github_onboarding/latest.{json,md}",
    )
    add_products_dir(og_pf)

    pr_pf = port_sub.add_parser(
        "product-readiness",
        help="Debug: orchestration + queue + evidence maturity for one product (argus.product_readiness_debug.v1)",
    )
    pr_pf.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    pr_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON to stdout",
    )
    pr_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/debug/product_readiness/latest.{json,md}",
    )
    add_products_dir(pr_pf)

    sc_pf = port_sub.add_parser(
        "signal-contract",
        help="Evaluate golden vs mission signal contract for one product (argus.signal_contract_evaluation.v1)",
    )
    sc_pf.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    sc_pf.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON to stdout",
    )
    sc_pf.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write runs/debug/signal_contract/<product_id>/latest.{json,md}",
    )

    # --- doctrine (product policy YAML) ---
    doctrine = sub.add_parser(
        "doctrine",
        help="Load machine-readable doctrine from products/<id>/doctrine.yaml.",
    )
    doctrine_sub = doctrine.add_subparsers(dest="doctrine_command", required=True)
    doc_show = doctrine_sub.add_parser(
        "show",
        help="Print parsed doctrine for a product (empty if no doctrine.yaml)",
    )
    doc_show.add_argument("product_id", metavar="PRODUCT_ID", help="Product id")
    doc_show.add_argument(
        "--json",
        action="store_true",
        help="Emit doctrine JSON (schema argus.doctrine.v1 fields)",
    )
    add_products_dir(doc_show)
    
