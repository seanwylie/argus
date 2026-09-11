# Operator learning synthesis (mission-conditioned)

**Schema:** `argus.operator_learning_synthesis.v1`  
**CLI:** `argus policy learning-synthesis [--json] [--no-save] [--limit-history N] [--products-dir DIR]`  
**Module:** `argus/policy/learning_synthesis.py`  
**Artifacts:** `runs/policy/learning_synthesis/latest.{json,md}` and timestamped copies

## Purpose

Answer: **what is Argus learning about how to operate products with different mission shapes?**

This is a **single coherent read-only artifact** that merges:

- Mission-segmented portfolio outcomes (via effectiveness)
- Operator policy effectiveness segments
- Operator policy feedback (stamped outcomes series, correlations)
- Operator policy recommendations (heuristic tuning proposals)
- Portfolio patterns (cross-product systemic detection)
- Latest **mission experiment** (`runs/mission/experiments/latest.json`, `argus.mission_experiment.v2`) when present
- Latest **operator policy experiment** (`runs/policy/experiments/latest.json`) for optional context

## Constraints

- **Deterministic** — same inputs → same synthesis.
- **No auto-tuning** — never writes `config/operator_policy.yaml`.
- **No causal claims** — lessons are associative; `caveat: not_causal` appears on segmented rows.
- **Cautious language** — suitable for human review before any policy change.

## Payload (high level)

| Field | Content |
|-------|---------|
| `by_objective_lessons` | Per mission objective: rates and short lesson text from effectiveness `by_objective` |
| `by_driver_lessons` | Per driver segment: support rates vs improvement (flag `correlates_support_signals` when thresholds met) |
| `by_guardrail_lessons` | Per guardrail segment: risk rates vs negative trajectory (flag `correlates_pressure_or_stagnation`) |
| `repeated_policy_tuning_signals` | Counts of `affected_policy_area` across recommendations |
| `mission_experiment_takeaways` | Bullet lines from latest mission experiment (queue diffs, recommendation diffs, composition notes) |
| `operator_policy_experiment_context` | Short lines from latest policy profile experiment, if present |
| `systemic_learning_patterns` | Notable effectiveness strings + portfolio `detected_patterns` rows |
| `sparse_signal_warnings` | Effectiveness caveats, feedback sparsity, recommendation sparse/conflict signals |
| `top_lessons_so_far` | Ranked short strings (deduped) for quick reading |

## CLI

```bash
argus policy learning-synthesis
argus policy learning-synthesis --json
argus policy learning-synthesis --no-save
argus policy learning-synthesis --limit-history 30
```

## Related docs

- [operator-policy-effectiveness.md](operator-policy-effectiveness.md)
- [operator-policy-feedback.md](operator-policy-feedback.md)
- [operator-policy-recommendations.md](operator-policy-recommendations.md)
- [portfolio-patterns.md](portfolio-patterns.md)
- [mission-experiments.md](mission-experiments.md) — `argus mission test` / sweep and compositions
- [operator-policy-experiments.md](operator-policy-experiments.md)
