# Argus proof run (repeatable)

Minimal end-to-end check after credibility changes. Uses existing CLI only. Substitute `PRODUCT_ID` and paths for your repo.

This checklist is **not** **`argus loop run`** (which stops at decisions) and **not** a single “whole product” pipeline: planning, escalation packets, and **orchestration state** are separate commands. **Implementation plan** and multi-phase **refinement** progress via **`argus refine`** and on-disk sessions; orchestration surfaces **eligible next actions** (e.g. **`implementation_plan_generate`**) as **tasks** / state—operators pick them up explicitly rather than relying on an inline blocking flow.

**Shell:** bash. **Tools:** `uv` (or your Argus entrypoint); **`jq` is required** for the scripted path below (idea pick + assertions).

---

## 0. Fast-fail preflight

Run **once** from the repository root before the long pipeline (signals → … → ideas):

```bash
export PRODUCT_ID="your_product_id"
export ARGUS_CONTEXT_PACKETS=1
./scripts/proof-run-preflight.sh
```

This checks: `jq` and `uv` on `PATH`, `ARGUS_CONTEXT_PACKETS=1`, `products/$PRODUCT_ID/product.yaml`, and `argus products show` succeeds.

---

## 1. Required environment

```bash
export PRODUCT_ID="your_product_id"          # must exist under products/<id>/product.yaml
export ARGUS_CONTEXT_PACKETS=1                 # required for context packets in refinement (and idea LLM prefix when LLM enabled)
```

Optional (only if you want OpenAI-backed outsiders during refine; grounded CURSOR file reviews do **not** require this):

```bash
# export ARGUS_LLM_ENABLED=1
# export ARGUS_OPENAI_API_KEY=...
```

---

## 2. Preconditions

- [ ] Same shell (or re-export) keeps `ARGUS_CONTEXT_PACKETS=1` through **`refine run`** — a new terminal without it changes packet-backed behavior vs this checklist.
- [ ] `products/$PRODUCT_ID/product.yaml` exists and validates (`argus products show $PRODUCT_ID`).
- [ ] At least one idea source exists for `ideas generate` (e.g. signals/findings present after steps 1–2). If the pipeline returns zero ideas, pick another product or seed data — **refine** needs a real `idea_id` from the bundle.
- [ ] **`runs/ideas/latest.json` is overwritten** on each `ideas generate`. If another product’s ideas run ran in between, `IDEA_ID` from `latest.json` may not match what you expect — avoid extra `ideas generate` until after `refine start`, or copy the timestamped bundle under `runs/ideas/` right after your run.

---

## 3. Command sequence

Run from the **repository root** (where `products/` and `runs/` live). Artifacts are always written next to `products/` (CLI resolves repo root from the installed package, not your cwd).

```bash
set -euo pipefail
export ARGUS_CONTEXT_PACKETS=1
export PRODUCT_ID="${PRODUCT_ID:?set PRODUCT_ID}"

# Optional: fail before observation steps (see section 0)
# ./scripts/proof-run-preflight.sh

# Observation
uv run argus signals collect "$PRODUCT_ID"
uv run argus findings generate "$PRODUCT_ID"

# Audit (deterministic nine-angle bundle; optional Cursor layer: `argus audit cursor-prompt` → `argus audit ingest-cursor` or `ingest-agent` `--file …`)
# Optional: signal interpretation sidecar — `argus signals cursor-prompt` → `argus signals cursor-ingest --file …` (does not change runs/signals/latest/)
uv run argus audit run --product-id "$PRODUCT_ID"

# Ideas (audit metadata in bundle meta when product-scoped)
uv run argus ideas generate "$PRODUCT_ID" --json

# Non-empty bundle (fast-fail)
jq -e '(.ideas | length) > 0' runs/ideas/latest.json >/dev/null

# Pick idea_id from latest bundle (requires jq)
IDEA_ID=$(jq -r '.ideas[0].idea_id' runs/ideas/latest.json)
test -n "$IDEA_ID" && test "$IDEA_ID" != "null"

# Refinement session
REFINE_START_JSON=$(uv run argus refine start --type idea --source "$IDEA_ID" --product-id "$PRODUCT_ID" --json)
SESSION_ID=$(echo "$REFINE_START_JSON" | jq -r '.session_id')
export SESSION_ID
```

If you ran `refine start` without `--json`, copy `session_id` from stdout and `export SESSION_ID=...`.

**Grounded CURSOR reviews (required for proof):** write **before** `refine run`.

Discover the draft round `N` (usually `0` for a new session; use this for the filename `round_N.json`). Keep this in **one** shell block so `ROUND` is set before the heredoc:

```bash
SESSION_ID="${SESSION_ID:?set SESSION_ID}"
ROUND=$(uv run argus refine show "$SESSION_ID" --json | jq -r '.session.current_round')
RDIR="runs/refinement/$SESSION_ID/reviews_in"
mkdir -p "$RDIR"
# IDEA council: grounded stakeholders are product, finance, technical (see argus/council/profiles.py)
cat > "$RDIR/round_${ROUND}.json" <<'EOF'
{
  "product": {
    "verdict": "pass",
    "blocking": false,
    "confidence_score": 0.85,
    "objection_categories": [],
    "objections": [],
    "suggestions": ["proof-run ok"],
    "rationale": "Grounded product review from proof-run file."
  },
  "finance": {
    "verdict": "pass",
    "blocking": false,
    "confidence_score": 0.85,
    "objection_categories": [],
    "objections": [],
    "suggestions": [],
    "rationale": "Grounded finance review from proof-run file."
  },
  "technical": {
    "verdict": "pass",
    "blocking": false,
    "confidence_score": 0.85,
    "objection_categories": [],
    "objections": [],
    "suggestions": [],
    "rationale": "Grounded technical review from proof-run file."
  }
}
EOF

uv run argus refine run "$SESSION_ID" --json

# Durable orchestration snapshot (derived from runs/*; no subprocess). Optional: `orchestration advance` / `cursor-ingest` — see orchestrator README.
uv run argus orchestration state --product-id "$PRODUCT_ID"

# Bounded orchestration progression (evaluate → advance loop; writes durable run record by default)
uv run argus orchestration run-progression --product-id "$PRODUCT_ID" --max-steps 8
```

If `refine run` fails with a **missing** `reviews_in/round_*.json`, stderr lists the expected path and suggests `argus refine show`.

---

## 4. Expected artifacts (paths)

| Step | Artifact |
|------|-----------|
| signals | `runs/signals/latest/<PRODUCT_ID>.json` (`argus.signal_collection.v1`; records include collection-time **`canonical`** / `argus.canonical_signal.v1` with **`freshness_status`**; optional **`signal_continuity`** / `argus.signal_continuity.v1` vs prior `latest`) |
| signals (optional Cursor review) | `runs/signals/review/<PRODUCT_ID>.json` (`argus.signal_review_bundle.v1`) — only after **`argus signals cursor-ingest`**; not required for proof |
| temporal (derived) | `runs/temporal/latest/<PRODUCT_ID>.json` (`argus.temporal_bundle.v1`; per-signal recency: **`freshness_status`** and/or **`freshness_bucket`**; aggregate **`worst_freshness_status`**; same **`signal_continuity`** block as the signal bundle when written) |
| findings | `runs/findings/latest/<PRODUCT_ID>.json` |
| audit | `runs/audit/<PRODUCT_ID>/bundle.json`, `runs/audit/<PRODUCT_ID>/latest.json` |
| ideas | `runs/ideas/latest.json` (and timestamped copy under `runs/ideas/`) |
| refine | `runs/refinement/<SESSION_ID>/session.json`, `drafts/round_0.json`, `reviews/round_0.json`, `synthesis/round_0.json`, `convergence/round_0.json` |
| input reviews | `runs/refinement/<SESSION_ID>/reviews_in/round_<N>.json` (you create this; `N` = `current_round` from `refine show`) |
| orchestration | `runs/orchestration/latest/<PRODUCT_ID>.json` (`argus.orchestration_state.v1`) — written by **`argus orchestration state`**; includes **`orchestration_status`**, **`overall_status`**, **`eligible_actions`**, **`next_action`**, **`escalation_eligible`** / **`escalation_triggers`**, **`artifacts.refinement.review_state`** |
| orchestration (task) | when **`next_action` ≠ `none`**: `runs/orchestration/tasks/latest/<PRODUCT_ID>.json` (`argus.orchestration_task.v1`) — machine-readable pending **task_type** / **action** aligned with the state snapshot |
| orchestration (advancement) | optional: `runs/orchestration/latest/advancements/<PRODUCT_ID>.json` (`argus.orchestration_advancement.v1`) — after **`argus orchestration advance`** (durable **intent** only; does not run signals/audit/refine) |
| orchestration (progression run) | **`runs/orchestration/latest/progression_runs/<PRODUCT_ID>.json`** and **`runs/orchestration/progression_runs/generations/<run_id>.json`** (`argus.orchestration_progression_run_artifact.v1`) — after **`argus orchestration run-progression`** (default: writes **latest** + **generation** with identical JSON; omit proof checks if you passed **`--no-write-artifact`**) |
| orchestration (optional Cursor review) | optional: `runs/orchestration/review/<PRODUCT_ID>.json` (`argus.orchestration_review_bundle.v1`) — after **`argus orchestration cursor-ingest`**; not required for proof |

---

## 5. Concrete assertions

Run after the sequence (with `PRODUCT_ID` and `SESSION_ID` set).

Optional coarse JSON check (fails fast on malformed artifacts under `runs/`):

```bash
uv run argus validate artifacts
```

**Signals** (real, normalized, temporally qualified — expect these after a fresh `argus signals collect`)

```bash
S="runs/signals/latest/${PRODUCT_ID}.json"
test -f "$S"
jq -e '.schema == "argus.signal_collection.v1"' "$S" >/dev/null
jq -e 'if (.records | length) == 0 then true else all(.records[]; .canonical.schema == "argus.canonical_signal.v1" and (.canonical.freshness_status | length > 0)) end' "$S" >/dev/null
jq -e 'has("signal_continuity") and (.signal_continuity.schema == "argus.signal_continuity.v1")' "$S" >/dev/null
```

**Declared signals (product `signals.yaml` / inline `signal_manifest`)** — fail proof if an **enabled** declaration still has no matching collected row (`manifest_declaration` gap with `collection_status` **`missing`**). Rows with **`unsupported_source_type`** are explicit adapter gaps and do not fail this check.

```bash
jq -e '[.records[] | select(.source == "manifest_declaration" and .payload.collection_status == "missing")] | length == 0' "$S" >/dev/null
```

**Temporal bundle** (written alongside signal collection when I/O succeeds)

```bash
T="runs/temporal/latest/${PRODUCT_ID}.json"
test -f "$T"
jq -e '.schema == "argus.temporal_bundle.v1"' "$T" >/dev/null
jq -e 'if (.signals | length) == 0 then true else all(.signals[]; ((.freshness_status // "") | length > 0) or ((.freshness_bucket // "") | length > 0)) end' "$T" >/dev/null
jq -e 'has("worst_freshness_status")' "$T" >/dev/null
jq -e 'if has("signal_continuity") then .signal_continuity.schema == "argus.signal_continuity.v1" else true end' "$T" >/dev/null
```

**Temporal freshness (proof bar)** — when there is at least one temporal row, **`worst_freshness_status`** must not be **`stale`** or **`expired`** (otherwise observations are too old for a green proof). Empty `signals` skips this.

```bash
jq -e 'if (.signals | length) == 0 then true else (.worst_freshness_status == null or (.worst_freshness_status != "stale" and .worst_freshness_status != "expired")) end' "$T" >/dev/null
```

If `worst_freshness_status` is missing, re-run **`argus signals collect`** so the temporal writer can refresh (older bundles may lack it). **`argus validate artifacts`** may **warn** on legacy signal files without `canonical` until you re-collect.

**Findings**

```bash
test -f "runs/findings/latest/${PRODUCT_ID}.json"
```

**Zero findings** is allowed: the checklist does not require `finding_count > 0`. Built-in rules emit findings only for gaps, risks, or opportunities; a maintain-stage product with sufficient fresh signals and cost under cap may legitimately produce an empty list.

**Audit bundle — no stub placeholder lines in non-stub angles**

`STUB_ANGLE_IDS` in code is **empty** when all nine angles use deterministic runners; the jq checks below ensure **summary_lines** do not contain the literal stub phrase for angles that should be implemented.

```bash
B="runs/audit/${PRODUCT_ID}/bundle.json"
test -f "$B"
jq -e '.angles.product_gap.angle_status == "active"' "$B" >/dev/null
jq -e '.angles.cost.angle_status == "active"' "$B" >/dev/null
jq -e '.angles.quality.angle_status == "active"' "$B" >/dev/null
jq -e '.angles.security.angle_status == "active" or .angles.security.angle_status == "partial"' "$B" >/dev/null
jq -e '.angles.security.summary_lines | any(test("not implemented \\(stub\\)")) | not' "$B" >/dev/null
jq -e '.angles.compliance.angle_status == "active" or .angles.compliance.angle_status == "partial"' "$B" >/dev/null
jq -e '.angles.compliance.summary_lines | any(test("not implemented \\(stub\\)")) | not' "$B" >/dev/null
jq -e '.angles.compliance.summary_lines | any(test("not legal"))' "$B" >/dev/null
jq -e '.angles.reliability.angle_status == "active" or .angles.reliability.angle_status == "partial"' "$B" >/dev/null
jq -e '.angles.reliability.summary_lines | any(test("not implemented \\(stub\\)")) | not' "$B" >/dev/null
jq -e '.angles.performance.angle_status == "active" or .angles.performance.angle_status == "partial"' "$B" >/dev/null
jq -e '.angles.performance.summary_lines | any(test("not implemented \\(stub\\)")) | not' "$B" >/dev/null
jq -e '.angles.store_business.angle_status == "active" or .angles.store_business.angle_status == "partial"' "$B" >/dev/null
jq -e '.angles.ux.angle_status == "active" or .angles.ux.angle_status == "partial"' "$B" >/dev/null
```

**Context packets**

- Preconditions: `ARGUS_CONTEXT_PACKETS=1` was exported for `refine run` (and any step where you rely on packet-backed prompts).
- Spot-check assembly (optional one-liner):

```bash
uv run python -c "
from pathlib import Path
from argus.context import ContextPurpose, assemble_context_bundle
root = Path('.').resolve()
b = assemble_context_bundle(root, ContextPurpose.REFINEMENT_GROUNDED, '${PRODUCT_ID}', draft=None)
assert b.get('packet_schema') == 'argus.context_bundle.v1'
assert 'audit' in b and 'angles' in b['audit']
print('context ok', list(b['audit']['angles'].keys())[:4])
"
```

**Ideas — audit-driven metadata**

```bash
jq -e '.meta.audit_ideas | type == "object"' runs/ideas/latest.json >/dev/null
jq -e '.meta.audit_ideas.applied == true or .meta.audit_ideas.reason != null' runs/ideas/latest.json >/dev/null
```

If audit was never run, `audit_ideas` may report `no_audit_artifact` — for a **passing** proof run, ensure `audit run` happened first so `applied` can be true when overlap logic applies.

**Refinement — grounded reviews from file**

```bash
R="runs/refinement/${SESSION_ID}/reviews/round_0.json"
test -f "$R"
jq -e '.reviews[] | select(.stakeholder_type=="product") | .backend_used == "cursor"' "$R" >/dev/null
jq -e '.reviews[] | select(.stakeholder_type=="product") | .llm_status == "file"' "$R" >/dev/null
jq -e '.reviews[] | select(.stakeholder_type=="product") | .rationale | test("proof-run")' "$R" >/dev/null
```

**Convergence**

```bash
test -f "runs/refinement/${SESSION_ID}/convergence/round_0.json"
jq -e '.converged != null' "runs/refinement/${SESSION_ID}/convergence/round_0.json" >/dev/null
```

**Orchestration state** (after `argus orchestration state` in section 3)

```bash
O="runs/orchestration/latest/${PRODUCT_ID}.json"
test -f "$O"
jq -e '.schema == "argus.orchestration_state.v1"' "$O" >/dev/null
jq -e '.product_id == $pid' --arg pid "${PRODUCT_ID}" "$O" >/dev/null
jq -e '(.eligible_actions | type == "array") and (.next_action | type == "string")' "$O" >/dev/null
jq -e 'has("orchestration_status") and has("orchestration_status_reason") and has("overall_status")' "$O" >/dev/null
jq -e 'has("escalation_eligible") and has("escalation_triggers")' "$O" >/dev/null
jq -e '(.artifacts.refinement | type == "object") and (.artifacts.refinement.review_state | type == "string")' "$O" >/dev/null
```

**Orchestration progression run** (after **`argus orchestration run-progression`** in section 3 — default **writes** durable artifacts; skip this block if you used **`--no-write-artifact`**)

```bash
P="runs/orchestration/latest/progression_runs/${PRODUCT_ID}.json"
test -f "$P"
jq -e '.schema == "argus.orchestration_progression_run_artifact.v1"' "$P" >/dev/null
jq -e '.product_id == $pid' --arg pid "${PRODUCT_ID}" "$P" >/dev/null
jq -e 'has("run_id") and has("started_at_utc") and has("completed_at_utc")' "$P" >/dev/null
jq -e 'has("terminal_status") and has("terminal_reason") and (.terminal_status | type == "string") and (.terminal_reason | type == "string")' "$P" >/dev/null
jq -e 'has("step_count") and has("actions_taken") and (.actions_taken | type == "array")' "$P" >/dev/null
jq -e 'has("orchestration_fingerprint") and has("final_state_summary")' "$P" >/dev/null
RUN_ID=$(jq -r '.run_id' "$P")
test -n "$RUN_ID" && test "$RUN_ID" != "null"
G="runs/orchestration/progression_runs/generations/${RUN_ID}.json"
test -f "$G"
cmp -s "$P" "$G"
```

A successful proof run after refinement usually has **`eligible_actions`** non-empty and **`next_action`** not `"none"` (unless you truly have no eligible next step). **`orchestration_status`** is the headline posture (e.g. **`blocked_waiting_input`** while waiting on **`reviews_in`**; **`eligible`** or **`stale_refresh_needed`** otherwise). This checklist does not require a specific `next_action` value.

### 5.1 Phase-2 orchestration contract (pytest)

Optional repeatable checks for **handled** Phase-2 actions (`findings_generate`, `decisions_generate`, `ideas_generate`, **`experiments_propose`**, `escalation_packet_generate`), dispatch subset parity (Phase-2 handled ids ⊆ full **`step_executor`** table), stable **`reason_codes`** (including **`experiments_propose_ready`**), **`execution_detail.schema`** alignment (including **`argus.experiment_proposals_run.v1`** for **`experiments_propose`**), **`run-progression`** ordering **`signals_collect` → `findings_generate` → `decisions_generate`**, and (with audit stub + fresh temporal bundle fixtures) end-to-end ordering through **`ideas_generate` → `experiments_propose` → `refinement_start_idea` → `escalation_packet_generate`** when those actions are eligible:

```bash
uv run pytest \
  tests/test_orchestration_phase2_contract.py \
  tests/test_orchestration_progression.py::TestOrchestrationProgression::test_progression_execute_multi_step_signals_findings_decisions \
  tests/test_orchestration_progression.py::TestOrchestrationProgression::test_progression_execute_phase2_chain_includes_escalation_packet_generate \
  tests/test_orchestration_progression.py::TestOrchestrationProgression::test_progression_experiments_propose_ordering_after_decisions_and_ideas \
  tests/test_orchestration_progression.py::TestOrchestrationProgression::test_progression_idea_refinement_corridor_submit_reviews_in_then_run \
  -q
```

Canonical semantics: **`docs/model-contracts.md`** (Phase-2 orchestration).

---

## 6. Honesty notes

- **Findings**: **Zero findings** can be healthy — same as above: proof only checks that the findings bundle file exists; an empty `findings` array is normal when no heuristic rule matches.
- **Outsiders** (investor, marketer, creative) may still use OpenAI or deterministic stub when LLM is off; this checklist validates **grounded** CURSOR file path and **convergence** on the full review set.
- **Ideas / audit**: `audit_ideas.applied` depends on token overlap with Product Gap capabilities — not every run will nudge scores; still assert `meta.audit_ideas` exists and reflects `run_audit` vs missing audit.
- **Security `partial`**: Normal if no lockfiles/manifests at repo or product roots; still non-stub content.
- **Performance / reliability `partial`**: Normal when few static markers match; angles are deterministic scans, not measured production telemetry.
- **Signals / temporal**: Proof jq checks expect **current** collection output (`canonical` on each record; temporal rows qualified by `freshness_status` and/or `freshness_bucket`; **`signal_continuity`** on the signal bundle and usually mirrored on the temporal bundle). Products with **`signals.yaml`** (or inline `signal_manifest` in `product.yaml` when `signals.yaml` is absent) get manifest-driven adapter enablement, canonical linkage via **`canonical.provenance.manifest_signal_id`** when a collected row matches a declaration, and explicit gap rows (`source` **`manifest_declaration`**) with **`collection_status`** `missing` or `unsupported_source_type` when no row matches or no local adapter exists for that `source_type`. The **manifest jq** above fails proof on **`missing`** only — fix wiring or paths so real rows match declarations, or adjust declarations. **`stale` / `expired`** worst status fails the **temporal freshness** check; refresh inputs or collect closer to live data for a green proof.
- **Orchestration**: **`state`** derives **`runs/orchestration/latest/`** from existing `runs/` artifacts; it does not run refinement or collect signals. It makes **blockers / escalation posture / eligibility** explicit; **`escalation_triggers`** may be empty on a clean run. **`argus escalation generate`** is a **separate** durable packet step when policy triggers fire—use both when triaging. **`runs/orchestration/tasks/latest/`** mirrors **`next_action`**. **`argus orchestration advance`** records **intent** under **`advancements/`** without executing work; **`cursor-ingest`** is an optional interpretation sidecar (like signals/audit). **`argus orchestration run-progression`** (default) writes **`argus.orchestration_progression_run_artifact.v1`** under **`latest/progression_runs/`** and **`progression_runs/generations/`**; use **`--no-write-artifact`** to skip files (then omit section 5 progression jq). **`--json`** on **`run-progression`** is the stdout summary (`argus.orchestration_progression_run.v1`), not the on-disk artifact schema.

---

## 7. One-shot copy-paste (after `PRODUCT_ID` and `SESSION_ID` / review file)

Run section 0 (optional), then section 3; then run all `test` / `jq` blocks from section 5 in order. Any non-zero exit is a failed proof run.
