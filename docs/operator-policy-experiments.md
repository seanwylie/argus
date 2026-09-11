# Operator policy experiments

Argus can compare how **different operator policy profiles** would affect the same repository state—**read-only**—before you adopt a policy in production.

Experiments evaluate:

- **Operator queue** — ranking and priority scores (in-memory build; does not overwrite `runs/portfolio/operator_queue/latest.*`).
- **Quiescence** — whether another portfolio pass is warranted, using the profile’s thresholds.
- **Intervention** — deterministic intervention hints (categories per flagged product).
- **Cycle synthesis** — the overall operator recommendation string produced from quiescence, delta, and intervention for that profile.

No **portfolio progression**, **orchestration advancement**, or **queue artifact writes** occur during an experiment. The payload marks `read_only: true` and documents this in `note`.

## CLI

```bash
uv run argus policy experiment
uv run argus policy experiment --profile path/to/override.yaml --profile path/to/another.yaml
uv run argus policy experiment --no-effective
uv run argus policy experiment --json
uv run argus policy experiment --no-save
```

| Option | Meaning |
|--------|---------|
| *(none)* | Compare **default (builtin)** policy with the repo **effective** policy (`config/operator_policy.yaml` merged over defaults), unless `--no-effective`. |
| `--profile PATH` | Repeatable. Each file is YAML merged over **builtin defaults**, validated (`argus.operator_policy.v1`), and compared as its own profile. |
| `--no-effective` | Omit the repo effective profile; useful when you only want default vs explicit `--profile` files. |
| `--json` | Print the full experiment JSON to stdout (schema `argus.operator_policy_experiment.v1`). |
| `--no-save` | Do not write under `runs/policy/experiments/`. |

## Artifacts

When not using `--no-save`, outputs go to:

- `runs/policy/experiments/latest.json` and `latest.md`
- Timestamped copies: `runs/policy/experiments/<run_id>.{json,md}`

Markdown is operator-oriented: profiles, headline quiescence/delta/intervention/cycle lines, queue top slice, intervention category changes vs default, quiescence interpretation diffs, and a **safe vs behaviorally significant** assessment per non-reference profile.

## Payload schema

Top-level schema: **`argus.operator_policy_experiment.v1`**.

Useful fields:

- **`compared_profiles`** — `id`, `label`, `source` for each profile (always includes **default**; optionally **effective**; each `--profile` file).
- **`effective_policy_summaries`** — compact per-profile knobs (confidence, quiescence, intervention windows, cycle, queue weights version).
- **`per_profile_results`** — queue top slice (first products), quiescence recommendation, delta action, intervention count, cycle recommendation and rationale codes.
- **`recommendation_differences`** — per profile vs default where applicable.
- **`products_priority_changes_vs_default`** — material score/rank moves vs default’s `score_delta_material`.
- **`products_intervention_category_changes_vs_default`** — intervention category changes per product vs default.
- **`quiescence_interpretation_changes_vs_default`** — recommendation / portfolio quiescent / material-change set diffs vs default.
- **`assessments`** — `reference` for default; others get labels such as **`safe_to_try`**, **`mostly_safe_queue_only`**, or **`behaviorally_significant`**, with machine-readable **`codes`**.

## Interpreting “safe to try” vs “behaviorally significant”

- **Reference** profile is always **default (builtin)**; other profiles are compared against it for queue, intervention, and quiescence.
- **Behaviorally significant** is used when changes would likely alter operator decisions: cycle recommendation change, intervention category change, quiescence interpretation or material-change set change, etc.
- **Mostly safe (queue only)** suggests ordering/score shifts without those higher-level flips.
- **Safe to try** means little or no material difference detected under the experiment’s diff rules.

Tune thresholds in policy (for example `quiescence.score_delta_material`) to control how sensitive queue diffs are.

## Programmatic use

Import from **`argus.policy.experiment`** (preferred). The package root `argus.policy` also exposes `evaluate_policy_experiment` and `run_policy_experiment` via lazy loading so importing `operator_policy` does not pull heavy portfolio/orchestrator modules at startup.

```python
from pathlib import Path
from argus.policy.experiment import evaluate_policy_experiment, run_policy_experiment

payload = evaluate_policy_experiment(Path("."), profile_yaml_paths=[Path("policy_try.yaml")])
# Or with artifact write:
payload = run_policy_experiment(Path("."), profile_yaml_paths=None, write_artifacts=True)
```

See also [operator-policy.md](operator-policy.md) for the base policy model and [operator-queue.md](operator-queue.md) for queue semantics.
