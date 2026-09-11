# Experiments

Argus separates **suggestions** from **tracked records**.

## Proposal vs persisted experiment

- **`ExperimentProposal`** — Output of `argus experiments propose` (deterministic rules from findings, trends, decisions, lifecycle). Not a file until you create one.
- **`Experiment`** — Stored JSON under `runs/experiments/exp_*.json` via `argus experiments create`.

## Lifecycle (tracked experiments)

States: **proposed → active → completed | failed** (see `ExperimentStatus`). Transitions are validated in `argus.experiments.registry`.

## Prioritization

`argus experiments rank` scores proposals using the same **strategy profile** as decision priority (`runs/strategy/current.json` when present): impact, urgency, confidence, lifecycle fit, cost and effort penalties, plus an experiment multiplier for launch-like work.

## Evaluation model

`argus experiments evaluate` reads local snapshots/trends and writes deterministic **`ExperimentEvaluation`** results (verdict, composite score, summary). Missing data yields conservative or inconclusive outcomes rather than crashing.

## Related commands

| Command | Purpose |
|---------|---------|
| `experiments propose` | Generate candidate experiments (JSON or text) |
| `experiments rank` | Sort proposals + top 3 portfolio-wide |
| `experiments create` | Persist a hypothesis |
| `experiments list` / `show` / `update-status` | Manage records |
| `experiments evaluate` | Score open experiments against artifacts |
