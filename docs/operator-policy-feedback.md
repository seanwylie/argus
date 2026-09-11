# Operator policy feedback (`argus.operator_policy_feedback.v1`)

**CLI:** `uv run argus policy feedback` (`--json`, `--no-save`, `--limit-history N`)

**Artifacts:** `runs/policy/feedback/latest.{json,md}` plus timestamped copies.

## Purpose

Answer: **Is the current operator policy helping or hurting observable portfolio outcomes?**

The analyzer is **deterministic** and **read-only** with respect to pipelines: it loads stamped **`portfolio/outcomes`** JSON (each run of `argus portfolio outcomes` records `thresholds_used` from that evaluation), runs a **fresh** `evaluate_portfolio_outcomes` for the latest window, and optionally reads recent **`portfolio/cycle`** summaries for recommendation history. It does **not** change `config/operator_policy.yaml` or apply tuning automatically.

## What it computes

Per time point (each stamped outcomes snapshot plus the live evaluation row), it derives:

| Dimension | Meaning |
|-------------|---------|
| Readiness improvement rate | Share of products with `readiness_trajectory == improved` |
| Understanding debt reduction rate | Share with `understanding_debt_trajectory == decreased` |
| Blocked → unblocked | Share with `blocked_pattern == cleared` |
| Intervention resolution rate | Share with `intervention_pattern == resolved` |
| Progression success proxy | Mean of `1 - blocked_count/total_runs` where progression history exists |
| Queue stability vs churn | Stability ≈ rank flat or improved; churn ≈ rank worsened |
| No movement frequency | Share with `overall_trajectory == no_meaningful_movement` |

Across points, it emits **trend summaries** (first vs last), **policy effectiveness indicators** (e.g. a simple outcome health score), **suspicious correlations** (heuristic: e.g. higher `debt_delta_material` between snapshots with falling positive ratio), and **potential tuning opportunities** (text-only suggestions, `safe_to_try: true`).

## Inputs and limitations

- **Historical policy:** Full `argus.operator_policy.v1` is **not** snapshotted per cycle in-repo today. Stamped outcomes carry **`thresholds_used`** (`debt_delta_material`, `confidence_delta_material`) from the moment they were generated — use those for historical materiality.
- **Sparse history:** If you rarely run `argus portfolio outcomes`, the time series is thin; the payload sets **`sparse_history_warning`**.
- **Causality:** Correlations are **not** causal; use [`argus policy experiment`](operator-policy-experiments.md) before changing thresholds.

## Workflow

1. Run portfolio cycles and outcomes on a cadence: `argus portfolio outcomes` (writes `runs/portfolio/outcomes/`).
2. Run `argus policy feedback` to refresh `runs/policy/feedback/latest.md`.
3. If a heuristic flags a threshold/outcome coupling, run `argus policy experiment --profile …` to preview behavior before editing YAML.

## Programmatic use

```python
from pathlib import Path
from argus.policy.feedback import evaluate_operator_policy_feedback, run_operator_policy_feedback

payload = evaluate_operator_policy_feedback(Path("."), limit_history=50)
run_operator_policy_feedback(Path("."), limit_history=50, write_artifacts=True)
```

See also [portfolio-outcomes.md](portfolio-outcomes.md), [operator-policy.md](operator-policy.md), and [operator-policy-experiments.md](operator-policy-experiments.md).
