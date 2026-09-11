# decision

**Intent:** Turn **findings** into ranked **`DecisionCandidate`**s with **priority scores** and a **portfolio** view across products.

- **Intents:** `argus/decision/intents.py` (`DecisionIntent`)
- **Generation:** `argus/decision/from_findings.py`, `argus/decision/engine.py`
- **Priority:** `argus/decision/priority.py` (transparent weights)
- **Portfolio:** `argus/decision/portfolio.py`
- **Persistence:** `argus/decision/persistence.py` → `runs/decisions/`

CLI: `argus decisions generate`, `portfolio`, `show`.

See [Decisions and lifecycle](../../docs/decisions-and-lifecycle.md).
