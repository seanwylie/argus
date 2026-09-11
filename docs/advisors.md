# Advisors

Multi-perspective consultation with **deterministic stubs by default** and optional OpenAI-compatible LLM calls.

## Archetypes

Defined in `argus.advisors.registry` (`global_advisors`): finance, investor, technical, marketing, product, creative. Each has a `prompt_template`, `weight`, and `AdvisorArchetype`.

## Per-product overrides

Optional `products/<id>/advisors.json`:

- `include_only` — restrict which advisor ids run
- `exclude` — drop advisors
- `weight_overrides` — numeric weight per advisor id (consensus normalizes)

Invalid JSON is reported by `argus doctor`.

## LLM (optional)

The OpenAI-compatible client in `argus.advisors.llm` uses **Python’s standard library only** (`urllib.request`, `json`, etc.). There is **no** optional extra such as `httpx` or `openai` to install for advisors—only network access and (when enabled) an API key.

- **Enable**: set `ARGUS_OPENAI_API_KEY`. Optional: `ARGUS_OPENAI_BASE_URL`, `ARGUS_ADVISORS_MODEL`.
- **Disable**: `ARGUS_ADVISORS_DISABLE_LLM=1` or unset the API key.
- **Failure behavior**: per-advisor try/except falls back to **`simulate_response`** (deterministic); metadata may include `llm_error`.

## Logging

Consultation artifacts go under `runs/advisors/consultations/<timestamp>_<product>/` (`context.json`, `per_advisor.json`, `run.json`). Payloads are passed through **secret redaction** (e.g. `sk-…` patterns) before write. API keys are sent only in HTTP headers for LLM calls, not embedded in prompts.

## Consensus

`argus advisors consensus <product>` runs advisors and merges responses in `argus.advisors.consensus` (stance-weighted aggregation + disagreement notes). See `tests/test_advisors.py` for behavioral coverage.

## Temporal grounding (evidence bounds)

Consultation loads `TemporalGrounding` (`argus.advisors.temporal`):

- **Artifact ages** — signals, findings, decisions, trends, experiments (staleness windows are deterministic).
- **Recent signal excerpt** — last N `SignalRecord` lines with timestamps (not live telemetry).
- **Collection recency** — when `runs/temporal/latest/<product>.json` exists, prompts include worst bucket / mean score from the derived bundle (`collection_recency` in `evidence_summary_dict()`).

System and user prompts state explicitly that advisors **interpret** evidence; they do not define operational truth. See **[temporal-intelligence.md](temporal-intelligence.md)**.
