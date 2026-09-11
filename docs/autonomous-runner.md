# Autonomous portfolio runner

One command runs a **bounded** autonomous operating session: each iteration executes, in order:

1. **Portfolio refresh** — `run_portfolio_refresh` (same as `argus portfolio refresh`)
2. **Portfolio cycle** — `run_portfolio_cycle` (same as `argus portfolio cycle`)
3. **Portfolio lifecycle** — `run_portfolio_lifecycle` (same as `argus portfolio lifecycle`)
4. **Operator summary** — `run_operator_summary` (same as `argus dashboard summary`)
5. **Operator narrative** — `run_operator_narrative` (same as `argus dashboard narrative`)

After each full iteration, **guardrails** match `argus portfolio schedule`: quiescence recommendations, cycle overall recommendation, no-material-change streak, intervention-heavy streak, and max cycle count. The implementation **reuses** helpers from `argus.portfolio.scheduler` for those checks (no duplicated guardrail logic).

### Bounded confidence adjustment (autonomy memory)

Before the loop starts, the runner consults **cross-session autonomy memory** (`argus portfolio autonomy-memory`, schema `argus.portfolio_autonomy_memory.v1`) via `evaluate_autonomous_confidence_adjustment` (schema `argus.portfolio_autonomous_confidence_adjustment.v1`). When **all** safety gates pass and recent history shows **repeated low-risk** patterns (for example repeated `inspect_specific_products` stops, repeated `mixed_sparse` lifecycle primaries, or repeated caution-class sessions without failure-class stops), the session may receive:

- **At most +1 iteration** on the configured `max_cycles` budget (`effective_max_cycles` on the payload).
- **At most one** deferred stop for **narrow** caution-class guardrails only: `cycle_overall` = `inspect_specific_products`, quiescence = `inspect`, or one extra chance on a no-material-change streak threshold hit.

**Never** adjusted: pipeline failures (refresh/cycle/lifecycle/dashboard), explicit stop sentinel, intervention-heavy streak, `request_human_review` / `repair_imports` cycle overall, or any stop while the **escalation inbox** has an open `unsafe_to_continue` item, critical severity, or high/critical **blocked_promotion** escalation.

The session payload includes **`confidence_adjustment`**: whether memory was consulted, eligibility, block reason, patterns matched, whether a continuation was consumed, and `configured_max_cycles` vs `effective_max_cycles`. Per-cycle rows may include **`confidence_adjustment`** when a one-shot continuation was applied. See [autonomy-memory.md](autonomy-memory.md).

## CLI

```bash
argus portfolio run-autonomous [options]
```

| Flag | Meaning |
|------|---------|
| `--max-cycles N` | Hard cap on iterations (default: 5) |
| `--limit-per-cycle N` | Passed to portfolio cycle progression limit (default: 5) |
| `--limit-history N` | History window for operator summary and narrative (default: 30) |
| `--dry-run` | Simulate the full pipeline **without persisting** refresh, cycle, lifecycle, operator summary, or narrative artifacts. Does not write lifecycle promotion records under `runs/products/promotion_actions/`. The autonomous session JSON is still written under `runs/portfolio/autonomous_runner/` unless you also pass `--no-save` (see below). |
| `--skip-import-failed` | Passed through to `run_portfolio_cycle` |
| `--skip-waiting` | Passed through to `run_portfolio_cycle` |
| `--json` | Print session payload (schema `argus.portfolio_autonomous_runner.v1`) to stdout |
| `--no-save` | **No disk writes at all** for this command: does not write `runs/portfolio/autonomous_runner/*`, does not persist any pipeline stage outputs (same effective persistence behavior as `--dry-run` for stages), and does not write lifecycle promotion artifacts. Use this for a read-only dry run when you want the full session payload on stdout (`--json`) or rendered Markdown without touching `runs/`. |
| `--products-dir PATH` | Optional products root override |
| `--allow-promotion` | After the session, run **bounded** safe lifecycle promotions via `argus.products.promotion` (creation scaffold, optional bootstrap, deprecation plan) |
| `--promotion-bootstrap` | With `--allow-promotion`: chain bootstrap after scaffold when stages are not in effective dry-run, or run one standalone bootstrap if no creation promotion applies |

## Lifecycle promotion awareness

Every session records a **promotion opportunity scan** (read-only): `promotable_actions`, `promotion_recommendations`, `blocked_promotions`, and `promotion_opportunities` on the JSON payload. This does not mutate products unless `--allow-promotion` is set.

With `--allow-promotion`, the session also includes `promotion_execution` (steps attempted, effective dry-run flag, nested promotion payloads). The rendered Markdown summarizes **available** promotions, **blocked** items with reasons, and **execution** outcomes (or detection-only mode when the flag is off).

### `--dry-run` vs `--no-save`

- **`--dry-run`:** Stages run in non-persisting mode (refresh/cycle/lifecycle/summary/narrative do not write under `runs/`). Lifecycle promotion JSON is not written. Session JSON under `runs/portfolio/autonomous_runner/` is still written **unless** you also pass `--no-save`.
- **`--no-save`:** Implies no session file **and** no stage persistence **and** no `runs/products/promotion_actions/*` promotion records — equivalent to a fully read-only run from Argus’s perspective.
- **`--dry-run` and `--no-save` together:** Same persistence behavior as either flag alone for stages; neither writes session or promotion artifacts.

See **`docs/lifecycle-promotion.md`** for semantics, caps, and artifact paths.

## Artifacts

| Output | Purpose |
|--------|---------|
| `runs/portfolio/autonomous_runner/latest.json` | Normalized session record |
| `runs/portfolio/autonomous_runner/latest.md` | Human-readable summary |
| `runs/portfolio/autonomous_runner/<session_id>.{json,md}` | Stamped copies |

## Stop sentinel

Create an empty file to stop before the next iteration (after the first cycle completes):

`runs/portfolio/autonomous_runner/STOP`

Same pattern as the scheduler’s stop file under `runs/portfolio/scheduler/STOP`, but scoped to the autonomous runner so the two sessions do not share a sentinel.

## Payload (schema)

`argus.portfolio_autonomous_runner.v1` includes:

- `session_id`, `started_at_utc`, `finished_at_utc`
- `cycles_run`, `stop_reason`, `stop_reason_codes`
- `artifacts_refreshed` — canonical relative paths touched when stages persisted
- `session_summary` — short narrative of what ran and why it stopped
- `per_cycle_outcomes` — per-iteration refresh / cycle / lifecycle / summary / narrative summaries
- `dashboard_refresh_status` — operator summary and narrative status from the last completed iteration
- `promotable_actions`, `promotion_recommendations`, `blocked_promotions`, `promotion_opportunities`, `promotion_execution` — lifecycle promotion scan and optional bounded execution (see `docs/lifecycle-promotion.md`)
- `confidence_adjustment` — autonomy memory consultation, eligibility, optional +1 cycle budget and one-shot deferrals (see section above)
- `inputs` — echo of limits and flags (including `allow_promotion`, `promotion_include_bootstrap`)

## Relation to `portfolio schedule`

| | `portfolio schedule` | `portfolio run-autonomous` |
|--|----------------------|----------------------------|
| Core loop | Repeated **portfolio cycle** only | **Refresh → cycle → lifecycle → summary → narrative** each iteration |
| Artifacts | `runs/portfolio/scheduler/` | `runs/portfolio/autonomous_runner/` |
| Guardrails | Same | Same (shared scheduler helpers) |

Use **run-autonomous** when you want one inspectable session that both **moves the portfolio forward** and **refreshes dashboard operator views** without pasting multiple commands.

## Tests

```bash
uv run pytest tests/test_autonomous_runner.py tests/test_autonomous_runner_confidence.py tests/test_autonomous_runner_promotion.py tests/test_runner_service.py
```

For **cadence + heartbeat** (systemd-friendly wrapper), see [autonomous-runner-service.md](autonomous-runner-service.md) (`argus portfolio run-service`).
