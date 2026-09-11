# Escalation inbox (autonomy boundary)

The **escalation inbox** is a first-class, file-backed operator queue for issues that **genuinely need a human** — distinct from the broader **intervention inbox** (routine portfolio signals) and from **routine blocked lifecycle promotions** (expected guardrails).

Schema: `argus.escalation_inbox.v1` under `runs/portfolio/escalation_inbox/latest.{json,md}` (plus stamped copies).

## What feeds the inbox

Deterministic merge, newest-wins on duplicate fingerprints:

| Source | Typical category |
|--------|------------------|
| `runs/portfolio/autonomous_runner/latest.json` — `blocked_promotions` | Duplicate product dir / invalid inventory → **review_needed**; “plan already exists” → **informational** (deferred, no active queue) |
| Same — `stop_reason` / `stop_reason_codes` | Pipeline failure → **unsafe_to_continue**; intervention-heavy streak → **review_needed**; quiescence `wait` → **external_dependency**; max cycles / no-op stops → **informational** |
| `runs/portfolio/scheduler/latest.json` | Same stop mapping as autonomous (scheduler session) |
| `runs/portfolio/cycle/latest.json` | `overall_operator_recommendation` in the cycle “stop” set → **approval_needed** or **external_dependency** (imports) |
| Intervention inbox (subset) | High severity, chronic, or recurring “meaningful” rows → **review_needed** (linked via `esc-inv-{intervention item id}`) |
| `manual_items` on prior escalation artifact | Preserved across rebuilds for operator- or tooling-added rows |

## Categories

- **informational** — no default active-queue row unless acknowledged (routine operational context).
- **review_needed** — inspect and decide (merge conflicts, invalid inventory, intervention pressure).
- **approval_needed** — automation paused pending explicit operator intent (e.g. cycle overall stop).
- **external_dependency** — unblock imports, approvals, or waiting inputs outside Argus.
- **unsafe_to_continue** — pipeline stage failed; fix before more autonomous runs.

Each item includes `requires_operator_action`, `severity`, `evidence_summary`, `requested_action`, `content_fingerprint`, recurrence fields when history is available, and `source_refs` to originating artifacts.

## Operator actions (append-only)

Actions live under `runs/portfolio/escalation_inbox/actions/*.json` (schema `argus.escalation_inbox_action.v1`). State is merged in **chronological order** by `at_utc` (then filename).

- **acknowledge** — seen / triaged.
- **resolve** — fingerprinted; if the same signal reappears, the item **reopens** (like intervention resolve semantics).
- **snooze** — hide from the active queue until `snooze_until_utc`.
- **escalate_priority** — visibility marker (`ack_state` = `escalated_priority`).

## CLI

```bash
argus portfolio escalation-inbox [--json] [--no-save] [--recent-session-limit N]
argus portfolio escalation-ack --item-id esc-… [--note …] [--json] [--no-save]
argus portfolio escalation-resolve --item-id esc-… [--note …] [--json] [--no-save]
argus portfolio escalation-snooze --item-id esc-… --days N [--note …] [--json] [--no-save]
argus portfolio escalation-escalate --item-id esc-… [--note …] [--json] [--no-save]
```

## Tests

```bash
uv run pytest tests/test_escalation_inbox.py
```

## Relation to intervention inbox

- **Intervention inbox** — all actionable intervention rows from the portfolio intervention report (with ack/snooze/resolve tuned to intervention fingerprints).
- **Escalation inbox** — smaller, higher-friction set: autonomy stops, escalation-grade blocked promotions, cycle/scheduler guardrails, and intervention rows that look **chronic / high severity**.

Use escalation when deciding whether **unattended automation** should continue; use intervention for day-to-day portfolio triage.
