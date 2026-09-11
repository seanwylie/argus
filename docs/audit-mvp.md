# Audit MVP — Product Gap angle (scoped capability maps)

This document describes the **first shipped slice** of product audit in Argus: the **Product Gap** angle only — deterministic, **evidence-backed** capability coverage vs `product.yaml` (and related local declarations). It is **not** the full multi-angle framework; see **[audit-architecture.md](audit-architecture.md)** for Security, Compliance, Cost, Quality, and how angles compose without flattening into one model.

The **canonical multi-angle artifact** is **`runs/audit/<id>/bundle.json`** (see [audit-architecture.md](audit-architecture.md)); **`latest.json`** is the legacy **Product Gap** alias for loaders and idea gating.

Broader vision limits still apply: no Cursor-backed deep audit, outsider councils, repo-wide semantic analysis, or competitive scans in this pass.

## What it does

- Runs a **quick**, **bounded** filesystem scan under each product’s declared tree (`product.yaml`, `scripts/`, `metrics/`, `app/`, `src/`, etc.—see `argus.audit.scanner`).
- Emits a structured artifact **`argus.audit_summary.v1`** at `runs/audit/<product_id>/latest.json` with:
  - **Capability rows** (`AuditCapabilityEntry`): id, description, **status**, confidence, **evidence** list, notes.
  - **Counts** by status, scanned paths, exclusions, **inputs fingerprint** (cache invalidation).
- Feeds **context packets** (`audit.summary` slice) for **`idea_generation`** and **`refinement_grounded`** when `ARGUS_CONTEXT_PACKETS=1`—bounded lists: already implemented, known missing, partial, unknown, plus coverage metadata.
- **Light idea gating** (`argus.idea_generation.audit_gating`): token-overlap heuristics nudge scores when an idea overlaps **implemented** capabilities (down) or **missing** gaps (small up). **Unknown** is not treated as missing and is not heavily penalized.

CLI: `argus audit run|show|list|summary` (see `--help`, `--json`).

## What it does not do yet

- No Cursor CLI / IDE integration for deep code understanding.
- No outsider councils or LLM-based audit conclusions (audit evidence is **local files + yaml**, not model inference).
- No convergence changes in refinement.
- No giant repo-wide or cross-product scans beyond the inventory-driven product list for `audit list` / doctor.

## Capability status meanings

| Status | Meaning |
|--------|---------|
| **implemented** | Clear local evidence (e.g. non-trivial script on disk, metrics dirs with files, enabled signals in yaml). |
| **partial** | Something exists but is weak or incomplete (e.g. empty metrics dir, placeholder script). |
| **missing** | Declared expectation in yaml points to a path that is absent or clearly empty where a file was required. |
| **unknown** | Insufficient evidence to claim missing vs present (e.g. `metrics.local_paths` empty—we cannot infer layout). |

### Why **unknown** is not **missing**

**Missing** asserts “we looked for what the manifest implies and did not find it.” **Unknown** means “we cannot conclude from this quick scan.” Collapsing unknown into missing would **over-claim** gaps and pollute idea generation and context. The scanner and capability map **default to unknown** when conservative.

## How status is determined (heuristics)

1. **Actions** (`actions.start` / `stop` / `analyze`): resolve paths under the product root; **implemented** if script exists and passes a small non-triviality check; **missing** if declared path missing; **unknown** if path unsafe or unresolvable.
2. **Metrics** (`metrics.local_paths`): if empty → **unknown**; if paths missing → **missing**; if dirs exist with files → **implemented**; dirs but no files → **partial**.
3. **Signals**, **doctrine**, **cost** notes: additional rows from yaml/files (see `argus.audit.capability_map`).

All of this is **deterministic** and **inspectable** in the JSON artifact.

## Feeding context and ideas

- **Context** — `argus.context.assemble._build_audit_summary_slice` adds a compact `audit.summary` object (caps in `ContextCaps`: `max_audit_*_ids`).
- **Ideas** — After batch scoring, `apply_audit_to_ideas` adjusts scores when overlap ≥ 2 tokens with capability id/description tokens; records **`Idea.audit_adjustment`** for transparency.

## Operator workflow

1. `argus audit run --product-id <id>` after product layout changes.
2. `argus doctor` — informational lines when audits are missing; warnings for invalid JSON under `runs/audit/`.
3. Use `argus ideas generate` with optional context packets so prompts include audit summaries.

## Related

- [audit-architecture.md](audit-architecture.md) — multi-angle audit roadmap (shared vs angle-specific; MVP order).
- [architecture.md](architecture.md) — subsystem row for `argus.audit`.
- [idea-generation.md](idea-generation.md) — audit-aware nudges (Product Gap).
- [artifact-refinement.md](artifact-refinement.md) — context packets for refinement.
- [system-flow.md](system-flow.md) — optional audit step.
