# First supervised run (Tier 1, dry-run)

Argus can be driven end-to-end without trusting real-world execution. Use this flow to validate the pipeline, read artifacts, and only later raise autonomy or enable subprocess execution.

## 1. Safe profile (Tier 1, suggest-only)

Writes `runs/autonomy/autonomy.json` with **manual** mode and **tier 1** (suggest-only). This caps policy toward plans and artifacts; it does not enable remote or shell execution by itself.

```bash
uv run argus run safe-profile
```

Optional JSON:

```bash
uv run argus run safe-profile --json
```

## 2. Pre-flight checks

```bash
uv run argus doctor
```

Doctor flags missing inputs, stale temporal artifacts, malformed doctrine/product files, and reminds you when autonomy is not in the supervised first-run posture.

## 3. Full loop (deterministic harness)

Runs discovery through dashboard with **static** execution dry-run analysis only (contract validation — no subprocess execution from this stage).

```bash
uv run argus loop full
# or one product:
uv run argus loop full --product <product_id>
```

Artifacts: `runs/loop/<run_id>/` — `summary.json`, `summary.txt`, `manifest.json`, `stages/*/`.

**Escalation** is not part of the harness; `summary.json` includes `chain.next_commands` with suggested `argus escalation generate <product_id>` steps.

## 4. Human-readable summary

Latest run (or a specific id):

```bash
uv run argus run summary
uv run argus run summary <run_id>
```

Refresh `summary.txt` next to `summary.json`:

```bash
uv run argus run summary <run_id> --write
```

Machine-readable:

```bash
uv run argus run summary <run_id> --json
```

## 5. Dashboard

```bash
uv run argus dashboard --open
```

The dashboard includes a **last loop full** panel (run id, timing, dry-run flag) when `runs/loop/` exists.

## What “safe” means here

| Guarantee | Notes |
|-----------|--------|
| Tier 1 + manual | Suggest-only posture in autonomy config. |
| Loop execution stage | Static dry-run / validation of action contracts; not `argus execution run`. |
| No external actions | Adapters and execution gates remain off unless you opt in elsewhere. |
| Capability gaps | Reported in the run summary and doctor; requests stay unresolved until wired. |

## Moving to Tier 2

When you are ready for **safe execution** experiments (still gated by approval and execution flags), raise tier and follow `docs/system-flow.md` and `argus/autonomy` CLI — do not rely on this doc alone for production automation.
