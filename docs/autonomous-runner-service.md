# Autonomous runner service (cadence + heartbeat)

**CLI:** `argus portfolio run-service`

Wraps **`argus portfolio run-autonomous`** with:

- **One-shot** mode (default): a single autonomous session — same as `run-autonomous` from an operator perspective, plus a **service heartbeat** under `runs/portfolio/runner_service/`.
- **Cadence** mode: `--interval-seconds SEC` and `--max-runs N` to run up to **N** bounded sessions, sleeping **SEC** seconds between the end of one session and the start of the next.
- **Hard caps:** `max_runs` is always finite; there is **no** unbounded loop.
- **Stop sentinel:** create `runs/portfolio/runner_service/STOP` (empty file) to stop before the next session (and after each sleep). Per-session guardrails inside `run-autonomous` are unchanged; the autonomous runner’s own `runs/portfolio/autonomous_runner/STOP` still applies **during** a session.

## Schema

`argus.portfolio_runner_service.v1` — see `argus/portfolio/runner_service.py` for the full payload. Notable fields:

| Field | Meaning |
|--------|--------|
| `current_status` | `starting` · `running` · `sleeping` · `stopped` |
| `last_run_started_at_utc` / `last_run_finished_at_utc` | Last autonomous session window |
| `last_session_id` | From `run-autonomous` payload |
| `next_planned_run_at_utc` | Set while sleeping between sessions (cadence mode) |
| `loop_count` | Autonomous sessions completed in this **service** invocation |
| `stop_reason` / `stop_reason_codes` | Why the service stopped (sentinel, max_runs, one-shot complete, pipeline failure, …) |
| `last_run_summary` | Truncated copy of the last session summary text |

Optional: pass **`--include-autonomous-session`** with **`--json`** to embed the full last `run-autonomous` payload (large).

## Artifacts

| Path | Purpose |
|------|---------|
| `runs/portfolio/runner_service/latest.json` | Current heartbeat (updated on major transitions) |
| `runs/portfolio/runner_service/latest.md` | Human-readable heartbeat |
| `runs/portfolio/runner_service/<service_run_id>__start.{json,md}` | Service invocation start |
| `runs/portfolio/runner_service/<service_run_id>__session_NNN_complete.{json,md}` | After each autonomous session |
| `runs/portfolio/runner_service/<service_run_id>__stop.{json,md}` | Final state when the service exits |

## CLI flags

See `argus portfolio run-service --help`. Common options:

- `--interval-seconds` — `0` (default) = one-shot.
- `--max-runs` — cap sessions (default `1`; use `>1` with a positive interval).
- `--max-cycles-per-run`, `--limit-per-cycle`, `--limit-history` — forwarded to `run-autonomous`.
- `--dry-run`, `--no-save`, `--allow-promotion`, `--promotion-bootstrap`, `--products-dir` — same semantics as `run-autonomous`; **`--no-save`** disables **all** writes including heartbeat files.

## Persistence (systemd)

Run the repo from a fixed directory and set **`WorkingDirectory`** so relative `runs/` and `products/` resolve correctly.

**User systemd sessions** often get a **minimal `PATH`**, so **`uv`** is not found (exit **127**). Use the same pattern as the operator dashboard: set **`Environment=PATH=...`** to include `%h/.local/bin` (or your `uv` install location), and/or run via **`tools/run_portfolio_runner.sh`**, which mirrors **`tools/run_dashboard.sh`** (prepends `~/.local/bin` when `uv` is missing; optional **`Environment=UV=/full/path/to/uv`**). See **`deploy/systemd/argus-portfolio-runner.service.example`**.

### User service (loop in foreground)

The service runs **`run-service`** with a positive interval and max runs. For a **long-lived** process, use a large `--max-runs` (e.g. `100000`) and rely on the **STOP** file or `systemctl --user stop` for shutdown.

After **`systemctl --user stop`** or **`restart`**, the main process often exits with **status 143** (128 + **SIGTERM**). That is a normal graceful shutdown, not a crash. Add **`SuccessExitStatus=0 143`** under `[Service]` if you want systemd to avoid logging **`Failed with result 'exit-code'`** for those stops.

Example: `~/.config/systemd/user/argus-portfolio-runner.service`

```ini
[Unit]
Description=Argus portfolio autonomous runner (cadence)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/path/to/argus/repo
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=ARGUS_REPO_ROOT=/path/to/argus/repo
ExecStart=/path/to/argus/repo/tools/run_portfolio_runner.sh \
  --interval-seconds 3600 \
  --max-runs 100000 \
  --max-cycles-per-run 3 \
  --limit-per-cycle 3
SuccessExitStatus=0 143
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
```

If you call **`uv` directly** instead of the wrapper, keep **`Environment=PATH=...`** as above (do not rely on `/usr/bin/env uv` without fixing `PATH`).

Enable and start:

```bash
systemctl --user daemon-reload
systemctl --user enable --now argus-portfolio-runner.service
```

### Timer-based one-shot (alternative)

If you prefer **discrete** invocations (each process exits), use a **systemd timer** that runs:

```bash
uv run argus portfolio run-service --interval-seconds 0 --max-runs 1
```

(or plain `run-autonomous`) on each trigger. That avoids a long-running Python process but does not update `next_planned_run_at_utc` between invocations in a single heartbeat file across runs (each invocation has its own `service_run_id`).

### Safe shutdown

1. **`touch runs/portfolio/runner_service/STOP`** — cooperative stop before the next session.
2. **`systemctl --user stop argus-portfolio-runner.service`** — SIGTERM; between sessions the process exits quickly. If a session is mid-flight, wait for it to finish (bounded by `run-autonomous` max cycles).
3. Do **not** delete the repo or `runs/` while the service is writing.

## Tests

```bash
uv run pytest tests/test_runner_service.py
```

## See also

- [autonomous-runner.md](autonomous-runner.md) — `run-autonomous` semantics
- [lifecycle-promotion.md](lifecycle-promotion.md) — promotion scans and bounded execution
