# Product creation proposals

**Schema:** `argus.product_creation_proposals.v1`
**CLI:** `argus products propose-creation [--json] [--no-save]`
**Module:** `argus/products/creation.py`
**Artifacts:** `runs/products/creation/latest.{json,md}` + timestamped copies

## Purpose

Give Argus a disciplined, evidence-based way to answer: **"what should we build next?"**

This is proposal-only — it does not scaffold directories, write `product.yaml` files, or alter
the existing portfolio. It identifies gaps in the current portfolio and produces candidate
product concepts aligned with the **creation mission** (the global/fallback mission profile).

## Inputs

| Source | What it provides |
|---|---|
| Product inventory (`products/*/product.yaml`) | Valid products, lifecycle stages, types |
| Creation mission (`resolve_creation_mission`) | Objective, drivers, risk posture for new-product bias |
| Portfolio outcomes (`runs/portfolio/outcomes/latest.json`) | Per-product trajectories (improving / stable / regressing) |
| Portfolio patterns (`runs/portfolio/patterns/latest.json`) | Systemic cross-product issues |
| Operator queue (`runs/portfolio/operator_queue/latest.json`) | Current prioritization context |

All inputs are optional except the product inventory. When portfolio artifacts are absent,
gap detection is limited to structural observations (empty portfolio, lifecycle skew, type
concentration).

## Gap detection

The evaluator identifies portfolio gaps using deterministic heuristics:

| Gap ID | Trigger | Severity |
|---|---|---|
| `gap.empty_portfolio` | No valid products | high |
| `gap.single_product_portfolio` | Only one product | medium |
| `gap.no_early_stage_products` | All products in maintain/decline/kill | high |
| `gap.pipeline_aging` | >60% late-stage, no early-stage | medium |
| `gap.portfolio_stagnation` | No products improving, majority regressing (from outcomes) | high |
| `gap.systemic_pattern_pressure` | ≥2 high-severity systemic patterns (from patterns) | medium |
| `gap.mission_driver_uncovered` | Mission drivers with no explicit product coverage | low |
| `gap.type_monoculture` | All products share a single type | low |

## Proposal structure

The top-level JSON payload includes **`mission_machine_contract`**, which points consumers at the
machine-usable mission fields (registry profile ids) and at where human prose lives:

```json
{
  "schema": "argus.product_creation_proposals.v1",
  "mission_machine_contract": {
    "initial_suggested_mission_schema": "argus.initial_suggested_mission.v1",
    "fields_are_registry_profile_ids": true,
    "human_readable_mission_phrases_field": "mission_human_context.creation_mission_driver_phrases"
  }
}
```

Each proposal carries structured mission data plus human context:

```json
{
  "proposal_id": "creation_a1b2c3d4e5f6",
  "concept_title": "Early-stage product to renew portfolio pipeline",
  "creation_mission_used": "revenue",
  "rationale": "Portfolio has no early-stage products; a new idea/validate-stage product would...",
  "related_products": ["alpha", "beta"],
  "expected_role_in_portfolio": "pipeline",
  "initial_suggested_mission": {
    "schema": "argus.initial_suggested_mission.v1",
    "objective": "revenue",
    "drivers": ["education"],
    "guardrails": [],
    "risk_posture": "moderate",
    "mission_profile_fields_are_registry_ids": true,
    "note": "Structured mission: objective/drivers/guardrails are mission registry profile ids..."
  },
  "mission_human_context": {
    "creation_mission_driver_phrases": ["conversion and retention", "margin and cost discipline"],
    "creation_mission_primary_objective_text": "Maximize sustainable revenue.",
    "structured_mission_ruleset": "creation_gap_mapping_v1"
  },
  "confidence": "medium",
  "evidence_summary": {
    "gap_id": "gap.no_early_stage_products",
    "gap_title": "No products in early lifecycle stages",
    "gap_severity": "high",
    "opportunity_type": "pipeline_renewal"
  }
}
```

**Machine vs human:** `initial_suggested_mission.objective`, `drivers`, `guardrails`, and
`risk_posture` are **registry profile ids** (same id namespace as `config/mission_profiles.yaml`).
Lines like “conversion and retention” from the creation mission YAML appear only under
`mission_human_context.creation_mission_driver_phrases`, not in `drivers`.

**Deterministic mapping:** `structured_mission_ruleset` is `creation_gap_mapping_v1`. The evaluator
chooses complementary profile ids from the registry from the detected gap’s `opportunity_type` and
severity (e.g. how many driver slots, which guardrail ids). Older proposal snapshots without
`schema` / `mission_profile_fields_are_registry_ids` are still accepted; scaffolding falls back to
filtering list entries to known profile ids.

### Roles

| Role | Meaning |
|---|---|
| `seed` | First product in an empty portfolio |
| `complement` | Diversification of a concentrated portfolio |
| `pipeline` | Early-stage renewal for an aging portfolio |
| `growth` | Growth injection for a stagnating portfolio |
| `remedy` | Structural fix for systemic pattern pressure |
| `mission_coverage` | Coverage for uncovered mission drivers |

## Recommendations vs scaffolding

Proposals are **advisory artifacts**. The operator reviews them, decides whether to act,
and uses `argus products create <name>` to scaffold a real product when ready.

The `initial_suggested_mission` in each proposal is intended to copy into the new product’s
`mission:` block in `product.yaml` with minimal loss (see [product-creation-scaffold.md](product-creation-scaffold.md)).
Operators may still refine missions after scaffolding.

## CLI usage

```
# Human-readable markdown to stdout + artifacts
argus products propose-creation

# Machine-readable JSON
argus products propose-creation --json

# Dry run (no artifact writes)
argus products propose-creation --no-save
```

## Artifacts written

| Path | Format | Description |
|---|---|---|
| `runs/products/creation/latest.json` | JSON | Full proposal payload |
| `runs/products/creation/latest.md` | Markdown | Human-readable proposal summary |
| `runs/products/creation/<timestamp>.json` | JSON | Timestamped copy |
| `runs/products/creation/<timestamp>.md` | Markdown | Timestamped copy |

## Design principles

- **Deterministic:** Same inputs → same gaps and proposals (modulo timestamps).
- **Mission-grounded:** Every proposal references the creation mission used.
- **Evidence-based:** Every proposal traces back to a detected gap with concrete evidence.
- **Proposal-only:** No side effects on the product tree. The operator decides.
- **Incremental:** Runs meaningfully even with an empty portfolio or no portfolio artifacts.
