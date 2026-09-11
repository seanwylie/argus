# cli

**Intent:** Single operator-facing CLI for Argus (`argus` console script). Stays thin and delegates to core packages.

## Invocation

- **`uv run argus`** (with `uv` and the project root as cwd), or
- **`argus`** after `pip install -e .` / `uv sync`

Top-level help: `argus --help` (includes short examples in the epilog).

## Command groups

| Command | Purpose |
|---------|---------|
| `argus products create <name>` | Scaffold `products/<name>/` (`product.yaml`, scripts, dirs). `--type` = `content_stream` \| `micro_saas` \| `static_site` \| `utility_api` \| `mobile_companion`. `--force`, `--json`. Validates after generation. |
| `argus products bootstrap <id>` | Add `doctrine.yaml`, `metrics/analytics_placeholders.md`, `metrics/snapshots/README.md`, `experiments/seed.json`. `--force`, `--json`. |
| `argus products list` | List products (table or `--json`); optional `--write PATH` for full inventory JSON |
| `argus products validate` | Validate all products; full report; `--json`; exits **1** if any invalid; `--write PATH` |
| `argus products show <id>` | Text or `--json` for one product; errors if missing or invalid |
| `argus scan` | Legacy: print product IDs only (prefer `argus products list`) |
| `argus signals adapters` | List registered signal adapters (`--json`) |
| `argus signals snapshot-types` | Business snapshot ingesters (PostHog/GA/AWS/Stripe/mobile-style JSON/CSV); `--json` |
| `argus signals ingest-snapshots` | Read `products/<id>/metrics/snapshots/` (+ optional `fixtures/business_snapshots/<id>/`); emit `CUSTOM` signals; `--no-merge`, `--no-fixtures`, `--no-save`; optional `PRODUCT_ID` |
| `argus signals collect` | Run enabled adapters for all valid products, or one `PRODUCT_ID`; `--json`; `--no-save` |
| `argus signals show <id>` | Last persisted signal bundle from `runs/signals/latest/` |
| `argus findings generate` | Run finding rules on signals (all products or one `PRODUCT_ID`); `--fresh-signals`; `--no-save`; `--json` |
| `argus findings show <id>` | Last persisted findings from `runs/findings/latest/` |
| `argus findings summary` | Aggregate counts from latest findings per product (`--json`) |
| `argus decisions generate` | Lifecycle scores + ranked candidates from latest findings (`--json`, `--no-save`) |
| `argus decisions portfolio` | Cross-product ranked top actions (`--json`); writes `runs/decisions/latest/portfolio.json` |
| `argus decisions show <id>` | Last persisted decision bundle for a product |
| `argus decisions history [id]` | Read generation files under `runs/decisions/generations/` (oldest first); `--json` |
| `argus decisions churn [id]` | Churn / stability scores from that history; `--json` |
| `argus lifecycle show <id>` | Lifecycle scores / `kill_candidate` (saved decisions, or computed from latest findings) |
| **`argus portfolio refresh`** | **Validate products → collect signals → findings → decisions → portfolio report**; writes `runs/portfolio/latest/{summary.txt,refresh.json}` and updates decisions portfolio; `--per-product` adds `runs/portfolio/latest/products/<id>.txt`; `--no-save` |
| `argus portfolio show` | Print last `summary.txt` (or `--json` for `refresh.json` when present) |
| `argus doctor` | Check products dir, invalid manifests, missing artifacts, staleness; `--json`; `--strict` (warnings → exit 1) |
| `argus dashboard` | Write `runs/dashboard/index.html` (static portfolio table + filters); `--out`, `--open`; `--products-dir` |
| `argus history snapshot` | Capture portfolio snapshot to `runs/history/snapshots/<id>/snapshot.json`; update `runs/history/latest.json`; `--label`, `--out`, `--json`; `--products-dir` |
| `argus history diff A B` | Compare two snapshot files or ids; `--json` |
| `argus history product <id>` | Timeline for one product across snapshots; `--json` |
| `argus trends analyze [id]` | Trend/drift from snapshot history; all products or one; writes `runs/trends/latest.{json,txt}`; `--json` |
| `argus trends summary` | One line per product with history; `--json` |
| `argus trends drift` | Only products with drift signals; `--json` |
| `argus escalation generate <product_id>` | Evaluate triggers vs latest findings + decisions; write `runs/escalations/latest/<packet_id>.json` if needed; `--json`, `--markdown`, `--no-save` |
| `argus escalation list` | List packets (`--json`) |
| `argus escalation show <packet_id>` | Show one packet; `--json`, `--markdown` |

Shared flags: `--products-dir` to override `<repo>/products` where applicable.

## Implementation map

| Area | Package |
|------|---------|
| Inventory + scaffolds | `argus/products/` (`scaffold.py`) |
| Signals | `argus/signals/` (`adapters/`, `snapshots/` for local business JSON/CSV) |
| Findings | `argus/findings/` |
| Lifecycle + decisions | `argus/lifecycle/`, `argus/decision/` |
| Portfolio refresh orchestration | `argus/portfolio/refresh.py` |
| Escalation packets | `argus/escalation/` |
| Dashboard (static HTML) | `argus/dashboard/` |
