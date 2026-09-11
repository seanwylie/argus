# Intervention inbox (`argus.intervention_inbox.v1`)

Turn portfolio **intervention detection** into a small, file-based **operator work queue**: acknowledge, snooze, resolve, ignore, or escalate items without any external services.

## Prerequisites

Build or refresh **`runs/portfolio/intervention/latest.json`** first (see [portfolio-intervention.md](portfolio-intervention.md)):

```bash
uv run argus portfolio intervention
```

The inbox reads that report and merges **append-only action records** under `runs/portfolio/intervention_inbox/actions/`.

## CLI

| Command | Purpose |
|---------|---------|
| `argus portfolio intervention-inbox` | Rebuild inbox JSON/Markdown from latest intervention + merged actions (`--recent-limit N`, `--json`, `--no-save`) |
| `argus portfolio intervention-ack --item-id ID` | Mark seen / acknowledged |
| `argus portfolio intervention-resolve --item-id ID` | Resolve with a **fingerprint** of current detection (reopens if the same issue keeps firing or changes materially) |
| `argus portfolio intervention-ignore --item-id ID` | Same reopen semantics as resolve; use when you want to dismiss without implying “fixed” |
| `argus portfolio intervention-snooze --item-id ID --days 7` | Hide from the **active** queue until the UTC deadline |
| `argus portfolio intervention-escalate --item-id ID` | Record escalation for tracking |

Shared options on action commands: `--note`, `--json` (print updated inbox JSON), `--no-save` (append action only; skip rewriting `intervention_inbox/latest.*`).

## Artifacts

| Path | Role |
|------|------|
| `runs/portfolio/intervention_inbox/latest.{json,md}` | Current inbox view (+ stamped copies when rebuilt) |
| `runs/portfolio/intervention_inbox/actions/*.json` | Append-only ledger (`argus.intervention_inbox_action.v1`) |

Actions are replayed in **sorted filename order** so merges are deterministic.

## Payload (summary)

Schema: **`argus.intervention_inbox.v1`**.

- **`open_items`**: one row per **meaningful** flagged product in the latest intervention report (noise categories such as `safe_to_ignore` are dropped; `continue_monitoring` + `low` severity is dropped).
- Each item includes: **`item_id`** (stable from product + category + sorted reason codes), **`intervention_category`**, **`severity`**, **`chronicity`**, **`detection_reason_codes`**, **`evidence_summary`**, **`recommended_operator_action`**, **`first_seen_run_id`** / **`last_seen_run_id`** / **`seen_in_run_count`** (from recent stamped intervention JSONs + `latest.json`), **`ack_state`**, **`snooze_until_utc`** (when snoozed), **`in_active_queue`** (false for resolved, ignored, or snoozed-until-future), and reopen hints when a resolve/ignore no longer matches the current detection.
- **`action_state`**: merged operator state keyed by `item_id` (for debugging / transparency).

## Semantics

- **Resolve / ignore** store a **resolution fingerprint** (category + severity + reason codes + product). If the detector still flags the **same** fingerprint, the item is treated as **open** again (“still flagged after resolve/ignore”). If the fingerprint **changes**, the row is a **new** issue (new `item_id` if codes differ).
- **Snooze** hides items from the active queue until the deadline; after expiry, they surface as **`open`** again unless you take another action.
- **Chronicity / recurrence** use the last `--recent-limit` stamped intervention files (plus `latest.json`) to populate first/last run and counts.

## Programmatic use

```python
from pathlib import Path
from argus.portfolio.intervention_inbox import build_intervention_inbox_payload, run_intervention_inbox

payload = build_intervention_inbox_payload(Path("."))
# or write latest.json / latest.md:
run_intervention_inbox(Path("."), write_artifacts=True)
```

See also [portfolio-intervention.md](portfolio-intervention.md) for detector rules and [operator-queue.md](operator-queue.md) for queue context.
