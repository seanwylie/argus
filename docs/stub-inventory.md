# Stub and placeholder inventory

This document lists **intentional stubs**, **placeholders**, **incomplete implementations**, and **deprecated shims** so the repo stays self-documenting about what is production-ready versus deferred.

**How to read categories**

| Category | Meaning |
|----------|---------|
| **Intentional stub** | Deliberate stand-in; behavior is defined and stable until replaced. |
| **Incomplete implementation** | Real code path exists but missing behavior, integration, or coverage. |
| **Placeholder (future)** | Reserved name, empty package, or doc-only hook for a future system. |
| **Deprecated** | Shim or name kept for compatibility; migrate to the replacement. |

**Priority**

| Level | Meaning |
|-------|---------|
| **P0** | Affects core loop correctness or operator expectations; document or wire first. |
| **P1** | Important for productization (infra, adapters, shared libs). |
| **P2** | Nice-to-have clarity or optional integrations. |

---

## Code markers (optional convention)

Where stubs live in Python, you may see a one-line marker:

```text
# ARGUS-STUB:<category> — short reason (see docs/stub-inventory.md)
```

`category` is one of: `intentional`, `placeholder`, `incomplete`, `deprecated`.

---

## Orchestrator (`argus loop run`)

**Boundary (by design):** `argus loop run` ends at **decisions** (not the full product pipeline). Weekly planning (`argus planning weekly`), escalation packets (`argus escalation generate <product>`), **orchestration state** (`argus orchestration state` — eligibility/tasks under `runs/orchestration/`), and related outputs are **separate commands**—there are no planning/escalation/orchestration stub stages in the analysis loop. See `docs/system-flow.md` and `argus/orchestrator/README.md`.

---

## Advisors

| Subsystem | File | Stub / gap | Category | Intended future state | Priority |
|-----------|------|------------|----------|------------------------|----------|
| Advisors | `argus/advisors/runner.py` | Deterministic responses when LLM unavailable or `--stub-only`; model id `argus.advisors.stub.v1`. | Intentional stub | Default remains deterministic; LLM path when `ARGUS_OPENAI_*` configured. | P2 |
| Advisors | `argus/advisors/models.py`, `cli.py` | LLM-ready scaffold; CLI `--stub-only`. | Placeholder (future) | Richer provider options, rate limits, caching. | P2 |

---

## Autonomy

| Subsystem | File | Stub / gap | Category | Intended future state | Priority |
|-----------|------|------------|----------|------------------------|----------|
| Shutdown | `argus/autonomy/shutdown.py` | `resource_cleanup_stub` — see subsection below (stable JSON; no external teardown). | Placeholder (future) | Provider hooks (AWS, DNS, billing) behind env/policy gates. | P1 |

### `resource_cleanup_stub` (autonomy shutdown)

**Stable payload** (return value of `resource_cleanup_stub`; also the `resource_cleanup` field on `ProductShutdownReport.to_jsonable()` after apply, and nested under the dry-run `resource_cleanup` step’s `data`):

| Key | Type | Meaning |
|-----|------|---------|
| `executed` | bool | Always `false` (no automated teardown ran). |
| `stub` | bool | Always `true`. |
| `product_id` | str | Target product id. |
| `archive_path` | str \| null | Path string when archive exists (apply path); `null` in dry-run stub calls. |
| `suggested_providers` | list[str] | Hints (e.g. aws, dns, billing_saas) from `plan_provider_cleanup` — not executed. |
| `notes` | str | Set to `RESOURCE_CLEANUP_STUB_NOTES` in `resource_cleanup_stub` (human-readable; overrides provider seam copy). |

Shutdown reports also include an **`archive_plan`** (`argus.archive_plan.v1`) with structured steps and economics rationale; see `argus/autonomy/shutdown.py`.

**Not torn down:** Anything outside the repo workflow—cloud accounts (compute, DBs, object storage, serverless), DNS, TLS certs, SaaS seats, billing, secrets in external vaults, CI/CD or registry resources tied to the product, etc. Local changes are limited to manifest deprecation and moving `products/<id>/` to `archive/products/…` when apply succeeds.

**Operator manual steps (after apply):** Deprovision or transfer external infra per your runbooks; revoke or rotate credentials; update DNS and billing; verify cost and access are fully wound down. Treat the stub as a **reminder**, not automation.

---

## Products

| Subsystem | File | Stub / gap | Category | Intended future state | Priority |
|-----------|------|------------|----------|------------------------|----------|
| Scaffold | `argus/products/scaffold.py` | `_script_stub` — `scripts/start|stop|analyze.sh` local-safe no-op (`exit 0`; no network). | Intentional stub | Operators replace with real product commands. | P2 |
| Scaffold | `argus/products/scaffold.py` | Template `cost_notes` and other YAML fields use placeholder copy. | Intentional stub | Operators replace with real estimates. | P2 |

---

## Capabilities

| Subsystem | File | Stub / gap | Category | Intended future state | Priority |
|-----------|------|------------|----------|------------------------|----------|
| Registry | `argus/capabilities/registry.py`, `models.py` | `CapabilityMaturity` includes `stub`; declared gaps list “not implemented” items. | **Incomplete implementation** + awareness scaffold | Tighter registry sync with real findings/rules; optional automation. | P2 |
| Requests | `argus/capabilities/requests/integrations.py` | Advisor LLM gap when no API key; **not** a code stub — human-in-the-loop requests. | Intentional (operational) | Configure LLM or fulfill requests. | P2 |

---

## Shared packages

The empty `shared/{utils,config,logging,schemas}/` placeholder directories were removed
rather than published as abandoned scaffolding. Cross-cutting helpers, if added later,
should live under `argus/` next to their consumers. Published JSON Schema / OpenAPI is
still a future item; runtime types live in `argus/core/models/` (see
[argus-object-model.md](argus-object-model.md)).

---

## Adapters

| Subsystem | File | Stub / gap | Category | Intended future state | Priority |
|-----------|------|------------|----------|------------------------|----------|
| Adapters | `argus/adapters/` (`base.py`, `pipeline.py`, `loader.py`, `registry.py`, `cli.py`, `builtins/`) | **Implemented:** layered `collect` → `normalize` → `to_signal_records`, registry, builtins (execution, filesystem, metrics, temporal) wrapping `argus.signals.adapters`; optional merge via `config/adapters.json` / `argus signals collect --merge-adapter-layer`. | **Real** + small gaps | Reserved `api` category / external integrations; optional published JSON Schema / OpenAPI contracts (see [adapters.md](adapters.md)). | P1 |

---

## Audit (multi-angle product bundle)

| Subsystem | Location | Notes |
|-----------|----------|-------|
| Product audit | [`argus/audit/`](../argus/audit/) | **Implemented:** `argus audit run` writes `runs/audit/<product_id>/bundle.json` with nine angles (`product_gap`, `cost`, `quality`, `security`, `compliance`, `reliability`, `performance`, `store_business`, `ux`). Deterministic scanners; optional **`argus audit ingest-cursor`** / **`ingest-agent`** merge validated **`cursor_scan`** JSON into the bundle (same merge path; see `argus/audit/` ingest). `STUB_ANGLE_IDS` in `argus/audit/angles/stub.py` is empty in normal operation—no generic stub payload is injected for those nine keys. (Do not confuse with **`manifest_declaration`** signal rows from product signal manifest reconciliation—those are collection-gap records, not audit angle stubs.) |

Heuristic **findings** (rules over signals) live in `argus/findings/`. **Self-audit** of the Argus tool itself remains `argus self` / `argus.self`.

### Signals — optional Cursor interpretation (not a stub)

**`argus signals cursor-prompt`** / **`cursor-ingest`** persist **`runs/signals/review/<product_id>.json`** (`argus.signal_review_bundle.v1`). This is an optional, file-ingested **interpretation layer** (no LLM inside Argus); it does **not** modify deterministic **`runs/signals/latest/`**. Do not confuse with: **product signal manifest** (`argus.product_signal_manifest.v1` in `signals.yaml` / `product.yaml`) or **local snapshot manifest** (`argus.local_snapshot_manifest.v1` under `metrics/snapshots/local/`).

---

## Planner name

| Subsystem | File | Stub / gap | Category | Intended future state | Priority |
|-----------|------|------------|----------|------------------------|----------|
| Planner | `argus/planner/__init__.py` | Re-exports `argus.planning`; emits `DeprecationWarning`. | **Deprecated** | All imports use `argus.planning` or `argus planning` CLI. | P2 |

---

## Dashboard & UI

| Subsystem | File | Stub / gap | Category | Intended future state | Priority |
|-----------|------|------------|----------|------------------------|----------|
| Dashboard | `argus/dashboard/render.py` | HTML `placeholder` attributes on filter inputs (UI hint text, not code stubs). | Intentional (UX) | N/A | — |

---

## Defensive `pass` / swallowed errors (not “stubs”)

These are **not** feature stubs; they swallow errors for optional paths or resilience. Review if you need stricter diagnostics:

| File | Notes |
|------|--------|
| `argus/planning/render.py` | Optional `self_improvement` weekly section — `except: pass` if import fails. |
| `argus/dashboard/data.py` | JSON parse failures return empty/partial dashboard blocks. |
| `argus/signals/persistence.py` | Temporal sidecar write after `save_collection` — `OSError` logged at **WARNING** (collection still succeeds). |
| `argus/temporal/visibility.py` | `OSError` on `stat` / portfolio compare: **WARNING** when pipeline or dashboard comparisons fail; **DEBUG** when a globbed findings file disappears before `stat` (benign race). |
| `argus/advisors/cli.py` | `OSError` when recording capability requests — **WARNING** log; CLI continues. (`argus/experiments/cli.py`, `argus/execution/cli.py` still silent.) |
| `argus/autonomy/activate.py` | `except ...: pass` if autonomy state JSON unreadable during activation check. |
| `argus/autonomy/controller.py` | `except OSError: pass` in capability emit helper. |
| `argus/self_improvement/findings.py` | Bare `pass` in optional analysis branches. |

---

## Summary: most critical gaps

1. **Published adapter contract schemas** — Runtime types live in `argus/core/models/`; there is no separate JSON Schema / OpenAPI tree (the **`argus/adapters/`** layer is implemented; see [adapters.md](adapters.md)).
2. **`resource_cleanup_stub`** — No automated cloud teardown after product archive.
3. **Capabilities registry** — Awareness scaffold (`maturity=stub`, declared gaps) vs deeper automation.

---

## Related docs

- [`architecture.md`](architecture.md) — subsystem map and **real vs stubbed** overview.
- [`adapters.md`](adapters.md) — adapter layer pipeline, registry, `config/adapters.json`, CLI.
- [`signals-and-adapters.md`](signals-and-adapters.md) — `SignalAdapter` model, classic `argus.signals` path, and how it relates to the layered adapters.
- [`README.md`](../README.md) — repo status and links.
- [`argus/orchestrator/README.md`](../argus/orchestrator/README.md) — `argus loop run` stages and orchestrator boundary.
- [`system-flow.md`](system-flow.md) — operator loop and planning/escalation vs **`argus loop run`**.
