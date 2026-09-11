# Importer operations — replay, drift, and status

Imports are **repeatable** when `products/<id>/product.yaml` contains `raw_extensions.import_state` (`schema: argus.import_state.v1`). That block records:

- **`source_repo_url`** — GitHub HTTPS URL  
- **`imported_from_branch` / `imported_from_commit`** — revision pinned at last sync  
- **`include_cursor`**, **`include_local_db_artifacts`**, **`exclude_node_artifacts`**, **`extra_excludes`** — flags replayed into the exclude list  
- **`sync_excludes`** — full rsync exclude list from the last run (preferred when non-empty)  
- **`cache_slug`** — directory name under `.import_cache/`  

See `argus/importer/import_state.py` for the full shape.

## Entry points

From the Argus repo root:

```bash
uv run python tools/import_product.py status --product-id <id>
uv run python tools/import_product.py drift --product-id <id>
uv run python tools/import_product.py replay --product-id <id>
```

Initial GitHub → product import (unchanged):

```bash
uv run python tools/import_product.py --repo-url https://github.com/org/repo --product-id <id>
```

## `status` — read cached metadata

Prints human-readable importer fields, or `--json` for scripts. Fails if `import_state` is missing (not an importer-managed product).

## `drift` — read-only comparison (default)

**Does not** sync the product tree or run `git fetch` unless you pass **`--fetch`**.

Reports:

| Field | Meaning |
|--------|--------|
| `imported_from_commit_stored` | Commit recorded at last import/replay |
| `cache_head_commit` | Current `HEAD` in `.import_cache/<cache_slug>/` |
| `cache_head_matches_stored_commit` | Whether HEAD equals stored commit (abbreviated match allowed) |
| `cache_has_stored_commit` | Whether the object exists in the local clone (`git cat-file`) |
| `import_state_complete` | Required keys present for replay |
| `drift_summary` | Short operator-facing sentence |

**Limits:** Drift compares **cache** to **stored commit**, not a full `rsync --dry-run` of `products/<id>/` vs the cache. If the clone is missing or shallow and the stored commit is not fetched, `cache_has_stored_commit` may be false.

```bash
# Optional: update remote refs before comparing (network)
uv run python tools/import_product.py drift --product-id <id> --fetch
```

## `replay` — re-sync from cache

1. Loads `import_state` from `product.yaml` (validates required fields).  
2. Ensures the GitHub cache clone exists (`ensure_git_repo_cache`).  
3. If **`imported_from_commit`** is set, runs **`git checkout --force <commit>`** in the cache (best effort; shallow clones may not have the object).  
4. **`rsync`** (or fallback) from cache → `products/<id>/` with the same excludes as the original import.  
5. Refreshes **`import_state`** with current branch, commit, and timestamp.  
6. Unless **`--skip-first-pass`**, runs the same Argus first-pass commands as a full import and updates `first_pass_*` fields.  

**Flags:**

| Flag | Effect |
|------|--------|
| `--dry-run` | Print reconstructed argv only; no git/rsync |
| `--skip-first-pass` | Sync only; do not run `argus` evaluation |
| `--no-delete` | Disable rsync `--delete` (destination files not removed) |
| `--cache-dir` | Override clone root (default: `<repo>/.import_cache`) |
| `--no-uv` | Run `argus` on PATH instead of `uv run argus` |
| `--json` | Print structured result |

**Auto-sync:** Nothing in **`drift`** modifies disk. **`replay`** is the explicit re-sync path.

## argv reconstruction

For automation and tests, the effective CLI argv is reconstructed from `import_state` (see `reconstruct_import_argv` in `argus/importer/replay.py`). **`--dry-run`** surfaces it without side effects.

## Troubleshooting

- **`import_state_errors` on replay** — Fix `product.yaml` so `schema`, `source_repo_url`, and `cache_slug` are present and `extra_excludes` is a list of strings.  
- **Checkout failed** — Deepen or fetch in the cache repo, or remove `imported_from_commit` from state to sync from current HEAD after fetch.  
- **Validation failed after replay** — Resolve `validation_errors` in the JSON report; the product manifest must still pass `validate_manifest`.

## Module map

| Module | Role |
|--------|------|
| `argus/importer/replay.py` | `run_import_replay`, `inspect_import_drift`, validation, argv helpers |
| `argus/importer/cli.py` | `status`, `replay`, `drift`, and legacy import argv |
| `argus/importer/import_state.py` | `build_import_state`, `extract_import_state` |
| `tools/import_product.py` | Thin `sys.path` wrapper calling `argus.importer.cli:main` |
