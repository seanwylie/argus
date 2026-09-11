# Autonomy (operator safety)

Argus **autonomy** is the operator-controlled safety layer for **how much** automation is allowed—not the same as “safe auto-execution” heuristics (`evaluate_safe_autonomy` in `argus.autonomy.safe_execution`).

## Concepts

- **Rollout tiers (0–4)** — explicit bounded-autonomy ladder (`tier` field in `runs/autonomy/autonomy.json`); see **[autonomy-rollout.md](autonomy-rollout.md)** for matrix, guardrails, and escalation rules.
- **`AutonomyMode`**: `off` | `manual` | `supervised` | `limited` | `active` — stored in `runs/autonomy/autonomy.json` (optional; default behavior matches **active**-like caps when the file is absent).
- **`AutonomyPolicy`**: numeric and set-based caps: `max_actions_per_run` (enforced vs a per–UTC-day counter in `runs/autonomy/state.json`), `max_cost_per_day`, allow/forbidden action types, `require_approval_for`, `auto_execute_types`, `escalation_thresholds`, daily **`max_product_spawns_per_utc_day`**, **`max_experiments_per_utc_day`**, **`max_shutdowns_per_utc_day`**, and optional **`min_confidence_autonomous`** (0–1; `0` disables the gate).
- **Enforcement**: Before subprocess execution (`argus execution run`, `argus actions execute`), `check_autonomy_execution` runs; blocks increment `block_streak` and may emit capability requests (`autonomy.policy`).

## CLI

- `argus autonomy show` / **`status`** — same payload: mode, tier, **guardrail_limits** (effective policy JSON), state paths, **policy_resolution_preview**.
- `argus autonomy set <mode>` — write `runs/autonomy/autonomy.json`.
- `argus autonomy set-tier <0–4>` — set tier + canonical mode mapping.
- `argus autonomy explain <matrix_key|action.yaml>` — tier matrix check; YAML/JSON paths infer the matrix class from `ActionContract`.
- `argus autonomy policy` — print merged policy (defaults + optional `policy_overrides`).

Other `argus autonomy` subcommands (e.g. spawn, run, scheduler) orchestrate long-running workflows; see `--help`.

## Decision context assessment

When `runs/decision_assessment/latest/<product_id>.json` exists and recommends **human escalation** (`escalation_recommendation: escalate_human`), `check_autonomy_execution` may **deny** autonomous execution for that product until an explicit approval exists—so “high doubt + high stakes” does not silently auto-run. This uses the same deterministic layer as `argus confidence` (see [decision-confidence.md](decision-confidence.md)); it is **not** emotion modeling. Stub/gap penalties in that assessment apply only when the **top decision** intersects documented stubs or capability gaps ([stub-inventory.md](stub-inventory.md)), matching `argus.decision.stub_awareness`.

## Coherence

- **Approvals** (`runs/approval/records/`) gate execution separately; autonomy constrains *whether* execution is allowed at all and *which* action types may auto-execute under policy.
- **Doctrine** (`products/<id>/doctrine.yaml`) influences findings/scoring; autonomy gates shell execution.

## Limits

Autonomy does not replace OS sandboxing, approval records, or execution sandbox checks—it layers **budget and mode** policy on top.

Optional **LLM** features ([llm-integration.md](llm-integration.md)) never change mode, tier, or policy; they cannot bypass these gates.

**Refinement** ([artifact-refinement.md](artifact-refinement.md), [councils.md](councils.md)) only creates review artifacts; it does not execute actions or alter autonomy state.

## Freshness and execution

`argus.autonomy.constraints.freshness` adds blockers when decision metadata sets **`freshness_escalation`** (stale **operational** signals for a freshness-gated finding with launch-experiment intent). This prevents “autonomous” runs from treating outdated metrics as current. It does **not** block actions that do not carry that metadata. See [temporal-intelligence.md](temporal-intelligence.md) and `argus/decision/freshness.py`.

## Relationship to `argus loop run`

**`argus autonomy run`** (and the background scheduler) execute a **different** staged pipeline under `runs/autonomy/<run_id>/` (signals through execution gate). That path may include a **weekly planning** artifact copy for traceability; it is **not** the same command as **`argus loop run`**, which stops at **decisions** and does not subsume **`argus planning weekly`** or **`argus escalation generate`**. See [system-flow.md](system-flow.md) and [argus/orchestrator/README.md](../argus/orchestrator/README.md).

## Autonomy shutdown (`argus autonomy shutdown`)

Evaluates kill-score eligibility for one product (`--product <id>`) or all candidates (`--all-candidates`). Default is **dry-run** (no file changes). **`--apply`** mutates manifests and moves the product tree to `archive/products/<id>_<ts>/` only when **`--approve`** is also passed (safety gate).

**What runs today:** lifecycle deprecation in `product.yaml`, archive of `products/<id>/`, and a **`resource_cleanup_stub`** record. **What does *not* run:** any automated teardown of cloud, DNS, billing, SaaS, or other external systems.

**Operator manual steps after a real apply:** Complete off-repo wind-down yourself—deprovision infra, cancel or reassign subscriptions, remove DNS and secrets, confirm spend stops, and follow any org-specific checklist. The stub exists so reports stay honest: **`executed` is always `false`** until real hooks are wired.

**Stable stub / report shape:** Each shutdown report uses schema `argus.autonomy.shutdown_report.v1` (`ProductShutdownReport.to_jsonable()`). The **`resource_cleanup`** object merges **`plan_provider_cleanup`** (structured seam) with stable **`notes`** from `RESOURCE_CLEANUP_STUB_NOTES` (see [stub-inventory.md](stub-inventory.md#resource_cleanup_stub-autonomy-shutdown)). Reports also include **`archive_plan`** (deprecate → archive → provider cleanup outline + economics rationale). Batch output uses `argus.autonomy.shutdown_batch.v1`. Artifacts are written under `runs/autonomy/shutdown_*.json` and `shutdown_latest.json`.
