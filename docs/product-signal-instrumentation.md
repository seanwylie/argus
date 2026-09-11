# Product signal instrumentation

**Purpose:** After **scaffold** and **bootstrap**, many products still lack enough *declared* observability for Argus to improve them safely. This pass answers: **“Can we measure this product well enough to reason about the next step?”** It does **not** invent business outcomes; it assesses coverage, proposes a minimal **signal contract**, and emits **labeled** structural/derived/synthetic seed hints so pipelines can align without confusing placeholders with real telemetry.

## Principles

- **Deterministic** — same product tree → same assessment (aside from `evaluated_at_utc`).
- **No LLM** — rules only.
- **No mutation** of real telemetry sources or external systems.
- **No fake success** — synthetic entries are explicit placeholders (`value: null`) for wiring, not KPIs.

## Command

```bash
argus products instrument-signals --product-id <id>
argus products instrument-signals --product-id <id> --json
argus products instrument-signals --product-id <id> --no-save
argus products instrument-signals --product-id <id> --products-dir /path/to/parent/of/products
```

| Flag | Effect |
|------|--------|
| `--json` | Print `argus.product_signal_instrumentation.v1` JSON to stdout |
| `--no-save` | Evaluate only; do not write under `runs/products/signal_instrumentation/` |
| `--products-dir` | Alternate parent directory for `products/<id>` (same semantics as other `argus products` commands) |

## Artifacts

Under `runs/products/signal_instrumentation/`:

| Path | Description |
|------|-------------|
| `latest/<product_id>.json` | Current evaluation for that product |
| `latest/<product_id>.md` | Short Markdown summary |
| `<product_id>__<timestamp>.json` | Stamped copy (same payload as latest for that run) |
| `<product_id>__<timestamp>.md` | Stamped Markdown |

## Payload (`argus.product_signal_instrumentation.v1`)

| Field | Description |
|-------|-------------|
| `product_id` | Product id |
| `evaluated_at_utc` | ISO-8601 UTC timestamp |
| `instrumentation_status` | `adequate` \| `weak` \| `sparse` \| `missing` (overall) |
| `signal_coverage_assessment` | Deterministic summary string (dimension counts) |
| `missing_signal_dimensions` | Dimensions not yet at `adequate` in the contract model |
| `proposed_signal_contract` | Structured minimal observable model (`argus.proposed_signal_contract.v1`): per-dimension coverage, suggested signal types, suggested primary metric keys |
| `synthetic_seed_signals` | Labeled `structural`, `derived`, and (when not `adequate`) `synthetic` placeholder slots |
| `observability_notes` | Bullet strings (paths empty, primary metrics empty, validation warnings, etc.) |
| `recommended_next_step` | Deterministic guidance (improve manifest/metrics before optimization loops) |

## Lifecycle placement (conceptual)

Recommended mental order for a new product:

1. **Scaffold** — on-disk product (`argus products scaffold-creation`, etc.)
2. **Bootstrap** — reality wiring (`argus products bootstrap` / `bootstrap-creation`)
3. **Instrument signals** — this pass (`argus products instrument-signals`)
4. **Improve** — experiments, findings, decisions (once observability is sufficient)

Autonomous runners and promotion logic are **not** required to enforce this sequence yet; operators (or future policy) can gate on `instrumentation_status` when ready.

## Worker handoff (instrumentation work orders)

After instrumentation artifacts exist under `runs/products/signal_instrumentation/latest/`, Argus core can turn **pressure** into an explicit **`argus.work_order.v1`** for the worker lane (the worker does not decide whether instrumentation is warranted):

```bash
argus worker issue-instrumentation-work-orders --product-id <id>
argus worker issue-instrumentation-work-orders --json
```

- Only **`weak` / `sparse` / `missing`** assessments qualify; **`adequate`** products are skipped.
- Work orders are written to `runs/worker/work_orders/` per [worker-lane.md](worker-lane.md); use **`--no-save`** to preview without writing.

See **`argus.products.instrumentation_work_orders`**.

## Worker apply (first real mutation path)

After a **`signal_instrumentation`** work order is **steward-approved**, the worker lane may apply a **minimal, deterministic** contract update so Argus can move from “diagnose weak signals” to “declared signal surfaces exist.” This is **not** a substitute for real telemetry; it wires **manifest rows** and optional **primary metric key refs** only.

- **Module:** `argus/products/apply_signal_instrumentation.py` — **`apply_signal_instrumentation`**
- **Schema:** **`argus.product_signal_instrumentation_apply.v1`**
- **Typical writes:** `products/<id>/signals.yaml` (create or merge), light updates to `product.yaml` `metrics.primary` when the proposed contract lists suggested keys.
- **Artifacts:** `runs/products/signal_instrumentation_apply/<product_id>__<execution_id>.json` (+ `.md`), and `latest/<product_id>.json` (+ `.md`).
- **Validation:** Re-runs `evaluate_product_signal_instrumentation` after apply; results appear in the apply payload (`post_apply.instrumentation_status`, `post_apply.missing_signal_dimensions`) and in **`argus.worker_execution_outcome.v1`** (`instrumentation_apply` / `validation_summary`).

### Argus core feedback (read-only)

Portfolio lifecycle, operator queue, operator summary/narrative, and autonomy memory **read** `runs/products/signal_instrumentation_apply/latest/<product_id>.json` (same schema as above) alongside the latest **scan** artifact under `runs/products/signal_instrumentation/latest/`. They distinguish:

- **Effective instrumentation pressure** — weak/sparse/missing on the latest scan, **minus** ids where a **successful** apply recorded **`post_apply.instrumentation_status == adequate`** (worker validation snapshot may disagree with a stale scan file).
- **Apply follow-up** — still weak on scan **and** latest apply is **`partial`**, or **success** without adequate **`post_apply`**.
- **Graceful absence** — missing or unreadable apply files simply skip refinement; no execution is triggered.

Module: **`argus.products.instrumentation_feedback`**.

**Boundaries:** Argus core **issues** the work order; the worker **does not** re-decide what is worth building. No LLM, no edits outside the observability contract surface, no claiming synthetic placeholders are live KPIs. See [worker-lane.md](worker-lane.md) and `argus worker execute-work-order`.

## Python API

```python
from argus.products.signal_instrumentation import run_product_signal_instrumentation

payload = run_product_signal_instrumentation(
    repo_root,
    product_id="my-product",
    write_artifacts=True,
    products_dir=None,
)
```

See `argus.products.signal_instrumentation` for `evaluate_product_signal_instrumentation` (no write) and artifact helpers.
