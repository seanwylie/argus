# Operator policy (control plane)

**Schema:** `argus.operator_policy.v1`

Argus exposes a single **effective operator policy**: frozen defaults in code merged with optional overrides in **`config/operator_policy.yaml`**. This consolidates tunable thresholds and weights that previously lived as scattered constants across readiness, queue scoring, quiescence, intervention, portfolio delta report, portfolio history trends, and portfolio cycle synthesis.

## Goals

- **One intentional surface** for operator-tunable numbers (weights, windows, materiality cutoffs).
- **Default behavior preserved** when the config file is absent or empty: defaults match historical pre-policy behavior.
- **Inspectable output:** effective policy is written to **`runs/policy/operator_policy_effective.json`** (and a short **`operator_policy_effective.md`**) when you run **`argus policy show-operator`** without `--json`, and when portfolio cycle artifacts are written (`write_portfolio_cycle_artifacts` also refreshes the JSON snapshot).

## CLI

```bash
argus policy show-operator          # writes runs/policy/operator_policy_effective.{json,md}
argus policy show-operator --json   # print effective policy JSON to stdout only
```

## Config file

- **Path:** `config/operator_policy.yaml` (repo root).
- **Merge:** Deep merge over `default_operator_policy()` in `argus/policy/operator_policy.py`; list values in the override replace whole lists (standard deep-merge behavior).
- **Validation:** Invalid files raise `ValueError` with a clear message at load time.

## Sections (canonical)

| Section | Role |
|--------|------|
| `confidence` | e.g. `low_threshold` — shared low-confidence line for readiness gate hints, operator queue scoring, intervention. |
| `readiness` | `debt_increments` (per reason code), `gate_debt_caution`, `policy_hint_debt_stabilize`. |
| `queue_scoring` | Tier points, debt scale, first-pass points, signal/waiting/blocker points, decision confidence points, lifecycle points, `weights_version`. |
| `quiescence` | Materiality: debt/confidence deltas, queue rank shift, priority score shift. |
| `intervention` | Progression/delta windows, blocked-outcome counts, oscillation/stagnation mins, queue prominence cutoffs, chronic/emerging spans. |
| `cycle` | `benign_intervention_categories`, `delta_inspect_paths` for overall cycle recommendation synthesis. |
| `next_action_policy` | Documentary note only; precedence remains in `argus/orchestrator/next_action_policy.py`. |

## Python API

- `load_operator_policy(repo_root)` — effective policy (cached on config mtime).
- `default_operator_policy()` — frozen defaults.
- `validate_operator_policy(mapping)` — strict validation.
- `clear_operator_policy_cache()` — tests.

## Related docs

- [orchestration-readiness.md](orchestration-readiness.md) — readiness ladder and debt.
- [operator-queue.md](operator-queue.md) — queue scoring and `weight_ledger`.
- [portfolio-quiescence.md](portfolio-quiescence.md), [portfolio-delta-report.md](portfolio-delta-report.md) — shared materiality thresholds.
- [portfolio-intervention.md](portfolio-intervention.md), [portfolio-cycle.md](portfolio-cycle.md) — intervention and cycle synthesis.
