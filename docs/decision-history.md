# Decision memory and churn

Each `argus decisions generate` run (when saved) writes a timestamped bundle under **`runs/decisions/generations/<UTC>_<product_id>.json`** plus **`runs/decisions/latest/<product_id>.json`**.

The **history** and **churn** commands read only those generation files (no execution).

## Commands

| Command | Purpose |
|---------|--------|
| `argus decisions history` | List every product id that has at least one generation file, with counts |
| `argus decisions history <product_id>` | Print each run (oldest first): top recommendation, scores, vs previous |
| `argus decisions churn` | Churn/stability report for every product with history |
| `argus decisions churn <product_id>` | Same for one product |

Add `--json` for machine-readable output.

## What churn measures

Deterministic heuristics over the sequence of top candidates:

- How often the **top recommended action** text changed between runs  
- **Intent buckets** (hold / deprecate / improve / other) and flip-backs (oscillation)  
- **Confidence** range across runs  
- Optional **repeated escalation titles** for the same product (from `runs/escalations/latest/` index)

Outputs include **`churn_score`** and **`stability_score`** in `[0, 1]` plus human **`summary_lines`** (e.g. “Top recommended action changed *n* times in *m* recorded run(s)”).

## Requirements

You need **multiple** saved generations over time. Re-run `argus decisions generate` after meaningful pipeline changes to build a usable timeline.
