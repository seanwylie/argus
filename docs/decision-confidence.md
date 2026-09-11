# Decision confidence and uncertainty (decision dynamics, not emotions)

Argus **does not model feelings**. It models **decision dynamics under uncertainty**: the quality of evidence, how incomplete or contradictory the picture is, and the risk of acting versus waiting.

The canonical implementation is **`argus.decision_assessment`**. It produces a **`DecisionContextAssessment`** with explicit 0–1 scores and factor lists:

| Score | Meaning |
|-------|---------|
| **confidence_score** | Trust in the *evidence base* (freshness, density, advisor consensus, decision stability). |
| **uncertainty_score** | How unknown or conflicted the state is (missing artifacts, stale temporal context, advisor spread, thin signals). |
| **risk_score** | Composite risk of moving forward (lifecycle/kill posture, cost vs cap, high-severity findings). |
| **recurrence_risk_score** | Repeated failure patterns (decision churn, failed experiments, escalation history). |
| **momentum_score** | Positive trends and validating experiment history. |
| **friction_score** | Capability gaps, pending approvals, policy bottlenecks. |
| **escalation_pressure** | Whether to proceed, gather data, delay, or involve a human (see `escalation_recommendation`). |
| **evidence_density_score** | Breadth/recency of core artifacts (signals, findings, decisions, trends, experiments). |

**Advisor council (interpretation):** `DecisionContextAssessment` may include **`advisor_alignment_score`** (0–1 or null), **`advisor_conflict_flag`**, and **`advisor_summary`**. These summarize advisor/council **interpretation** from local JSON (`runs/advisors/*.json`), not live production truth. When `runs/advisors/llm_council_<id>.latest.json` exists (from `argus llm council`), small **nudges** may apply to confidence/uncertainty; temporal freshness, risk, and constraints are not overridden. See [llm-integration.md](llm-integration.md).

**Artifact refinement:** open **`argus refine`** sessions for a product add a small **uncertainty** factor (`refinement_sessions_active`) via `argus.refinement.signals` — interpretation workload, not a quality score. Convergence remains **deterministic**; grounded vs outsider separation is documented in [councils.md](councils.md). See [artifact-refinement.md](artifact-refinement.md).

**Stub / gap awareness:** `score_confidence` can apply **`architecture_stub_risk`** (0–1) when the **top-ranked decision candidate** intersects documented architectural stubs or registry capability gaps — same tag rules as `argus.decision.stub_awareness.apply_stub_awareness_to_candidates`, so **irrelevant** placeholders do not reduce confidence. See [stub-inventory.md](stub-inventory.md).

## Human ideas → system concepts

- **Self-doubt** → low confidence, high uncertainty, conflicting signals, churn, stale grounding.
- **Confidence** → higher evidence density, aligned advisors (as *evidence*, not truth), fresh artifacts, stable recommendations.
- **Gun shy** → recurrence penalty, escalation pressure, friction.
- **Leap of faith** → bounded **exploratory** action: only in **exploration** strategy mode, early lifecycle stages, within cost/risk gates — see `argus.decision_assessment.leap`.
- **“Go to the doctor”** → `escalation_recommendation == escalate_human` or autonomy gate when assessment demands human review.

## Artifacts and CLI

- Per-product JSON: `runs/decision_assessment/latest/<product_id>.json`
- Embedded in decision bundles: `decision_context` on `runs/decisions/latest/<product_id>.json` when decisions are generated or refreshed.
- CLI: `argus confidence assess [product_id]`, `argus confidence summary`, `argus confidence explain <product_id>` (`--json` supported).

## Interactions

Scores are **deterministic** and **explainable** (factor breakdowns). They feed **dashboard** operator visibility (when `decision_context` is present), **escalation** packet enrichment, **experiment prioritization** metadata (`decision_context_by_product` on prioritization runs), and **autonomy** (optional block when human escalation is strongly recommended).

For architecture placement, see [architecture.md](architecture.md) and [system-flow.md](system-flow.md).

## System cohesion (single assessment surface)

**Confidence / uncertainty / risk / recurrence / momentum / friction / escalation pressure** are defined only in **`DecisionContextAssessment`** and reused by dashboard slices, escalation enrichment, experiment prioritization metadata, and autonomy gates. Do not duplicate these names with different formulas elsewhere—extend `argus.decision_assessment` if new factors are needed.
