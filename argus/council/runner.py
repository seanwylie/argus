"""
Council execution bridge.

Refinement owns the orchestration loop (``argus refine`` → ``run_refinement_cycle``).
This module documents the execution stack:

- **Deterministic**: always available; structured stub reviews.
- **OpenAI**: used when ``BackendType.OPENAI`` and LLM env allows (see ``argus.refinement.review``).
- **Cursor**: requested for grounded seats via ``BackendType.CURSOR``; not yet wired to an external
  Cursor CLI — ``argus.council.backends.resolve_active_backend`` falls back to deterministic and
  reviews carry ``backend_used: deterministic`` / ``llm_status: cursor_placeholder_fallback``.

Do not import this module expecting a second orchestrator; call refinement APIs instead.
"""

from __future__ import annotations

EXECUTION_OWNER = "argus.refinement.session.run_refinement_cycle"
