# Argus North Star

> **This document describes an intended end state, not current capability.**
>
> It is the design target the project aims at, written in the present tense throughout
> because that is how the target is specified. Most of what follows is **not** implemented.
> Deployment, third-party service integration, and revenue instrumentation in particular do
> not exist in any form.
>
> For what actually works today, skip to [Current state (Burn-down)](#current-state-burn-down)
> below, or read [`docs/architecture/state-of-the-system.md`](../architecture/state-of-the-system.md),
> which is grounded in the code rather than the goal.

## Core intent

Argus aims to become an autonomous, audit-grounded system that **invents, builds, operates, and retires a portfolio of real products** — from simple tools to complex branded ecosystems with revenue generation — across any industry, subject matter, or technology stack.

Each product declares its own **mission** (objective, supporting drivers, guardrails, risk posture) via `config/mission_profiles.yaml` and per-product `product.yaml`. Argus remains **mission-agnostic in its truth layers** (signals, audit, findings) and **mechanical in its builder**: it executes declared intent without opinion, preference, or creative agenda. Mission and ethics live in the policy layer. The builder builds whatever the pipeline decided.

Argus itself does not hardcode goals — it **executes** declared intent where operators attach it.

Critically:

> **Ethos shapes decisions, not truth. The builder is mechanical, not opinionated.**

All understanding is grounded in verifiable evidence. The mission influences what Argus chooses to do, not what it believes to be true. The builder implements what the pipeline decided, not what it thinks is best.

---

## The closed loop

Per product, continuously:

1. **Observe** — collect live signals from real-world sources (Stripe revenue, Google Analytics traffic, PostHog events, AWS cost/performance, market data, temporal feeds) and verify reality through deterministic audits of the product's codebase and infrastructure.
2. **Understand** — generate findings grounded in what exists, what is missing, and what is working.
3. **Decide** — prioritize opportunities into a living roadmap, shaped by the product's declared mission.
4. **Refine** — transform ideas into product specs and implementation plans through structured councils (grounded and outsider perspectives).
5. **Build** — execute plans using a mechanical builder agent that produces code changes, reviewed and merged through the audit layer.
6. **Deploy** — provision infrastructure, deploy to staging and production, wire third-party services (payments, AI, hosting, analytics).
7. **Validate** — observe real-world outcomes (revenue, traffic, cost, engagement, errors), feed signals back into the loop.
8. **Learn** — track execution outcomes against the hypotheses that justified them; transfer patterns across products; adjust strategy.

This loop runs continuously across a portfolio of products, with shared learning across all systems.

---

## Product lifecycle responsibility

Argus is responsible for the full lifecycle of every product in its portfolio:

- **Invention** — detecting portfolio gaps from market signals, strategy, and mission alignment; proposing new product concepts grounded in evidence.
- **Creation** — scaffolding product structure, infrastructure definitions, and service integrations; bootstrapping the first pipeline loop.
- **Curation** — building features, fixing defects, improving performance, refining quality — mechanically executing what the pipeline decides.
- **Experimentation** — testing hypotheses and validating outcomes with real production data and real user behavior.
- **Deprecation** — identifying underperformers, planning retirement, tearing down infrastructure, and removing products that no longer justify their cost or serve the mission.

Argus does not accumulate systems. It maintains a **lean, continuously optimized portfolio**.

---

## What products look like

*Target state. None of the integrations below are implemented; the only product in this
repository is a fictional static-site fixture.*

The intent is that Argus builds **real, deployed, revenue-capable products** that leverage the full landscape of third-party services:

- **Technology range:** web applications, mobile apps (React Native, Flutter), API services, CLI tools, AI/ML-powered features, 3D/immersive experiences, voice/audio products, complex branded ecosystems.
- **Service integration:** AWS (Lambda, S3, DynamoDB, CloudFront, Cognito, SES), OpenAI, ElevenLabs, Meshy, Stripe, Twilio, SendGrid, Supabase, Vercel, and whatever else fits the product's mission and type.
- **Revenue models:** subscription (Stripe), advertising, marketplace, freemium, enterprise licensing — wired into the signal layer for real performance tracking.
- **Industry agnosticism:** education, entertainment, commerce, developer tools, creative platforms, media, health, finance — the builder is mechanical and the mission is per-product.

The aim is that products are not prototypes or stubs: deployed, monitored, revenue-generating (when the mission calls for it), and continuously improved. Today none of that deployment or revenue path exists.

---

## End state vision

At maturity, Argus behaves like a disciplined, self-directed product organization in software form:

- It **invents** new products when portfolio strategy and market signals reveal gaps aligned with the operator's mission direction.
- It **builds and ships** features and improvements independently when confidence is high and constraints are satisfied, using a mechanical builder that works across any technology stack.
- It **operates** deployed products: monitoring cost, performance, errors, and user engagement through live signal adapters.
- It runs **experiments** and evaluates results using real production data.
- It actively **reallocates** effort across the portfolio based on performance, mission alignment, and strategic posture.
- It **deprecates and retires** systems that no longer perform — tearing down infrastructure, redirecting traffic, and freeing portfolio attention.
- It **learns** from what it ships: tracking outcomes against hypotheses, transferring successful patterns across products, and surfacing what the system is learning through the operator dashboard.

It escalates intentionally when:

- external access is required (API keys, cloud accounts, service credentials),
- risk exceeds autonomy thresholds,
- ambiguity cannot be resolved deterministically,
- a high-stakes lifecycle transition (first production deploy, first retirement) needs operator confirmation.

Argus operates with **graduated autonomy**: acting when safe, pausing when uncertain, escalating when necessary. As trust is established through successful outcomes, the autonomy boundary widens.

---

## Core identity

**Argus is not:**

- a content generator
- a static planner
- an LLM wrapper
- a code assistant

**Argus is:**

a closed-loop, audit-grounded, multi-product autonomous system that invents, builds, deploys, operates, and retires real software products in pursuit of declared missions — with human oversight only where it meaningfully matters.

---

## Architectural invariants

These do not change regardless of how far the system progresses:

1. **Truth is impartial.** Signals, audit, and findings never know what the mission is. A revenue product and an education product produce the same factual findings from the same evidence.
2. **The builder is mechanical.** It does not have opinions, preferences, or style. It executes plans. It can build a gambling app or a meditation app with equal competence and zero judgment. Mission and ethics live in the policy layer, not the builder.
3. **Everything is inspectable.** Every decision, every deployment, every signal, every finding, every promotion — written as schema-tagged JSON under `runs/`. If you can't trace why Argus did something, it shouldn't have done it.
4. **Bounded autonomy.** Guardrails are enforced, not advisory. As the system matures, the bounds widen — but they never disappear. The escalation inbox always exists. The operator always has a kill switch.
5. **File-based durable state.** No hidden databases, no opaque model state, no runtime-only knowledge. The repo + `runs/` + `products/` is the complete system state.

---

## Grounding principle

Every action Argus takes must be rooted in:

- **context** — what is known and relevant
- **audit truth** — what actually exists
- **structured reasoning** — refinement and councils
- **deterministic constraints** — safety, autonomy, and convergence

LLMs and agents contribute ideas and judgment, but:

> **Argus decides and acts based on evidence, not suggestion.**

---

## Mission and ethos

Each product operates under a declared **mission**, which defines:

- the primary **objective** (e.g. revenue, education, impact)
- supporting **drivers** (e.g. engagement, creativity, retention)
- **guardrails** (constraints that bound how aggressively the objective is pursued)
- **risk posture** (conservative → aggressive)

Mission is per-product, compositional, and inspectable:

- Declared in `product.yaml` as structured `mission: { objective, drivers, guardrails, risk_posture }`
- Resolved through `config/mission_profiles.yaml` (named profiles with concrete policy adjustments)
- Applied as operator policy overlays: objective at full strength, drivers at partial strength, guardrails as constraints
- Effective policy written as a traceable artifact per cycle (`mission_integration` block)

Mission influences:

- queue priority scoring and portfolio attention allocation
- quiescence sensitivity and intervention thresholds
- confidence gates and readiness requirements
- creation proposal direction and deprecation scoring
- experimentation strategy

Mission does not influence:

- signal collection
- audit results
- factual findings

This separation ensures Argus remains **truthful first, strategic second**.

---

## What success looks like

You wake up and see:

- A new product invented overnight based on market signals and portfolio strategy, scaffolded with infrastructure and service integrations, bootstrapped through its first pipeline loop.
- An existing product improved: a feature built, tested, deployed, and generating measurable results.
- An underperformer identified and cleanly retired: infrastructure torn down, traffic redirected, portfolio attention freed.
- Revenue tracked in real time across the portfolio, with mission-segmented effectiveness analysis showing which strategies are working.
- A clear report of what changed, why it changed, and what's next.
- Only a small number of intentional escalations waiting for you:
  - need an API key for a new service
  - need account approval for a cloud provider
  - a high-stakes deployment needs confirmation
  - a deprecation plan needs sign-off

---

## Current state (Burn-down)

**Bounded autonomous steward** — the analytical, lifecycle, and autonomous operation layers are real and deep. The system is **credibly stateful**: durable artifacts under `runs/` and explicit **orchestration state** make the loop inspectable. It reasons, decides, and manages lifecycle.

Execution is bounded rather than absent. Argus can select a target, declare a scope contract, hand the work to an external coding agent under containment, and classify what came back — but only for products shaped like a catalog of ordered content slots, and it writes no application code itself. The remaining gaps are deploying, operating, and integrating real third-party services.

For the phased plan from current state to end state, see **[`docs/plans/ephemeral-roadmap.md`](../plans/ephemeral-roadmap.md)**.

For code-grounded architectural detail, see **[`docs/architecture/state-of-the-system.md`](../architecture/state-of-the-system.md)**.

For maintained gaps vs shipped reality, see **`06-open-gaps.md`** in this folder.

| Area | Status |
| --- | --- |
| Signals → findings → decisions | **Real** — normalized pipelines with temporal sidecars. |
| Live signal ingestion (Stripe, GA, PostHog, AWS) | **Not yet** — file-based ingesters exist; API adapters are the next phase. |
| Multi-angle audit | **Real** — per-product bundles with nine audit dimensions. |
| Structured product mission | **Real** — per-product objective/drivers/guardrails/risk_posture with policy overlay. |
| Mission-conditioned policy | **Real** — per-product policy composition, effectiveness analysis, learning synthesis. |
| Refinement | **Real** — councils, sessions, convergence, implementation plan artifacts. |
| Orchestration posture | **Real** — deterministic eligibility, 17-step Phase-2 chain, state artifacts. |
| Planning & escalation | **Real** — structured outputs aligned with orchestration state. |
| Portfolio reasoning | **Real** — queue, progression, quiescence, delta, intervention, outcomes, strategy. |
| Product lifecycle (creation) | **Real** — proposals, scaffold, bootstrap, promotion workflows. |
| Product lifecycle (deprecation) | **Real** — proposals, plan artifacts. Execution (teardown) not yet present. |
| Learning loop | **Real** — outcomes, effectiveness, feedback, patterns, recommendations, learning synthesis. |
| Autonomous operation | **Real** — persistent runner service with cadence, heartbeat, bounded promotions. |
| Operator console | **Real** — Streamlit dashboard with 7 tabs, heartbeat, escalation inbox, learning. |
| Policy control plane | **Real** — configurable, inspectable, experimentable, mission-aware. |
| Human loop | **Real** — intervention inbox, escalation inbox, escalation actions. |
| Builder agent | **Partial** — a bounded harness around an *external* coding agent, not a code generator of its own. Deterministic target selection from a product's content catalog, a declared execution contract with an allowed-path scope, opt-in invocation (`invoke --execute`) with OS-level containment on Linux, and reconcile-time scope-breach detection, diff capture, and outcome classification. Argus writes no application code itself; the agent does, and Argus decides what to ask for and verifies what came back. Applies only to products shaped like a catalog of ordered content slots. |
| Deployment / infrastructure | **Not yet** — no infra-as-code generation, no deploy pipeline. |
| Third-party service integration | **Not yet** — no service registry, no SDK scaffolding. |
| Outcome closure (build → observe) | **Partial** — execution outcomes bridge exists; hypothesis-to-outcome tracking not complete. |

---

## Final framing

Argus is evolving toward:

> **an autonomous, audit-grounded product organization in software form that invents, builds, deploys, operates, and retires real products across any industry and technology stack — learning from every outcome and escalating only when it genuinely matters.**
