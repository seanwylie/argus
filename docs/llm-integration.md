# Optional LLM integration (augmentation only)

Argus remains **deterministic-first**. Optional OpenAI-backed features **extend** critique and ideation; they **do not** replace scoring, safety rules, autonomy tiers, or approval gates.

## Environment variables

| Variable | Role |
|----------|------|
| `ARGUS_LLM_ENABLED` | Master switch. If unset or false, **all** LLM features are off (no API calls). |
| `ARGUS_CONTEXT_PACKETS` | When `1` / `true`, artifact refinement uses structured **context bundles** (`argus.context`: `product.system` + `artifact.draft`) for draft and review prompts instead of legacy product-summary-only text. Default off. See [reasoning-context-refinement-architecture.md](plans/reasoning-context-refinement-architecture.md). |
| `ARGUS_OPENAI_API_KEY` | API key when LLM is enabled. If missing, Argus **falls back** to deterministic behavior and logs a warning (never logs or persists the key). |
| `ARGUS_OPENAI_BASE_URL` | Optional OpenAI-compatible base URL (default `https://api.openai.com/v1`). |
| `ARGUS_OPENAI_MODEL` | Optional model override (client default: `gpt-4o-mini`). |

Legacy: `ARGUS_ADVISORS_DISABLE_LLM=1` still disables advisor LLM config even when `ARGUS_LLM_ENABLED` is on.

## Idea expansion

**Flow:** deterministic generation → classification (exploit / explore / invent) → optional LLM expansion → batch scoring (novelty / diversity) → ranking.

- Canonical **`title`**, **`description`**, **`type`**, and score inputs are unchanged.
- Enrichment is stored under **`Idea.llm_expansion`** (e.g. `expanded_description`, `improved_title`, `concrete_examples`, `monetization_suggestions`).
- CLI: `argus ideas generate` (use `--no-llm-expansion` to skip), or `argus llm expand-ideas <product_id>`.

## Advisor council (LLM)

Five perspectives (finance, growth, product, technical, creative) run from prompts in `argus/llm/prompts/`. Output is **advisory** JSON (critique, risks, suggestions, optional score).

- **CLI:** `argus llm council <product_id>` writes `runs/advisors/llm_council_<product_id>.latest.json` (no secrets).
- **Decisions:** `argus confidence assess` / `evaluate_decision_context` reads this file when present and sets **`advisor_alignment_score`**, **`advisor_conflict_flag`**, **`advisor_summary`** on `DecisionContextAssessment`, with **small** nudges to confidence/uncertainty. Temporal freshness, risk, and constraints are **not** overridden.
- **Existing:** `argus advisors run` / `argus advisors consult` continue to use `argus.advisors` (stub or OpenAI) and write `runs/advisors/<product_id>.latest.json`.

## Safety guarantees

- LLM code paths **never** trigger execution, change autonomy mode/tier, or bypass approval.
- Outputs are labeled **advisory** in artifacts; operators should treat them as interpretation, not telemetry.

## Related

- [idea-generation.md](idea-generation.md) — pipeline order and schemas.
- [decision-confidence.md](decision-confidence.md) — assessment fields and factors.
- [autonomy-rollout.md](autonomy-rollout.md) — enforcement remains outside LLM.
