# Argus object model

Argus reasons about a **portfolio of product nodes** using a small set of canonical types. The flow is intentionally linear:

**Product metadata** → **signals** → **findings** → **decision candidates** → **action proposals** → **run records**

Nothing here is orchestration; these are the **nouns and verbs** future modules will share.

## Product side

### `ProductNode`

Loaded from `products/*/product.yaml` (plus resolved paths). It captures identity, declared metrics/cost/signal wiring, action commands, constraints, lifecycle metadata, and filesystem locations (`product_root`, `config_path`). Optional `type_info` mirrors loose fields such as `type`, `status`, and `state` from the file without forcing every product into one stack.

### `ProductLifecycle`

Wraps a `LifecycleStage` (`idea` → `build` → `validate` → `grow` → `maintain` → `decline` → `kill`) plus an optional `next_gate` string. `allowed_transitions()` / `can_transition_to()` encode a conservative transition graph; adjust the graph in code when policy changes.

## Observation and audit

### `SignalRecord`

One normalized observation from any adapter (filesystem, analytics, cost, logs, …). `payload` stays unstructured but JSON-safe. Use `severity_hint` and `confidence` when the source supports it.

### `Finding`

An issue or opportunity derived from one or more signals (`source_signal` IDs). `kind` uses `FindingKind` (e.g. `cost_risk`, `deprecation_candidate`). `evidence` holds supporting snippets or metrics snapshots—never secrets.

## Planning and action

### `DecisionCandidate`

A scored option Argus might take (could be many per product). Carries `action_type`, impact/cost hints, rationale, and `priority_score`.

### `ActionProposal`

The **selected** next action: concrete `command`, human `reason`, expected outcome, rollback notes, and `selected_at`.

## Execution artifact

### `RunRecord`

A single Argus **run** (audit, plan, or execute): time bounds, `RunStage`, participating `product_ids`, embedded `findings` and `selected_actions` for a self-contained export, plus `RunResult` and free-form `notes` / `metadata`.

## Code map

| Area | Python package |
|------|----------------|
| Dataclasses, enums, validation | `argus.core.models` |
| JSON/YAML helpers | `argus.core.serialize` |
| Example instances | `argus.core.fixtures` |
| Product discovery & inventory (scan `products/*/product.yaml` → `ProductNode`) | `argus.products` |
| Signal adapters & collection (`SignalRecord`) | `argus.signals` — see [Signals and adapters](signals-and-adapters.md) |
| Finding rules (`SignalRecord` → `Finding`) | `argus.findings` — see [Findings engine](findings-engine.md) |
| Lifecycle scoring + decision candidates | `argus.lifecycle`, `argus.decision` — see [Decisions and lifecycle](decisions-and-lifecycle.md) |
| Weekly portfolio planning (synthesis, `runs/planning/`) | `argus.planning` — see [Architecture](architecture.md); do **not** use the deprecated `argus.planner` name |
| Future JSON Schema / OpenAPI | Not published yet; runtime types live in `argus/core/models/` |

See **[model-contracts.md](model-contracts.md)** for identifier prefixes and which types are canonical vs package-specific.

## Serialization

- Use `argus.core.serialize.dumps_json` / `loads_json` for interchange.
- `dumps_yaml` / `loads_yaml` use PyYAML (`pyyaml` dependency).
- `product_node_from_dict` accepts a merged mapping shaped like `product.yaml` plus `product_root` and `config_path`.

## Validation

`validate_*` functions in `argus.core.models.validation` perform **structural** checks (required strings, enum types, confidence in \[0, 1\], nested validation for runs). They do **not** encode business policy (e.g. whether a transition is allowed in a given quarter—that belongs in orchestration later).
