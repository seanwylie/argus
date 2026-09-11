# Operator policy effectiveness (mission-segmented)

**Schema:** `argus.operator_policy_effectiveness.v1`

**CLI:** `argus policy effectiveness` (optional `--json`, `--no-save`, `--limit-history N`)

This evaluator answers a **descriptive** question: *under the current operator policy snapshot, how do portfolio outcomes differ across mission composition?* It is **deterministic**, **read-only**, and reports **associations** between mission segments and outcome metrics — **not** causal effects of policy.

## Inputs

- **Live** `evaluate_portfolio_outcomes` (including per-product `mission_interpretation` from [portfolio-outcomes.md](portfolio-outcomes.md)).
- **Stamped** `runs/portfolio/outcomes/*.json` (up to `--limit-history`), for a short time series of `by_objective` improvement rates.
- **Effective policy** compact slice from `load_operator_policy` (same shape as policy feedback).
- Presence of `runs/policy/operator_policy_effective.json` (informational flag only).
- **Portfolio artifact counts** and **latest stamps** from [portfolio-history.md](portfolio-history.md) (context for sparse-signal warnings).

## Outputs

- `runs/policy/effectiveness/latest.json` and `latest.md`
- Timestamped `YYYYMMDDTHHMMSSZ.json` / `.md`

## Payload highlights

| Section | Meaning |
|--------|---------|
| `current.by_objective` | Rates (improvement, stagnation, negative, guardrail-risk any, driver-support any, blocked/intervention patterns, mission alignment) **per `mission_objective`** |
| `current.by_driver` | Same rates for products that declare each driver (multi-membership: a product appears in every driver bucket it lists) |
| `current.by_guardrail` | Segment rates for products grouped by declared guardrail membership |
| `current.guardrail_risk_frequency_by_id` | Per guardrail id: share of products listing that guardrail that had any `guardrail_risk_signals` |
| `current.driver_support_frequency_by_id` | Per driver id: share listing that driver with any `driver_support_signals` |
| `current.by_risk_posture` | Rates keyed by `effective_risk_posture` from mission interpretation |
| `current.trajectory_reason_code_contrast` | Reason codes more common among positive vs negative `overall_trajectory` products (when both exist) |
| `stamped_outcomes_series` | Each stamped outcomes file: `by_objective_improvement_rate` map |
| `notable_patterns` | Short deterministic strings (e.g. large objective rate gaps, high guardrail risk); **not** tuning instructions |
| `caveats` | Sparse products, sparse stamped history, single-product segments |

## Relationship to other policy tools

- [operator-policy.md](operator-policy.md) — source thresholds.
- [operator-policy-feedback.md](operator-policy-feedback.md) — time-series feedback without mission segmentation.
- [mission-policy-integration.md](mission-policy-integration.md) — how mission adjusts policy; effectiveness does **not** change that mapping.

## Non-goals

- No LLM output, no auto-tuning, no writes to `config/operator_policy.yaml`.
- Does not prove that a mission profile “caused” an outcome; use `argus policy experiment` before changing config.
