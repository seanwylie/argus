# runs/

**Run artifacts** live here: local experiment outputs, exported snapshots, scratch logs from Argus or operators. Treat this directory as **ephemeral** in real use; the folder exists so workflows have a stable mount point.

**Git:** The repo root **`.gitignore`** ignores **all** paths under `runs/` except **`runs/README.md`** (blanket `runs/*` + `!runs/README.md`). That covers pipeline output, `latest.json`, debug reports, policy synthesis, products/*, worker/*, mission snapshots, and anything else added later—**do not** commit them as source. If something under `runs/` shows up in `git status`, it is usually a mistake; after fixing `.gitignore`, drop it from the index with `git rm --cached` (see below).

Example: `argus products validate --write runs/inventory-snapshot.json` writes a full **product inventory** JSON (valid and invalid entries).

If files were committed by mistake:

```bash
git ls-files runs/ | grep -v '^runs/README.md$' | xargs -r git rm --cached
```

Do not commit secrets or large binaries without explicit policy.

## Retention (disk)

Some pipelines write **timestamped copies** in addition to `latest` artifacts. Argus trims them automatically so `runs/` does not grow without bound:

| Location | Behavior |
|----------|----------|
| `runs/signals/collections/*.json` | After each successful **signals collect** save, old `YYYYMMDDTHHMMSSZ_<product_id>.json` files are pruned (default **32** per product). Override with **`ARGUS_SIGNALS_COLLECTIONS_KEEP_PER_PRODUCT`** (`0` = unlimited). Manual pass: **`uv run argus signals prune-collections`** (`--dry-run`, `--keep N`). **`runs/signals/latest/` is never deleted by this.** |
| `runs/builder/work_orders/<id>/*.json` | After each work-order write, stamped copies older than the newest **24** are removed (`latest.json` / `latest.md` kept). Override with **`ARGUS_BUILDER_WORK_ORDERS_KEEP_STAMPED`** (`0` = unlimited). |

For a full wipe of generated state, use **`argus reset soft`** (see CLI help). That removes essentially all of `runs/` except `runs/README.md`.

**Memory:** Argus processes typically load one product bundle at a time; peak RSS grows when something reads **many large JSON files** at once (for example portfolio dashboards). Retention here targets **disk** growth from duplicate timestamped artifacts; use **`argus reset soft`** or narrow CLI scope if a workspace has grown huge.

### Headroom (free space)

Before larger writes (for example **signals collect** and **builder work orders**), Argus can compare volume free space to a minimum. This is **not** a per-user quota (NFS quotas need OS-specific tooling).

| Variable | Meaning |
|----------|---------|
| **`ARGUS_MIN_FREE_DISK_MB`** | Minimum free MiB on the volume (default **256**). Set **`0`** to disable the check. |
| **`ARGUS_ENFORCE_DISK_HEADROOM`** | If **`1`** / **`true`**, low free space **raises** before the write; otherwise Argus **logs a warning** only. |

**`argus doctor`** prints an approximate **`runs/`** size and volume free/total and surfaces low-disk warnings (including under **`--strict`**).
