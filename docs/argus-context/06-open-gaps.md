# Open gaps (vs shipped reality)

Companion to the burn-down table in [`north-star.md`](north-star.md). This file is intentionally short: it records **where truth layers or posture still lag** what the north star describes, and anchors audit expectations so edits to `north-star.md` stay reviewable against concrete contracts.

## Audit: nine-angle bundle

Deterministic multi-angle audit is **shipped**: `argus audit run` writes **`runs/audit/<product_id>/bundle.json`** (`argus.audit_bundle.v1`) with **nine** angle keys always present (`product_gap`, `cost`, `quality`, `security`, `compliance`, `reliability`, `performance`, `store_business`, `ux`). Optional **`ingest-cursor`** / **`ingest-agent`** merge validated interpretation into the same `bundle.json`. See [`docs/audit-architecture.md`](../audit-architecture.md) and [`docs/model-contracts.md`](../model-contracts.md).

## Gaps worth tracking

- **Learning loop** — outcomes, policy feedback, and patterns exist; closing the loop from experiment → sustained behavior change is still **emerging** vs a mature operator practice.
- **Autonomous operation** — scheduler and cycles are **bounded** by design; breadth of safe unattended operation is intentionally limited until guardrails and evidence mature.
- **Structural depth** — orchestration eligibility and portfolio logic are powerful but concentrated in large modules; consolidation and operator-facing coherence remain ongoing (see [`docs/architecture/state-of-the-system.md`](../architecture/state-of-the-system.md)).

When you change **`north-star.md`**, update this file if the burn-down table or audit posture claims shifted.
