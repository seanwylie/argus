# Portfolio operator cycle (`argus.portfolio_cycle.v1`)

**CLI:** `argus portfolio cycle` (`--limit`, `--dry-run`, `--skip-import-failed`, `--skip-waiting`, `--json`, `--no-save`)

**Artifacts:** `runs/portfolio/cycle/latest.{json,md}` plus timestamped `YYYYMMDDTHHMMSSZ.{json,md}` (same pattern as progression / quiescence).

## Purpose

Run **one bounded** operator pass that chains existing portfolio subsystems in order:

1. Build and persist the **operator queue** (`argus.portfolio.operator_queue`)
2. Run **bounded portfolio progression** (`argus.portfolio.progression`)
3. Evaluate **portfolio quiescence** (`argus.portfolio.quiescence`)
4. Build the **portfolio delta report** (`argus.portfolio.delta_report`)
5. Run **portfolio intervention** detection (`argus.portfolio.intervention`)
6. Emit a **cycle summary** with an **overall operator recommendation**

This module is **orchestration glue** only: it does not duplicate scoring, thresholds, or policy from those modules.

## Guardrails

- **`--limit N`** — Passed through to progression (max products touched this cycle).
- **`--dry-run`** — Progression uses preview-only advancement (no orchestration advancement writes); progression still records outcomes in its artifact when saves are enabled.
- **No looping** — Exactly one invocation of each stage per command.
- **Failure handling** — If a stage raises, the cycle records `status: error` for that stage, continues with subsequent stages where possible, and sets top-level `ok: false` if any stage failed.
- **`--no-save`** — Does **not** write `runs/portfolio/cycle/*`. Sub-stages still write their usual outputs under `runs/portfolio/{operator_queue,progression,quiescence,delta_report,intervention}/` unless you rely on other tooling to avoid writes (not this flag).

## Overall operator recommendation

The synthesized field `summary.overall_operator_recommendation` is one of:

| Value | Typical meaning |
|-------|------------------|
| `run_again` | Another portfolio pass is likely useful (quiescence / delta signals active work). |
| `wait` | Portfolio looks quiescent and intervention noise is low. |
| `inspect_specific_products` | Targeted inspection (delta “establish baseline”, blockers/regressions, or non-benign intervention flags). |
| `repair_imports` | Import health / first-pass signals dominate (quiescence `import_refresh`, delta `inspect_import_health`, or intervention `import_repair`). |
| `request_human_review` | Stuck/blocked patterns, high-severity intervention, or quiescence `human_review`. |

Rationale strings are listed under `summary.overall_rationale_codes` (stable identifiers).

## CLI

```bash
uv run argus portfolio cycle
uv run argus portfolio cycle --limit 3 --dry-run
uv run argus portfolio cycle --json
uv run argus portfolio cycle --no-save
```

Optional **`--products-dir`** follows the same convention as other portfolio commands.

## See also

- [operator-queue.md](operator-queue.md)
- [portfolio-progression.md](portfolio-progression.md)
- [portfolio-quiescence.md](portfolio-quiescence.md)
- [portfolio-delta-report.md](portfolio-delta-report.md)
- [portfolio-intervention.md](portfolio-intervention.md)
