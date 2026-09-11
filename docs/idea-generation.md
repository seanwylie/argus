# Idea generation (exploit / explore / invent)

Structured ideas are **deterministic, inspectable artifacts** under `runs/ideas/`. They sit **after decisions** in the recommended loop: decisions set posture; ideas propose **what to try next** without automatically creating experiments.

## Pipeline position

1. **Inputs** — Latest signals, findings, advisor snapshots (when present), and **ranked decision candidates** for scope.
2. **Generation** — `argus ideas generate` runs synthesis and optional mutation passes (`IdeaSource.SYNTHESIS`, `IdeaSource.MUTATION`), with additional rows from signals/findings/advisors where applicable.
3. **Classification** — Each idea gets an **`IdeaType`**: `exploit` (optimize known pattern), `explore` (adjacent), `invent` (novel combination) — applied after dedupe so the class reflects the final text.
4. **Optional LLM expansion** — When `ARGUS_LLM_ENABLED` and `ARGUS_OPENAI_API_KEY` are set, **`Idea.llm_expansion`** may hold richer text (examples, monetization hints). **Canonical title/description used for scoring are unchanged.** Use `--no-llm-expansion` to skip. When **`ARGUS_CONTEXT_PACKETS=1`**, expansion prompts are prefixed with the **`idea_generation`** context bundle (including **`audit.summary`** if `runs/audit/<id>/latest.json` exists). See [llm-integration.md](llm-integration.md).
5. **Scoring** — Novelty, adjacency, expected value, confidence, and **diversity impact** are computed in `argus.idea_generation.score` (see module docstrings for interpretation).
6. **Audit nudges (optional)** — When `runs/audit/<product_id>/latest.json` exists, `apply_audit_to_ideas` runs **immediately after** batch scoring (before doctrine nudges and final sort): simple **token overlap** against audited **implemented** / **missing** capability rows (see `argus.idea_generation.audit_gating`). Overlap with implemented capabilities slightly **lowers** expected value / novelty (duplicate-like ideas); overlap with **missing** rows gets a small **boost**. **Unknown** audit rows are **not** treated as gaps for penalization. Adjustments are recorded on **`Idea.audit_adjustment`**; bundle **`meta`** includes **`audit_ideas`**. See [audit-mvp.md](audit-mvp.md).
7. **Selection** — `argus.idea_generation.select` marks ideas as selected vs `rejected_duplicate` using fingerprints and policy; the bundle **`meta`** may record duplicate titles, portfolio-level diversity metrics, and advisor expansion sidecars.

Canonical on-disk bundle: **`argus.ideas_bundle.v1`** → `runs/ideas/latest.json`. Per-idea schema: **`argus.idea.v2`**.

## Novelty and diversity

- **Novelty** blends lexical signals with historical idea fingerprints (`argus.idea_generation.novelty`) so near-copies score lower.
- **Diversity** is not a separate quota system: the pipeline scores **diversity impact** and surfaces **portfolio diversity** metadata for operators (see `argus.idea_generation.diversity` and the dashboard ideas panel). Selection rejects obvious duplicates; it does **not** guarantee a fixed percentage of “invent” ideas — that would be policy tuning, not a hidden hard rule.

## Dashboard and operator visibility

The dashboard embeds **`argus.dashboard_ideas.v1`** (from `runs/ideas/latest.json`): type distribution, sample rows, rejected-duplicate titles, and diversity index when present. See [dashboard.md](dashboard.md).

## Doctrine interaction

When `products/<id>/doctrine.yaml` sets `scoring.experiment_score_boost`, **`argus ideas generate`** nudges **expected value** for `explore` / `invent` ideas only; metadata is under `meta.doctrine_ideas` in the bundle. This does not replace novelty/diversity logic—it adjusts ranking weights in one place.

## Refinement (optional)

For **council-backed iteration** on a concrete idea id, use **`argus refine`** (see [artifact-refinement.md](artifact-refinement.md)). Councils combine **grounded** and **outsider** seats for ideas — [councils.md](councils.md). This does not replace bundle generation; it adds staged review artifacts under `runs/refinement/`.

## Related docs

- [system-flow.md](system-flow.md) — where `argus ideas generate` fits in the operator loop.
- [model-contracts.md](model-contracts.md) — schema identifiers.
- [decision-confidence.md](decision-confidence.md) — decision dynamics vs idea scoring (different concerns).
- [doctrine.md](doctrine.md) — doctrine effects on findings/decisions/advisors.
