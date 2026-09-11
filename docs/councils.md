# Councils: grounded vs outsider review

Argus separates **repo-aware grounded reviewers** from **intentionally limited outsider reviewers**. Both participate in the same refinement **round** (draft → reviews → synthesis → convergence), but they have different **context policies**, **backends**, and **convergence authority**.

## Why two classes

- **Grounded** reviewers consume richer context packets (product, findings, audit summary when enabled, temporal signals when appropriate). They are the right place for **feasibility**, **doctrine fit**, **architecture**, and **implementation plausibility** judgments.
- **Outsider** reviewers receive **reduced context** (pitch/market slice). They answer pressure questions: clarity, investability, differentiation, “why care”—without claiming **repository truth**.

Advisors remain **interpretation**, not ground truth ([`docs/argus-context/08-operating-principles.md`](argus-context/08-operating-principles.md)).

## Authority rules (deterministic convergence)

- **Approval / reject** math uses **grounded** reviews only (`counts_toward_convergence_gate` on `CouncilMemberProfile`).
- **Outsider** `FAIL` does **not** veto a grounded-passing artifact. Reasons include `outsider_fail_count:N` for visibility.
- **Outsiders** cannot hard-approve feasibility; `can_hard_block` is **false** for outsider stakeholder types (`investor`, `marketer`, `customer_proxy`).
- Optional policy: `human_review_if_outsider_blocking_count` on `OutsiderInfluencePolicy` can route to `human_review_required` when grounded metrics pass but many outsiders fail (see `argus/refinement/convergence.py`).

## Routing by artifact type

Defaults live in `argus/council/profiles.py`:

| Artifact | Grounded (summary) | Outsider (summary) |
|----------|--------------------|---------------------|
| `idea` | product, finance, technical | investor, marketer, creative (optional) |
| `product_spec` | product, doctrine, ux; optional technical, finance | marketer, customer_proxy (optional) |
| `implementation_plan` | technical, architecture, bones, product | none by default |

Inspect with:

```bash
argus council profiles
argus council show idea
argus council run --type idea --source <opaque_id>
```

Execution of rounds remains **`argus refine start` / `argus refine run`** — the `council` CLI is for **inspection**, not a second orchestrator.

## Context policies

Mapped in `argus/council/context_delivery.py` to `argus.context.assemble_context_bundle` purposes:

- `full_grounded` → `refinement_grounded` (temporal + audit when configured)
- `compact_grounded` → `idea_generation` (audit, no temporal)
- `implementation_grounded` → `council_implementation_grounded`
- `outsider_pitch_only` / `outsider_market_only` → slim product + draft; **no audit block**; explicit `context_slice_note`

## Backends

- **Deterministic**: always available; structured stub reviews.
- **OpenAI**: used when `BackendType.openai` and LLM env allows (same gating as other Argus LLM paths).
- **Cursor**: requested for grounded seats via `BackendType.cursor`; **not wired** to an external Cursor CLI yet. `resolve_active_backend` falls back to deterministic; reviews may show `cursor_placeholder_fallback`.

See `argus/council/backends.py` and `argus/council/runner.py`.

## Artifacts and synthesis

- `StakeholderReview` carries `council_mode` (`grounded` | `outsider`) and `backend_used`.
- `ReviewSynthesis.theme_items` lists `{source, stakeholder, issue}` for mixed grounded/outsider themes.
- Session `meta.council_profiles` stores the resolved profile for auditability.

## Doctor

`argus doctor` runs `argus.council.doctor.check_council_profiles` (malformed defaults, outsider-only profiles).

## Related docs

- [artifact-refinement.md](artifact-refinement.md) — refinement loop
- [llm-integration.md](llm-integration.md) — LLM gating (distinct from `argus llm council` product artifact)
