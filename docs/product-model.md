# Product model (`products/<id>/product.yaml`)

Argus treats each product as a **bounded node** under `products/<product_id>/`. The canonical **source** for identity, lifecycle, signals wiring, and operator hooks is **`product.yaml`** at that product root. Everything under `runs/` is **generated** from pipelines (signals, findings, decisions, ideas, orchestration, etc.) and is not a substitute for the manifest.

This document matches enforcement in `argus/products/validate.py` (`validate_manifest`). Run **`uv run argus products validate`** after edits.

### Inventory snapshot (this repo)

| Product id | Notes on `raw_extensions` |
|------------|---------------------------|
| `example-app` | Fictional fixture shipped with the repo. No `raw_extensions` block. |
| *all* | None of the committed manifests include **`import_state`**; that block appears when products are managed by **`argus importer`** (see below). |

---

## Source of truth vs generated artifacts

| Source (edit intentionally) | Generated (do not treat as config) |
|------------------------------|-------------------------------------|
| `products/<id>/product.yaml` | `runs/signals/latest/<id>.json` |
| `products/<id>/signals.yaml` (optional; overrides inline manifest) | `runs/findings/latest/<id>.json` |
| `products/<id>/doctrine.yaml` (optional) | `runs/decisions/latest/<id>.json` |
| `products/<id>/scripts/*` (hooks referenced by `actions`) | `runs/ideas/latest.json`, `runs/orchestration/latest/<id>.json`, etc. |

**Rule:** If Argus behavior should change (what to collect, lifecycle stage, cost caps), change **`product.yaml`** (and related product files), then re-run the relevant pipelines.

---

## Required fields

These must be present and well-typed for `validate_manifest` to build a `ProductNode`:

| Field | Requirement |
|-------|-------------|
| **`id`** | Non-empty string. Should match the directory name `products/<id>/`. A **warning** is emitted if `id` ≠ directory name. |
| **`owner`** | Mapping with non-empty **`owner.team`**. **`owner.operator`** is optional (may be `""`). |
| **`lifecycle`** | Mapping with **`lifecycle.stage`** — one of `LifecycleStage` values (`idea`, `build`, `validate`, `grow`, `maintain`, `decline`, `kill`). |
| **`lifecycle.stage`** | Canonical lifecycle; drives transitions and reporting. |

**`name`** is optional; if omitted, the loader defaults `name` to `id`.

---

## Optional fields (recognized at root)

The loader and validator recognize these keys (see `KNOWN_OPTIONAL_ROOT_KEYS` in `validate.py`). Other top-level keys are **not** loaded into `ProductNode` and produce a **warning** (possible typo).

| Field | Role |
|-------|------|
| **`name`** | Human display name. |
| **`type`**, **`status`**, **`state`** | Loose typing for `ProductTypeInfo` (`state` mirrors `lifecycle.stage` when used; if both are set, they must **match** or validation errors). |
| **`metrics`** | `local_paths` (list of repo-relative paths), `primary` (list of metric names). |
| **`cost`** | `monthly_usd`, `notes`. |
| **`signals`** | List of `{ type: <SignalType>, enabled: bool }` entries. |
| **`actions`** | `start`, `stop`, `analyze`, plus extra string keys — typically `./scripts/...` paths under the product. |
| **`constraints`** | `max_monthly_cost_usd`, `min_activity_threshold`. |
| **`lifecycle.next_gate`** | Human gate description; omit or set to a non-empty string (empty string is invalid). |
| **`tags`** | List of strings. |
| **`mission_id`** | Optional reference to a **named mission profile id** in `config/mission_profiles.yaml` (e.g. `revenue`, `education`). Canonical **per-product** mission for operator policy and provenance; must match a known profile or validation fails. |
| **`raw_extensions`** | Mapping — **extension point** for importer state and human notes (below). |
| **`signal_manifest`** | Inline manifest (used only if `signals.yaml` is absent; see `signal_manifest` module). |

---

## Extension points (`raw_extensions`)

`raw_extensions` must be a **mapping** when present. Common patterns include:

### 1. `raw_extensions.import_state` (machine-readable, importer-owned)

Written by **`argus importer`** flows. Durable **import/sync** metadata: cache slug, sync excludes, first-pass status, paths to summaries, etc.

- **Schema:** `schema: argus.import_state.v1` is **required** when `import_state` is present.
- If `import_state` exists but `schema` is wrong or missing, **`validate_manifest` fails** with a clear error.

See `argus/importer/import_state.py` for `build_import_state` and stable keys.

**Orchestration:** computed **`readiness`** on **`argus orchestration state`** (see **`docs/orchestration-readiness.md`**) is separate from **`import_state`** — the latter is durable importer metadata; the former is a derived snapshot from artifacts and eligibility.

### 2. `raw_extensions.argus_onboarding` (human-oriented, optional)

Used by some products for onboarding notes: source repo path, tech stack, entry points, security flags. **Not** validated by the core manifest validator beyond “`raw_extensions` is a mapping” — treat as documentation and context for operators.

**None of the stock products are required to use either block.** Several products have no `raw_extensions`; others only include `argus_onboarding`.

### 3. `raw_extensions.external_bindings` (optional, validated)

Declares **which external identifiers** (domains, repos, analytics property ids) are expected for this product. **`id` / `product_id` remains the canonical internal routing key** — this block is a **verification contract** for signal collection, not a second identity system and not a replacement for world-context `entity` labels.

- **Schema:** `schema: argus.external_bindings.v1` is **required** when `external_bindings` is present.
- **Shape (all lists optional; each entry must be a non-empty string):**
  - **`domains`:** hostnames (e.g. `example.com`).
  - **`repos`:** repository URLs or normalized `host/path` strings consistent with internal normalization (see `argus/signals/external_identity.py`).
  - **`analytics`:** mapping with optional lists:
    - **`google_analytics`**
    - **`google_search_console`**
    - **`other`**
- **Validation:** unknown keys under `external_bindings` produce **warnings** and are ignored; unknown keys under `analytics` similarly.
- **Signal collection:** when bindings are declared, `argus signals collect` compares **extractable** identities from normalized signal payloads (e.g. optional `site_hostname`, `domain`, `ga_property_id` on adapter payloads, or HTTP(S) `canonical.source_ref`) to these lists. If a declared category has **no** extractable identity, collection still succeeds and the bundle records `not_verifiable` for that check. If an extracted value **contradicts** the allow-list, collection **fails** with a clear error and **does not** write `runs/signals/latest/<id>.json`.
- **Adapters:** `analytics_file` copies optional keys from `metrics/analytics.snapshot.json` / `config/analytics.snapshot.json` when present: `site_hostname`, `domain`, `hostname`, `ga_property_id`, `property_id`, `repo_url`.

---

## Examples

### Minimal valid shape (scaffold-style)

```yaml
id: example_app
name: Example App
type: micro_saas
status: experimental
state: build
owner:
  team: argus
  operator: ""
metrics:
  local_paths: [metrics/]
  primary: [signups]
cost:
  monthly_usd: 0
  notes: "Placeholder"
signals:
  - type: filesystem
    enabled: true
actions:
  start: "./scripts/start.sh"
  stop: "./scripts/stop.sh"
  analyze: "./scripts/analyze.sh"
constraints:
  max_monthly_cost_usd: 100
  min_activity_threshold: 0
lifecycle:
  stage: build
  next_gate: "first measurable user outcome"
```

### Importer `import_state` fragment (valid)

```yaml
raw_extensions:
  import_state:
    schema: argus.import_state.v1
    import_mode: cache_sync
    source_repo_url: "https://example.com/org/repo.git"
    cache_slug: "repo_main"
    imported_at_utc: "2026-01-01T00:00:00Z"
    sync_excludes: []
    include_cursor: false
    include_local_db_artifacts: false
    exclude_node_artifacts: true
    extra_excludes: []
    first_pass_ran: false
    first_pass_status: pending
    first_pass_summary_path: null
```

---

## Validation behavior summary

- **Errors** block inventory registration: missing `id` / `owner` / `lifecycle`, invalid enums, bad `signals` / `actions` shapes, script paths missing on disk, `state` vs `lifecycle.stage` conflict, invalid `raw_extensions.import_state`, non-mapping `cost` / `metrics` / `constraints`, invalid `tags`, empty `lifecycle.next_gate` when key is present.
- **Warnings** do not block: missing `name`, unknown top-level keys, empty `actions.*`, metrics path directory missing, directory name ≠ `id`, signal manifest notices.

---

## Related code

- `argus/core/models/product.py` — `ProductNode` and nested types
- `argus/core/serialize.py` — `product_node_from_dict`
- `argus/products/validate.py` — `validate_manifest`
- `argus/products/inventory.py` — `build_inventory`
- `argus/importer/import_state.py` — `import_state` schema and helpers
