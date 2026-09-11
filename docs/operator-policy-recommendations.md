# Operator policy recommendations (`argus.operator_policy_recommendations.v1`)

**CLI:** `argus policy recommend` (`--json`, `--no-save`, `--limit-history`, optional `--products-dir`)

**Artifacts:** `runs/policy/recommendations/latest.{json,md}` plus timestamped `YYYYMMDDTHHMMSSZ.{json,md}`.

## Purpose

Turn **read-only** portfolio signals into **human-reviewable** proposals for changing `config/operator_policy.yaml` (merged over code defaults). Nothing in this path mutates policy files or runtime thresholds.

Inputs are combined deterministically from:

- **`argus policy feedback`** — outcome rates, trends, suspicious correlations, effectiveness snapshot
- **`argus policy effectiveness`** — mission-segmented rates (objective, driver, guardrail, risk posture) and stamped outcomes series context
- **Portfolio outcomes** (via feedback, effectiveness, and patterns) including **mission-aware interpretation** on live evaluation
- **`argus portfolio patterns`** — cross-product systemic patterns
- **Stamped intervention history** — flagged-product counts over recent `runs/portfolio/intervention/*.json` files (trend: rising / falling / flat / unknown)

## Mission-aware recommendations

Rows whose **`mission_scope`** is not `portfolio_wide` are **scoped** to a mission segment observed in effectiveness data (associative only — not causal). They reference **`argus.policy.effectiveness`** segment tables and registry ids when a segment id matches **`config/mission_profiles.yaml`** profiles.

| `mission_scope` | Meaning |
|-----------------|--------|
| `portfolio_wide` | Heuristic driven by aggregate feedback/patterns (legacy-style) |
| `objective` | Triggered from `current.by_objective` for a given `mission_scope_detail` (e.g. `revenue`, `education`) |
| `driver` | Triggered from driver cohort + `driver_support_frequency_by_id` |
| `guardrail` | Triggered from `guardrail_risk_frequency_by_id` |
| `risk_posture` | Triggered from `by_risk_posture` contrast (e.g. conservative vs moderate stagnation) |
| `mixed` | Meta recommendation when effectiveness **notable patterns** report large objective gaps |

**`applicable_mission_profiles`:** Registry profile ids that align with the segment (e.g. objective `education` → profile `education`). Empty `[]` means “not specific to one profile” or “all profiles” for meta rows — use JSON context.

**`recommendation_strength`:** `low` | `moderate` | `high` — derived from heuristic confidence (after weak-signal down-weighting).

**`sparse_signal_warning`:** `true` when the row is down-weighted (few products, effectiveness caveats, or portfolio weak-signal path).

**Top-level `sparse_signal_warning`:** `true` when feedback reports sparse stamped history or effectiveness reports sparse caveats.

## Schema (each recommendation)

| Field | Meaning |
|-------|---------|
| `recommendation_id` | Stable identifier |
| `affected_policy_area` | Dotted path such as `quiescence.debt_delta_material`, `confidence.low_threshold`, `queue_scoring.debt_scale`, `intervention.progression_runs_window`, `process` |
| `current_value` | Effective value from resolved policy (or `null` for process-only rows) |
| `suggested_value` | Proposed value or a string such as `manual_review` / CLI hint |
| `rationale` | Why this might help given current signals |
| `expected_effect` | Intended operational consequence |
| `confidence` | Heuristic 0–1 (down-weighted when few products evaluated) |
| `safe_to_try` | Boolean — `false` marks higher-impact or stricter changes that deserve extra review |
| `mission_scope` | `portfolio_wide` \| `objective` \| `driver` \| `guardrail` \| `risk_posture` \| `mixed` |
| `mission_scope_detail` | Optional segment key (e.g. `revenue`, `education`, `conservative_vs_moderate`) |
| `applicable_mission_profiles` | List of mission profile ids from the registry when applicable |
| `recommendation_strength` | `low` \| `moderate` \| `high` |
| `sparse_signal_warning` | Boolean |

Additional top-level fields:

- `observed_inefficiencies` — bullet strings (sparse history, stressed mix, systemic patterns, rising intervention load, **effectiveness caveats/patterns**)
- `conflicting_signals` — when headline trends and correlation heuristics disagree
- `sparse_signal_warning` — aggregate sparse signal
- `source_snapshot` — run ids for feedback, **effectiveness**, patterns, plus intervention trend summary

## Markdown report

Rendered output groups rows into:

1. **Safe adjustments** — `safe_to_try: true` (each row lists **scope**, **strength**, **sparse** when relevant)
2. **High-impact but risky adjustments** — `safe_to_try: false`
3. **Observed inefficiencies** — narrative bullets (not necessarily one-to-one with each recommendation)

## Heuristics (summary)

**Portfolio-wide** (unchanged in spirit): high no-movement share → slightly lower `quiescence.debt_delta_material`; improving readiness + low flat → optional small increase; stressed mix → optional tighten `confidence.low_threshold`; very low positive share → optional `confidence_delta_material` tweak; high-severity patterns → optional `queue_scoring.debt_scale`; intervention trends → optional intervention windows; suspicious correlations → `argus policy experiment`; conflicting signals → review row.

**Mission-scoped** (from effectiveness, examples):

- **Revenue objective**, high **negative_rate** in segment → optional tighten `confidence.low_threshold` (risky).
- **Education objective**, high **stagnation_rate** → optional tighten `quiescence.confidence_delta_material` (risky).
- **Guardrail id**, high **guardrail_risk_signal_rate** → optional widen `intervention.progression_runs_window` (risky).
- **Education driver**, low **driver_support_signal_rate** with enough products → optional slightly lower `quiescence.debt_delta_material` (safer).
- **Conservative vs moderate** risk posture stagnation gap → process row suggesting mission-mapped **policy experiment**.
- **Objective improvement-rate gap** in effectiveness notable patterns → process row for experiment-first workflow.

Exact thresholds live in `argus/policy/recommendations.py`.

## CLI

```bash
uv run argus policy recommend
uv run argus policy recommend --json
uv run argus policy recommend --no-save
uv run argus policy recommend --limit-history 30
```

## See also

- [operator-policy.md](operator-policy.md)
- [operator-policy-feedback.md](operator-policy-feedback.md)
- [operator-policy-effectiveness.md](operator-policy-effectiveness.md)
- [operator-policy-experiments.md](operator-policy-experiments.md)
- [portfolio-patterns.md](portfolio-patterns.md)
- [portfolio-outcomes.md](portfolio-outcomes.md)
