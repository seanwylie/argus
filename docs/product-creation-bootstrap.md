# Product creation bootstrap (`argus.product_creation_bootstrap.v1`)

**CLI:** `argus products bootstrap-creation --product-id <id>` (`--minimal`, `--dry-run`, `--json`, `--no-save`)

**Module:** `argus/products/creation_bootstrap.py`

**Artifacts:** `runs/products/creation_bootstrap/latest.{json,md}` plus timestamped copies.

## Purpose

After scaffolding a product (`argus products create` or `argus products scaffold-creation`), run a **first lightweight Argus loop** so the product immediately has:

- Validated `product.yaml` (explicit check before pipeline steps)
- **Signals** — same path as `argus signals collect <id>` (classic adapters; adapter layer off by default for a predictable bootstrap)
- **Findings** — same engine as `argus findings generate <id>`
- **Full mode only:** **decisions** (`argus decisions generate`) and **ideas** (`argus idea_generation` pipeline with LLM expansion and advisor expansion **off** to keep the run local and fast)

The result is a normalized payload with **`steps_executed`**, **`artifacts_created`** (repo-relative paths), and **`initial_direction_summary`** (signal count, finding previews, optional lifecycle and candidate/idea headlines).

## Modes

| Mode | Flag | Steps |
|------|------|--------|
| **Full** | (default) | validate → signals → findings → decisions → ideas |
| **Minimal** | `--minimal` | validate → signals → findings |

## Scaffold integration

`argus products scaffold-creation` accepts:

- **`--bootstrap`** — after a successful, non–dry-run scaffold, run bootstrap for the new product id.
- **`--bootstrap-minimal`** — implies bootstrap with **minimal** steps (signals + findings only).

Both default **off**; the operator must opt in.

If **`--no-save`** is passed to `scaffold-creation`, bootstrap artifacts under `runs/products/creation_bootstrap/` are not written when bootstrap runs (pipeline outputs under `runs/signals`, `runs/findings`, etc. are still produced unless you avoid bootstrap entirely).

## Dry run and no-save

- **`--dry-run`** — validate the product only; no signals, findings, decisions, or ideas; **no** `runs/products/creation_bootstrap/*` writes (nothing to record for a no-op pipeline).
- **`--no-save`** — run the pipeline but **do not** write the bootstrap summary artifact (`latest.json` / `latest.md`); underlying tools still persist their usual outputs (signals, findings, …) when not dry-run.

## CLI examples

```bash
# Full bootstrap after manual scaffold
uv run argus products bootstrap-creation --product-id my-new-app

# Minimal (signals + findings only)
uv run argus products bootstrap-creation --product-id my-new-app --minimal

# Plan only (validation)
uv run argus products bootstrap-creation --product-id my-new-app --dry-run

# JSON to stdout; still writes artifacts unless --no-save
uv run argus products bootstrap-creation --product-id my-new-app --json

# Scaffold and bootstrap in one flow
uv run argus products scaffold-creation --proposal-id creation_abc123 --bootstrap
```

## Payload shape (summary)

```json
{
  "schema": "argus.product_creation_bootstrap.v1",
  "run_id": "20260112T120000Z",
  "evaluated_at_utc": "...",
  "ok": true,
  "dry_run": false,
  "product_id": "my-new-app",
  "bootstrap_mode": "full",
  "steps_executed": [
    {"step": "validate", "status": "ok", "detail": "...", "warnings": []},
    {"step": "signals_collect", "status": "ok", "detail": {"record_count": 3, "collection_path": "..."}}
  ],
  "artifacts_created": ["runs/signals/collections/...", "runs/findings/generations/..."],
  "initial_direction_summary": {
    "bootstrap_mode": "full",
    "signal_record_count": 3,
    "finding_count": 2,
    "top_findings": [{"title": "...", "severity": "medium", "kind": "..."}],
    "lifecycle_stage": "validate",
    "top_decision_candidates": [],
    "top_ideas": [{"title": "..."}]
  }
}
```

## See also

- [product-creation-scaffold.md](product-creation-scaffold.md) — scaffold from creation proposals
- [product-model.md](product-model.md) — `product.yaml` validation
- [execution.md](execution.md) — loop stages and artifacts
