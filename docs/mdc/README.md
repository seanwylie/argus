# Argus MDC (Model Directive Constitution)

Machine- and human-readable behavioral rules for implementation agents live under:

**`.cursor/rules/mdc/`**

| Layer | Files | Purpose |
|-------|--------|---------|
| PRECURSOR | `000`–`004` | Prime directive, modes, self-improvement, precedence, drift |
| CORE | `010`, `020`, `030`, `050` | Execution, artifacts, confidence/escalation, iteration |
| EXTENSION | `040`, `060`, `070` | Product modes, onboarding, roadmap (stubs; evolve via 002) |

**Proposals:** Add `docs/mdc/proposals/PROP-*.md` or put YAML `mdc_proposal` in commit body per `002_argus_self_improvement_protocol.mdc`. Historical field logs and revision journals are not part of the public tree.

**Drift log (optional):** `docs/mdc/drift_log.yaml` — see `004_argus_drift_detection.mdc`.
