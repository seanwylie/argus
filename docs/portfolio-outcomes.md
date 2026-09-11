# Portfolio outcomes

**Schema:** `argus.portfolio_outcomes.v1`

**CLI:** `argus portfolio outcomes` (optional `--json`, `--no-save`, `--limit-history N`)

This layer answers: **did recent portfolio cycles actually improve product state?** It is fully **deterministic** and reads only existing artifacts under `runs/portfolio/` (and uses policy materiality thresholds from `config/operator_policy.yaml` merged with code defaults).

## What it compares

Per product, Argus builds a **time-ordered series** of per-product fingerprints from:

1. **Stamped delta reports** — `baseline_for_next_run.per_product` (preferred; same shape as [portfolio-delta-report.md](portfolio-delta-report.md)).
2. **Stamped quiescence** — fills run ids missing from delta (same merge rule as [portfolio-history.md](portfolio-history.md): delta wins on duplicate run ids).

The **first vs last** snapshot in the window drives trajectory labels. Additionally:

- **Progression** — blocked outcome counts / streaks from stamped progression JSONs.
- **Intervention** — whether a product was flagged, stayed flagged, or cleared across stamped intervention runs.

Optional context: **latest cycle** `summary.overall_operator_recommendation` from the newest stamped cycle artifact (if any).

## Per-product metrics

| Field | Meaning |
|-------|---------|
| `readiness_trajectory` | `improved` / `unchanged` / `regressed` / `unknown` (readiness tier ladder) |
| `understanding_debt_trajectory` | `decreased` / `flat` / `increased` / `unknown` (vs `quiescence.debt_delta_material`) |
| `queue_rank_trajectory` | `improved` / `worsened` / `flat` / `unknown` (lower rank = higher priority) |
| `next_action_pattern` | `resolved` / `repeated` / `changed` / `unknown` |
| `blocked_pattern` | `cleared` / `persisted` / `never_blocked` / `newly_blocked` / `unknown` |
| `import_health_trajectory` | `recovered` / `stayed_bad` / `unchanged_ok` / `regressed` / `unknown` |
| `decision_confidence_trajectory` | `improved` / `worsened` / `flat` / … (vs `quiescence.confidence_delta_material`) |
| `intervention_pattern` | `resolved` / `repeated` / `newly_flagged` / `clear` / `changed` / `unknown` |
| `overall_trajectory` | `positive` / `negative` / `mixed` / `no_meaningful_movement` (score of supporting signals) |

## Mission-aware interpretation (additive)

Each per-product row includes **`mission_interpretation`**, schema **`argus.portfolio_outcome_mission_interpretation.v1`**. This is an **interpretation layer only**: it does not change trajectory labels, reason codes, or any upstream metrics.

**Inputs:** Structured mission for the product (objective, drivers, guardrails) from `resolve_product_mission` — same source as other mission-conditioned behavior. **Signals:** Only the existing outcome trajectories and deltas already present on the row (readiness, debt, rank, blocked pattern, confidence, import health, overall trajectory, intervention pattern).

**Fields (high level):**

| Field | Meaning |
|-------|---------|
| `mission_objective` / `mission_drivers` / `mission_guardrails` | Echo of resolved mission for transparency |
| `mission_alignment` | `positive` / `neutral` / `negative` — label from a weighted score over the same directional signals, with weights that depend on **objective** (e.g. education weights confidence moves more heavily than revenue) |
| `mission_alignment_score` | Numeric score used for the label (rounded) |
| `driver_support_signals` | Driver ids whose trajectory-weighted support crosses a fixed threshold |
| `guardrail_risk_signals` | List of `{ guardrail, risk_codes }` when observed trajectories match guardrail-specific risk patterns (stable `outcomes.mission_guardrail.*` codes) |
| `outcome_quality_summary` | Short deterministic string assembled from the above (no LLM) |
| `interpretation_reason_codes` | e.g. `outcomes.mission_interpretation.insufficient_history` when snapshot history is too sparse for a confident mission view |

**Portfolio-level:** `mission_alignment_summary` counts products by `mission_alignment`; `mission_interpretation_note` documents the additive contract.

The Markdown report includes a **Mission-aware interpretation** section and per-product **mission interpretation** lines.

## Portfolio summary

- `products_with_positive_trajectory` — net improvement by the heuristic above.
- `products_with_negative_trajectory` — net regression.
- `products_with_no_meaningful_movement` — flat outcome or **&lt; 2** snapshots for that product.
- `outcome_reason_codes` — stable codes such as `outcomes.readiness_improved`, `outcomes.progression_repeated_blocked`, etc.

## Outputs

- `runs/portfolio/outcomes/latest.json` and `latest.md`
- Timestamped `YYYYMMDDTHHMMSSZ.json` / `.md` (same pattern as other portfolio evaluators)

## Related

- [portfolio-history.md](portfolio-history.md) — artifact spine and trend summaries (broader than outcomes).
- [portfolio-delta-report.md](portfolio-delta-report.md) — baselines used here.
- [operator-policy.md](operator-policy.md) — materiality thresholds.
