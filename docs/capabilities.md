# Capabilities (gaps and requests)

The **capabilities** subsystem tracks **declared** vs **inferred** gaps and records **capability requests** when something is blocked or missing.

## Declared capabilities

Shipped behaviors are listed in `argus/capabilities/registry.py` (`current_capabilities()`). Each entry has a **maturity** (`CapabilityMaturity` in `argus/capabilities/models.py`):

| Value | Meaning |
| --- | --- |
| `stub` | Minimal or scaffold integration—present for awareness and CLI wiring, not a production-grade surface for that concern. |
| `basic` | Usable end-to-end for its scope with documented limitations (`known_gaps`). |
| `advanced` | Reserved for richer or more complete implementations (none may be declared yet). |

Today, **`cap.orchestrator.loop`** is the only declared capability at **`stub`** maturity: **`argus loop run`** / **`loop full`** are implemented locally, but the registry marks this as **not** a production remote orchestrator (`known_gaps` in `registry.py`).

Use **`argus capabilities list`** (or **`argus capabilities list --json`**) to print the registry; text output includes `[category] maturity` on the second line of each block.

## Requests

- Stored under `runs/capabilities/requests/<request_id>.json`.
- Sources include execution blocks, autonomy policy blocks, advisor LLM gaps, etc. (`CapabilityRequestSource`).
- Status workflow: open → acknowledged → resolved/rejected/cancelled (see `CapabilityRequestStatus`).

## CLI

- `argus capabilities evaluate` / `gaps` — snapshot analysis.
- `argus capabilities request list|show|create|...` — manage requests.

## Coherence

When autonomy or execution refuses work, integrations may call `record_autonomy_policy_block` or `record_execution_blocked` so operators see a **request** instead of a silent failure.

**Doctor** surfaces open request counts and autonomy streaks.

## Determinism

Listing and evaluation are local filesystem operations; no network required unless you add external checks later.

## Analytics and accounts

- **Snapshot ingestion** is always local (drop files under `products/<id>/metrics/snapshots/`). See [analytics-integrations.md](analytics-integrations.md) for adapter filename hints and how live ETL should land data.
- **Hosted analytics / cloud projects** (PostHog projects, GA properties, AWS accounts, etc.) are **not** created by Argus CLI. Use **`argus capabilities request create`** (or your process) when you need API keys, billing, or org-level setup.

## Product bootstrap

After `argus products create`, **`argus products bootstrap <product_id>`** adds doctrine template, metrics placeholders, and an experiment seed file — still filesystem-only; no vendor APIs.
