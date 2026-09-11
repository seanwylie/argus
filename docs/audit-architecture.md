# Multi-angle audit architecture (roadmap)

This document **continues** the Argus audit roadmap: deterministic-first, evidence-backed, scoped per product, **no** repo-wide “vibe scans,” **no** Cursor CLI requirement for MVP. It refines the plan so audit is a **framework of first-class dimensions**, not a single capability-map dressed in different labels.

**Current code** writes **`runs/audit/<product_id>/bundle.json`** (`argus.audit_bundle.v1`) with **nine angle keys always present** (`product_gap`, `cost`, `quality`, `security`, `compliance`, `reliability`, `performance`, `store_business`, `ux`). **`runs/audit/<product_id>/latest.json`** remains **`argus.audit_summary.v1`** (Product Gap only) for backward compatibility and idea gating.

Each angle includes **`angle_status`** (`active` | `partial` | `stub`), **`summary_lines`** (≥1 line; convention `signal: value (qualifier)`), and structured fields where applicable. **`inputs_fingerprint_by_angle`** plus **`inputs_fingerprint_bundle`** (hash of sorted per-angle hashes) support traceability and cache invalidation.

**Optional Cursor codebase scan:** `argus audit prompt` / `argus audit cursor-prompt` emit a structured JSON contract (`argus.audit_cursor_scan_batch.v1` by default, or **`argus.audit_cursor_scan.v1`** for a single angle via `--angle <id>`). **`argus audit ingest-cursor`** and **`ingest-agent`** are aliases: both merge validated scans into each angle as **`cursor_scan`** (alongside deterministic fields), set **`sources`** (`deterministic` / `cursor_scan`), and store **`deterministic_summary_lines`** for re-merge. A later **`argus audit run`** refreshes static evidence but **preserves** existing **`cursor_scan`** blocks.

Context consumes **`audit.summary`** (Product Gap) plus **`audit.audit_coverage`** and **`audit.angles`** built from **`summary_lines` only**—not raw angle payloads. **No** global audit score, merged master findings list, or cross-angle scoring unless explicitly revisited later.

The **implemented / partial / missing / unknown** four-state model is **Product Gap only**; other angles use their own semantics.

---

## 1. Role of each audit angle

Each angle answers a **different primary question**, consumes **typed evidence**, and emits **angle-appropriate findings**. MVP methods stay **local, bounded, deterministic**.

| Angle | Primary question | Evidence consumed (examples) | Findings emitted (MVP shape) | Deterministic MVP methods |
|-------|------------------|------------------------------|------------------------------|---------------------------|
| **Product Gap** | What does the manifest imply exists vs what is present on disk? | `product.yaml`, `doctrine.yaml`, declared paths, scripts, metrics dirs, shallow `app/` / `src/` | **Coverage rows**: capability id, status (implemented/partial/missing/unknown), evidence refs | Bounded tree walk, path resolution, size/triviality checks *(shipped)* |
| **Security** | What obvious local security posture signals exist (or are absent)? | Dependency lockfiles if present, env sample patterns in docs, `scripts/` shebangs, absence of committed secrets *(pattern lists only)* | **Checklist findings**: pass / warn / fail / unknown per rule id | Static rules over allowed files only; no dynamic scanning |
| **Compliance** | What policy hooks or audit trails does the product declare or expose locally? | Doctrine, autonomy/approval references in yaml, logging config if present | **Declaration vs evidence** rows (e.g. “approval required” vs `runs/approval/` activity) | Compare declared constraints to presence of local artifact paths |
| **Reliability** | What runbooks / health / restart signals exist locally? | `scripts/start|stop`, README ops sections, optional metrics error paths | **Presence + partial** findings (e.g. “stop script missing”) | Same bounded file checks as Product Gap plus doc headings |
| **Performance** | What performance-related artifacts or budgets are declared? | Doctrine or product notes, build config, static asset dirs if tiny | **Declared budget vs unknown measurement** (never fake latency numbers) | Parse declared numbers from yaml; **unknown** if no declared SLO |
| **Cost** | Economics alignment for this product | `product.yaml` cost, `config/economics/`, `runs/economics/` if present | **Orphan / mapping / declared monthly** findings *(reuse economics doctor patterns)* | Deterministic joins of known IDs and file presence |
| **Store & Business** | What commercial / distribution hooks are declared? | Store URLs, pricing fields in yaml, experiment records under `runs/experiments/` | **Declared channel** vs **local evidence** (e.g. experiment count) | Yaml + shallow `runs/` index |
| **Quality** | What test / lint / CI signals exist locally? | `pyproject.toml`, `package.json`, `.github/workflows` *under product scope or repo root policy* | **Tooling present / absent** per stack | Bounded glob under declared roots only |
| **UX** | What UX-facing artifacts exist (copy, flows, accessibility hints)? | Small set of UI files if `app/` or `src/` present, README user flows | **Surface / gap** findings (e.g. “no a11y checklist doc”) | File presence + keyword lines in bounded files |

**Note:** “Findings” for non–Product-Gap angles in MVP are **not** the same as `AuditCapabilityEntry` with four statuses everywhere. For example Security may emit **rule_id + severity + evidence**; Product Gap keeps **coverage status**.

---

## 2. Shared framework vs per-angle specialization

### Common across all angles (worth standardizing now)

- **Scope**: product root + explicit allowlist of subpaths (see `argus.audit.scanner` patterns); hard caps on files/depth.
- **Identity**: `product_id`, `generated_at_utc`, `inputs_fingerprint`, `schema` version string.
- **Evidence spine**: `kind`, `ref` (repo-relative or logical key), optional `detail` — already in `AuditEvidence`; angles may **extend** allowed `kind` values in JSON without merging angles into one flat list.
- **Artifact layout**: **`runs/audit/<product_id>/bundle.json`** (`argus.audit_bundle.v1`) is canonical; **`latest.json`** remains Product Gap–only for compatibility.
- **Context integration**: `audit.summary` + `audit.audit_coverage` + `audit.angles` (from **`summary_lines` only**, capped per `ContextCaps.max_audit_summary_lines_per_angle` in [`argus/context/caps.py`](../argus/context/caps.py)).
- **Doctor**: per-angle JSON validity + staleness hints; never fatal.
- **No convergence changes**; reasoning layers consume summaries as **additive** context.

### Angle-specific

- **Finding model**: Product Gap → coverage status enum; Security → rule results; Cost → economics linkage rows; etc.
- **Deterministic rulesets**: separate modules / tables per angle.
- **Downstream nudges**: idea gating for Product Gap (overlap with implemented/missing) is **not** copied blindly to Security (could instead **tag** risk themes).

### Cohesive but not flattened

Recommended artifact evolution:

- **Option A (incremental):** Keep `argus.audit_summary.v1` as **Product Gap only**; add `argus.audit_bundle.v1` wrapper that contains `product_gap: {...}` and optional `security: {...}` as they appear.
- **Option B:** One file per angle under `runs/audit/<id>/product_gap/latest.json`, etc.

**Recommendation:** When a second angle lands, introduce **`argus.audit_bundle.v1`** with **keys per angle** and migrate Product Gap payload under `angles.product_gap` *or* keep backward-compatible top-level fields as aliases for one release. Avoid forcing all angles into one `capabilities[]` array.

---

## 3. Relationships: run hierarchy, products, context, reasoning

| Concept | Definition |
|---------|------------|
| **Top-level audit run** | Operator command: `argus audit run --product-id X` (and optionally `--angles` later). Orchestrates **one or more angle runners** for one product in one invocation; single fingerprint for “what was scanned together.” |
| **Per-angle process** | Deterministic function: `(repo_root, product_id, scope) → angle artifact`. No cross-angle required ordering except shared scope resolution. |
| **Per-product results** | All angle outputs for that product + run metadata; persisted under `runs/audit/<product_id>/`. |
| **Context packet integration** | `assemble_context_bundle` adds **bounded** `audit.summary` today (Product Gap). Future: `audit.angles.<name>` each with caps; purposes (`idea_generation`, `refinement_grounded`) subscribe to **allowed angle keys** per purpose profile. |
| **Later reasoning layers** | LLM / advisors **read** audit slices; they do **not** become source of truth. Optional councils may reference audit refs by id; convergence unchanged. |

---

## 4. Product Gap vs operational / technical / business audits

| | **Product Gap** | **Other angles** |
|--|-----------------|------------------|
| **Core metaphor** | Coverage of declared product surface (features, scripts, metrics paths). | Obligation / risk / ops / economics questions. |
| **Status shape** | implemented / partial / missing / **unknown** (unknown ≠ missing). | May use pass/warn/fail, severity, or economics states — **do not** force the four-value enum. |
| **Idea/refinement tie-in** | Strong: duplicate vs implemented, boost vs missing gap. | Usually **weaker**: tags, “do not suggest X if security fail,” not score collapse. |
| **Data sources** | Manifest + local tree. | Manifest + economics store + CI files + rule packs. |

---

## 5. MVP implementation order

### Ship first (already done or next)

1. **Product Gap** — *Shipped* (`capability_map`, context slice, idea gating). Remains the reference **pipeline** for “angle runner + artifact + context + doctor.”
2. **Cost** — High leverage for Argus; deterministic data already (`economics`, product cost fields). Small **join** artifact; aligns with portfolio thinking.
3. **Quality** — Bounded globs for test/CI configs; clear pass/fail/unknown per stack signal.

### Stub soon (schema + no-op or minimal placeholder)

4. **Security** — Stub: `angles.security: { "status": "not_run", "schema": "argus.audit_angle.security.v0" }` or a **single** “rules_version” + empty findings[] until rules land.
5. **Compliance** — Stub: declaration extraction from yaml only.
6. **Reliability**, **Performance**, **Store & Business**, **UX** — Documented rule tables; implement as **empty or 1–2 rule** pilots when needed.

### Shared abstractions **now**

- Scope + fingerprint helpers (`scanner`), evidence dataclass, `runs/audit/` layout conventions, CLI entrypoint, doctor hook, context caps pattern.

### Shared abstractions **wait**

- Generic “finding” superclass used by all angles — **wait** until two non–Product-Gap angles exist; avoid premature abstraction.
- Cross-angle scoring or “audit health index” — **wait**; risk flattening angles.

---

## 6. Integration notes (context, ideas, refinement)

- **Context packets**: Add **per-angle keys** under `audit` when bundles exist; keep **strict byte caps** per angle. Product Gap remains default when `audit.angles` absent (backward compatibility).
- **Ideas**: Keep **Product Gap–specific** gating in `audit_gating.py`; add **separate** optional hooks (e.g. `security_tags`) later — **do not** merge into one overlap scorer.
- **Refinement**: Same as context — audit blocks are **facts**, not instructions; additive only.

---

## Related

- [audit-mvp.md](audit-mvp.md) — current Product Gap MVP (implementation detail).
- [architecture.md](architecture.md) — subsystem map.
- [08-operating-principles.md](argus-context/08-operating-principles.md) — temporal vs interpretation; stubs.
