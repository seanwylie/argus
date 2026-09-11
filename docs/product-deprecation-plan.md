# Product deprecation plan (`argus.product_deprecation_plan.v1`)

**CLI:** `argus products plan-deprecation --proposal-id <id>` (`--json`, `--no-save`)

**Module:** `argus/products/deprecation_plan.py`

**Artifacts:** `runs/products/deprecation_plan/latest.{json,md}` and timestamped copies.

## Purpose

Turn a row from **`argus products propose-deprecation`** (`argus.product_deprecation_proposals.v1`, under `runs/products/deprecation/`) into a **structured retirement plan**: ordered **plan steps**, **preservation needs**, **archive/tombstone guidance**, **rollback notes**, and **operator review requirements**.

This mirrors the creation pipeline (propose → plan/scaffold) but for wind-down: **plan-only**, with explicit safety flags.

## Safety (non-negotiables)

The payload includes `safety`:

- `no_deletion` — true
- `no_product_mutation` — true
- `no_irreversible_actions` — true
- `plan_only` — true

No files are deleted, `product.yaml` is not edited, and orchestration is not advanced by this command.

## Input

- **`proposal_id`** — must appear in `runs/products/deprecation/latest.json` or a stamped `runs/products/deprecation/<timestamp>.json` (same schema as deprecation proposals).

## Output fields (summary)

| Field | Meaning |
|-------|---------|
| `deprecation_posture` | Copied from proposal: `retire`, `harvest`, `archive`, or `repair_instead` |
| `plan_steps` | Deterministic steps with `step_id`, `phase`, `title`, `description`, `safe_action_category` |
| `preservation_needs` | What to retain or export (planning text) |
| `archive_or_tombstone_recommendation` | Guidance for archival / tombstone README (no automated writes) |
| `rollback_or_restore_note` | How to back out a plan or restore from backups outside Argus |
| `operator_review_requirements` | Human gates before destructive work elsewhere |
| `evidence_summary` | Condensed from the proposal’s supporting evidence |
| `product_still_in_inventory` | Whether `products/<id>/` still validates in inventory |

### `repair_instead`

This is **not** a retirement execution path. The plan is **repair-oriented**: triage inbox, stabilize orchestration, re-run deprecation proposals later. Archive/tombstone text explicitly defers archival until re-evaluation.

## CLI examples

```bash
uv run argus products propose-deprecation --no-save
# … approve a proposal id from JSON …

uv run argus products plan-deprecation --proposal-id deprecation_abc123def456

uv run argus products plan-deprecation --proposal-id deprecation_abc123def456 --json
```

## See also

- [product-deprecation-proposals.md](product-deprecation-proposals.md) — proposal source
- [product-model.md](product-model.md) — product layout
