# Findings engine

**Signals** are observations (`SignalRecord`). **Findings** are interpreted, decision-ready items (`Finding`) with kind, severity, effort, summary, and evidence.

## Flow

1. Load or collect signals (typically `runs/signals/latest/<product_id>.json` via `argus signals collect`).
2. Run **rules** in `argus/findings/rules/` over `RuleContext(product, signals)`.
3. **Consolidate** candidates that share `(kind, rule_id, issue_key)` and merge `source_signals`.
4. Assign **severity** and **effort** with `argus/findings/heuristics.py` (deterministic).
5. Persist to `runs/findings/latest/<product_id>.json` and `runs/findings/generations/`.

## CLI

- `argus findings generate`, `argus findings show <product_id>`, `argus findings summary`

See built-in rule implementations in `argus/findings/rules/builtins.py`.

## Temporal-aware rules

`argus/findings/rules/temporal.py` (`TemporalSignalsRule`) emits kinds such as **current_opportunity**, **current_risk**, **stale_context**, **trending_topic**, **urgency_window**, **no_recent_evidence** from `payload.temporal` conventions and product-level staleness. These are **interpretations** of structured snapshot or adapter fields — not a second signal pipeline. See [temporal-intelligence.md](temporal-intelligence.md).
