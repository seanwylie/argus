# Simulation

`argus simulate` produces a **deterministic** `SimulationResult` with three branches: **best**, **expected**, **worst** (`ScenarioKind`).

## Inputs

- **Product** — Loaded `ProductNode` (lifecycle stage, cost, caps).
- **Signals** — Latest bundle metrics aggregated when present (`runs/signals/latest/`).
- **Experiment (optional)** — Persisted `Experiment` via `--experiment <exp_id>`; shapes scenario deltas by `ExperimentType` and confidence.

## Outputs

Structured `ScenarioOutcome` rows with narrative labels, metric deltas (string percentages), cost hints, and risk bullets. No writes required; optional JSON via `--json`.

## Relationship to decisions

Simulation is a **preview** tool: it does not mutate decisions or findings. Operators can compare scenarios to top **DecisionCandidate** intents or **Experiment** hypotheses informally. Schema fields (`product_id`, `experiment_id`, `experiment_type`) align with experiment and product models for traceability.

## Limitations

- Heuristic branching — not predictive modeling or Monte Carlo.
- No live execution or product-side API calls.
- Sparse signals → weaker baseline metrics; still returns three scenarios from lifecycle/cost posture.
