# Importer discovery — product shape classification

The GitHub → `products/<id>/` importer runs a **filesystem-only** scan (`argus/importer/discover.scan_repo`) and then assigns a **single heuristic label** describing the *shape* of the repository: how manifests and directories line up, not how the system behaves in production.

**Module:** `argus/importer/classification.py` — `classify_product_shape(facts: RepoFacts) -> ProductShapeResult`.

---

## Labels

| Label | Meaning (hedged) |
|-------|-------------------|
| `mixed_app` | Both Python packaging (`pyproject.toml` / `setup.py`) **and** `package.json` at repo root. |
| `js_frontend` | `package.json` without Python packaging; often paired with Vite/Next/etc. configs **if those files exist**. |
| `static_site` | `index.html` at root without Python packaging or `package.json` (minimal static tree). |
| `python_cli` | Python project with `[project.scripts]` entries surfaced by the scan. |
| `python_service` | Python project without `package.json` and without detected console scripts — may be a library, service, or app with another entry mechanism; **Dockerfile** / `docker-compose` / `src/` / `app/` add evidence only. |
| `unknown` | Not enough distinctive markers. |

Nothing here asserts **runtime**, **scale**, **framework choice in production**, or **architecture** beyond “this file path exists.”

---

## Where it appears

1. **`product.yaml`** — `raw_extensions.argus_onboarding.product_shape` with `label`, `evidence`, `hedged_summary`, `disclaimer`.
2. **`import_notes.md`** — section **C.0** (before grounded facts).
3. **`signals.yaml`** — comment line at top + tuned description for the primary `dependency_health` manifest row when applicable.
4. **`first_pass_argus_summary.md`** — section **A.0** when first-pass runs; same blob is in the JSON instrumentation block.

---

## Rules and precedence

Evaluation order (first match wins):

1. `mixed_app` — Python project **and** `package.json`.
2. `js_frontend` — `package.json` **and not** a Python project.
3. `static_site` — not Python, no `package.json`, `index.html` at root.
4. `python_cli` — Python project, no `package.json`, non-empty script entries from `pyproject.toml`.
5. `python_service` — Python project, no `package.json`, no script entries; stronger hints if Docker or `src/` / `app/` exist.
6. `unknown` — fallback.

---

## Tests

`tests/test_importer_classification.py` builds temporary trees (pyproject, package.json, Vite config, Dockerfile, etc.) and asserts labels and evidence substrings.

---

## Related

- [product-model.md](product-model.md) — canonical `product.yaml` schema.
- `argus/importer/discover.py` — `RepoFacts` fields used as inputs.
- `argus/importer/scaffold.py` — onboarding writers consuming `ProductShapeResult`.
