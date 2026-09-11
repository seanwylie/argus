# `argus loop full` (extended harness)

**`argus loop run`** is the **four-stage** analysis spine only (discover → signals → findings → decisions); see [`argus/orchestrator/README.md`](../argus/orchestrator/README.md).

**`argus loop full`** runs a **longer, in-process harness** with the same four analysis stages, then continues through doctrine, capabilities, history, trends, **structured ideas** (per target product), experiments, advisors, planning, generated actions, approval sampling, execution dry-run analysis, and dashboard render. Artifacts land under **`runs/loop/<run_id>/`** (`manifest.json`, `summary.json`, per-stage folders under `stages/`).

**Escalation is not a harness stage.** `summary.json` includes a **`chain`** object (`escalation: "separate"`, `next_commands` listing `uv run argus escalation generate <product_id>` per target) so operators can chain escalation after the run. Simulation and portfolio **read** are **not** part of this harness—use **`argus simulate`** and **`argus portfolio refresh` / `allocate`** when you need those artifacts.

## Stage order (implementation)

| # | Stage id | Role |
|---|----------|------|
| 1 | `discovery` | Product inventory / discover (same spine as `loop run`) |
| 2 | `doctrine_load` | Load `doctrine.yaml` per target product |
| 3 | `capability_load` | Evaluate capabilities; write evaluation artifact |
| 4 | `signals` | Collect signals |
| 5 | `findings` | Generate findings |
| 6 | `decisions` | Generate decisions |
| 7 | `history_snapshot` | Portfolio snapshot under `runs/history/` |
| 8 | `trends` | Trends analyze; `runs/trends/latest.*` |
| 9 | `ideas` | `run_pipeline` per target product → `runs/ideas/latest.json` (last product wins latest path; see stage output for per-product bundle paths) |
| 10 | `experiments` | Evaluate + prioritize experiments |
| 11 | `advisors` | Advisor consensus per product (stub-safe) |
| 12 | `planning` | Weekly plan under run `stages/planning/` |
| 13 | `plan_actions` | Persist `runs/planning/actions.json` + copy under run |
| 14 | `approval_evaluation` | Sample auto-approval evaluation on action contracts |
| 15 | `execution` | Static dry-run analysis on action contracts (not subprocess execution) |
| 16 | `dashboard` | Generate dashboard HTML |

Stage rows appear in **`summary.json`** (`schema: argus.loop.full_summary.v1`) and in **`manifest.json`**.

## Related CLI

- **`argus validate artifacts`** — structural checks on JSON under `runs/`; optional loop run id validates that run’s `manifest.json` / `summary.json` too.
- **`argus capabilities resume`** — after capability gaps close, clear pauses and re-validate executable work (see `argus capabilities resume --help`).

Adapter normalization for signals is separate from the harness; see **[adapters.md](adapters.md)** and **[signals-and-adapters.md](signals-and-adapters.md)**.
