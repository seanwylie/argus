# Product creation scaffold (`argus.product_creation_scaffold.v1`)

**CLI:** `argus products scaffold-creation --proposal-id <id>` (`--json`, `--no-save`, `--dry-run`, optional `--product-id`)

**Artifacts:** `runs/products/creation_scaffold/latest.{json,md}` plus timestamped copies.

## Purpose

Turn a row from **`argus products propose-creation`** (`argus.product_creation_proposals.v1`, stored under `runs/products/creation/`) into a **real** `products/<id>/` tree with:

- **`product.yaml`** — valid for `argus products validate`, **`lifecycle.stage: idea`**, and a **`mission`** block (objective, drivers, guardrails, optional risk posture) built only from **registry profile ids** (see [product-model.md](product-model.md)).
- **Starter `signals.yaml`** — minimal `argus.product_signal_manifest.v1` placeholder.
- **Starter `doctrine.yaml`** — minimal `argus.doctrine.v1` bootstrap (same spirit as `argus products bootstrap`).
- **`notes/CREATION.md`** — import-style record: proposal id, gap evidence, rationale, JSON snapshot of `initial_suggested_mission` as proposed (v1 payloads are already registry ids).
- **Layout** matching **`argus products create`**: `app/`, `config/`, `metrics/`, `scripts/`, `README.md`.

Non-goals: no network, no overwriting existing `products/<id>/`, no LLM content.

## Inputs

1. **`proposal_id`** — must appear in `runs/products/creation/latest.json` or an older stamped `runs/products/creation/<stamp>.json` (ids change each `propose-creation` run; keep the JSON that contains the proposal you approved).

2. Optional **`--product-id`** — normalized slug (same rules as `argus products create`). If omitted, the id is derived from the proposal **`concept_title`**.

3. **Mission registry** — `config/mission_profiles.yaml` must define every profile id referenced in `mission.objective` / drivers / guardrails.

## Mission block rules

Creation proposals carry **`initial_suggested_mission`**. Current emitters use schema
**`argus.initial_suggested_mission.v1`** with **`mission_profile_fields_are_registry_ids: true`**:
`objective`, `drivers`, and `guardrails` are **mission registry profile ids** (not YAML prose).
Human-readable driver lines from the creation mission live under
**`mission_human_context.creation_mission_driver_phrases`** on the proposal, not in `mission.drivers`.

The scaffold copies v1 lists **as registry ids** (validated against `config/mission_profiles.yaml`),
deduplicated with the objective and across drivers vs guardrails. **Legacy** proposals (no v1 flag /
older snapshots) may still list prose or mixed strings; those paths **filter** to known profile ids
only, same as before.

**`resolve_creation_mission`** at scaffold time is recorded in the artifact as **`creation_mission_at_scaffold`** for drift awareness vs **`proposal.creation_mission_used`**.

## Dry run

With **`--dry-run`**, nothing is written under `products/`. The JSON payload includes **`planned_files`**: a map of repo-relative paths to **full file contents** so you can review exactly what would be created.

## CLI examples

```bash
uv run argus products propose-creation --no-save
uv run argus products scaffold-creation --proposal-id creation_abc123def456 --dry-run
uv run argus products scaffold-creation --proposal-id creation_abc123def456 --product-id my-new-app
uv run argus products scaffold-creation --proposal-id creation_abc123def456 --json --no-save
```

After scaffold: `argus products validate`, then normal loop / signals / orchestration as usual.

## See also

- [product-model.md](product-model.md) — `mission` vs `mission_id`
- [mission-model.md](mission-model.md) — registry profiles
- [stub-inventory.md](stub-inventory.md) — scaffold scripts are stubs
