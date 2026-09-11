"""
Mission / ethos profiles — first-class operator intent for downstream policy and decisions.

Truth-producing layers (signals, audit, findings) stay independent; mission influences interpretation
and policy only through explicit, inspectable contracts.
"""

from __future__ import annotations

import copy
import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from argus.core.serialize import dumps_json

MISSION_REGISTRY_SCHEMA = "argus.mission_registry.v1"
MISSION_PROFILE_SCHEMA = "argus.mission_profile.v1"
MISSION_EFFECTIVE_SCHEMA = "argus.mission_effective.v1"

RISK_POSTURES = frozenset({"conservative", "moderate", "aggressive"})


@dataclass(frozen=True)
class MissionExperimentComposition:
    """
    Structured mission composition for read-only experiments (registry profile ids only).

    Mirrors product ``mission:`` shape: **objective** profile plus optional **driver** and **guardrail**
    overlays — each id must exist in ``config/mission_profiles.yaml`` (same rules as
    :func:`argus.mission.product_mission.validate_product_mission_registry`).
    """

    objective: str
    drivers: tuple[str, ...] = ()
    guardrails: tuple[str, ...] = ()

    def label(self) -> str:
        parts = [f"objective={self.objective}"]
        if self.drivers:
            parts.append("drivers=" + ",".join(self.drivers))
        if self.guardrails:
            parts.append("guardrails=" + ",".join(self.guardrails))
        return " ".join(parts)

    def validate_registry(self, repo_root: Path) -> None:
        root = repo_root.resolve()
        seen: set[str] = set()
        for mid in (self.objective, *self.drivers, *self.guardrails):
            m = str(mid).strip()
            if not m:
                raise ValueError("mission composition: empty mission profile id")
            if m in seen:
                raise ValueError(f"mission composition: duplicate profile id {m!r}")
            seen.add(m)
            load_mission_by_id(root, m)


# Experiment-only mission resolution overrides (read-only comparisons). Never persisted; does not affect
# truth-producing layers. Wired into :func:`resolve_effective_mission_id` /
# :func:`resolve_effective_mission_id_for_product` and operator policy cache keys only.
_MISSION_EXPERIMENT_PRODUCT: ContextVar[dict[str, str] | None] = ContextVar(
    "_MISSION_EXPERIMENT_PRODUCT", default=None
)
_MISSION_EXPERIMENT_COMPOSITION: ContextVar[dict[str, MissionExperimentComposition] | None] = ContextVar(
    "_MISSION_EXPERIMENT_COMPOSITION", default=None
)
_MISSION_EXPERIMENT_GLOBAL: ContextVar[str | None] = ContextVar("_MISSION_EXPERIMENT_GLOBAL", default=None)


def mission_experiment_cache_token() -> tuple[Any, ...]:
    """Stable token for :func:`argus.policy.operator_policy.load_operator_policy` cache invalidation."""
    ep = _MISSION_EXPERIMENT_PRODUCT.get()
    eg = _MISSION_EXPERIMENT_GLOBAL.get()
    ec = _MISSION_EXPERIMENT_COMPOSITION.get()
    comp_items: tuple[Any, ...] = ()
    if isinstance(ec, dict) and ec:
        comp_items = tuple(
            (pid, c.objective, tuple(c.drivers), tuple(c.guardrails))
            for pid, c in sorted(ec.items())
        )
    if not ep and not eg and not comp_items:
        return ()
    items = tuple(sorted((str(k), str(v)) for k, v in (ep or {}).items()))
    return (items, eg or "", comp_items)


@contextmanager
def mission_experiment_scope(
    *,
    product_mission_overrides: dict[str, str] | None = None,
    product_mission_compositions: dict[str, MissionExperimentComposition] | None = None,
    global_mission_id: str | None = None,
):
    """
    Temporarily force mission resolution for comparison tooling.

    * ``product_mission_compositions`` — map ``product_id`` → :class:`MissionExperimentComposition`
      (structured objective + driver + guardrail overlays; wins over simple overrides and ``product.yaml``).
    * ``product_mission_overrides`` — map ``product_id`` → mission profile id (single-profile shorthand).
    * ``global_mission_id`` — force repository-level resolution (env / ``current.json`` / default skipped).

    Restores previous context on exit; safe to nest only if inner scopes reset outer tokens (avoid nesting).
    """
    t0 = _MISSION_EXPERIMENT_COMPOSITION.set(
        product_mission_compositions if product_mission_compositions else None
    )
    t1 = _MISSION_EXPERIMENT_PRODUCT.set(product_mission_overrides)
    eg: str | None = None
    if isinstance(global_mission_id, str) and global_mission_id.strip():
        eg = global_mission_id.strip()
    t2 = _MISSION_EXPERIMENT_GLOBAL.set(eg)
    try:
        yield
    finally:
        _MISSION_EXPERIMENT_COMPOSITION.reset(t0)
        _MISSION_EXPERIMENT_PRODUCT.reset(t1)
        _MISSION_EXPERIMENT_GLOBAL.reset(t2)


_CURRENT_JSON = "current.json"
_EFFECTIVE_JSON = "mission_effective.json"


def mission_runs_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "mission"


def default_mission_registry() -> dict[str, Any]:
    """Frozen default registry when ``config/mission_profiles.yaml`` is absent."""
    return {
        "schema": MISSION_REGISTRY_SCHEMA,
        "default_mission_id": "revenue",
        "profiles": {
            "revenue": {
                "id": "revenue",
                "primary_objective": "Maximize sustainable revenue and healthy unit economics without sacrificing trust.",
                "drivers": [
                    "conversion and retention",
                    "margin and cost discipline",
                    "pricing and packaging clarity",
                ],
                "risk_posture": "moderate",
                "policy_influence_hints": {
                    "emphasis": (
                        "Prefer portfolio and decision moves with measurable revenue or margin impact where evidence exists."
                    ),
                },
                "weights": {"revenue_alignment": 1.0, "cost_sensitivity": 0.8},
            },
            "education": {
                "id": "education",
                "primary_objective": "Maximize learning outcomes, clarity, and equitable access for learners.",
                "drivers": [
                    "pedagogical quality",
                    "curriculum coherence",
                    "accessibility and comprehension",
                ],
                "risk_posture": "conservative",
                "policy_influence_hints": {
                    "emphasis": "Favor stability, clarity, and safety over aggressive shipping when evidence is thin.",
                },
                "weights": {"learning_quality": 1.0, "safety_and_clarity": 0.95},
            },
            "engagement": {
                "id": "engagement",
                "primary_objective": "Build durable, ethical engagement and retention without dark patterns.",
                "drivers": [
                    "product delight",
                    "habit formation (transparent)",
                    "trust and brand",
                ],
                "risk_posture": "moderate",
                "policy_influence_hints": {
                    "emphasis": (
                        "Balance growth tactics with transparency; avoid coercion in orchestration and messaging."
                    ),
                },
                "weights": {"retention_alignment": 1.0, "trust_preservation": 0.9},
            },
        },
    }


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for k, v in override.items():
            if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                out[k] = _deep_merge(out[k], v)
            else:
                out[k] = copy.deepcopy(v)
        return out
    return copy.deepcopy(override)


def _registry_path(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "config" / "mission_profiles.yaml"


def load_mission_registry(repo_root: Path) -> dict[str, Any]:
    """Load mission registry from ``config/mission_profiles.yaml`` merged over code defaults."""
    root = repo_root.resolve()
    base = default_mission_registry()
    cfg = _registry_path(root)
    if not cfg.is_file():
        return copy.deepcopy(base)
    try:
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise ValueError(f"failed to read mission registry from {cfg}: {e}") from e
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("mission_profiles.yaml must parse to a mapping")
    merged = _deep_merge(base, raw)
    if str(merged.get("schema") or "") != MISSION_REGISTRY_SCHEMA:
        merged["schema"] = MISSION_REGISTRY_SCHEMA
    return merged


def validate_mission_profile(profile: dict[str, Any], *, mission_id: str) -> None:
    if str(profile.get("id") or "") != mission_id:
        raise ValueError(
            f"mission profile id mismatch: expected {mission_id!r}, got {profile.get('id')!r}"
        )
    po = profile.get("primary_objective")
    if not isinstance(po, str) or not po.strip():
        raise ValueError(f"mission {mission_id!r}: primary_objective must be a non-empty string")
    drivers = profile.get("drivers")
    if not isinstance(drivers, list) or not all(isinstance(x, str) and x.strip() for x in drivers):
        raise ValueError(f"mission {mission_id!r}: drivers must be a list of non-empty strings")
    rp = str(profile.get("risk_posture") or "").strip().lower()
    if rp not in RISK_POSTURES:
        raise ValueError(
            f"mission {mission_id!r}: risk_posture must be one of {sorted(RISK_POSTURES)}, got {rp!r}"
        )
    hints = profile.get("policy_influence_hints")
    if hints is not None and not isinstance(hints, dict):
        raise ValueError(
            f"mission {mission_id!r}: policy_influence_hints must be an object or omitted"
        )
    weights = profile.get("weights")
    if weights is not None and not isinstance(weights, dict):
        raise ValueError(f"mission {mission_id!r}: weights must be an object or omitted")
    if isinstance(weights, dict):
        for k, v in weights.items():
            if not isinstance(v, (int, float)):
                raise ValueError(f"mission {mission_id!r}: weights.{k} must be numeric")


def _normalize_profile(raw: dict[str, Any], mission_id: str) -> dict[str, Any]:
    p = copy.deepcopy(raw)
    p["id"] = mission_id
    p["schema"] = MISSION_PROFILE_SCHEMA
    validate_mission_profile(p, mission_id=mission_id)
    return p


def load_mission_by_id(repo_root: Path, mission_id: str) -> dict[str, Any]:
    """
    Return a single mission profile by id (validated, with ``schema: argus.mission_profile.v1``).

    Raises ``ValueError`` if the id is unknown or invalid.
    """
    reg = load_mission_registry(repo_root)
    profiles = reg.get("profiles") or {}
    if not isinstance(profiles, dict):
        raise ValueError("mission registry profiles must be a mapping")
    mid = str(mission_id).strip()
    if not mid:
        raise ValueError("mission_id must be non-empty")
    raw = profiles.get(mid)
    if not isinstance(raw, dict):
        known = sorted(str(k) for k in profiles.keys())
        raise ValueError(f"unknown mission_id {mid!r}; known profiles: {known}")
    return _normalize_profile(raw, mid)


def load_default_mission(repo_root: Path) -> dict[str, Any]:
    """Load the configured default mission profile (``default_mission_id`` in registry)."""
    reg = load_mission_registry(repo_root)
    did = str(reg.get("default_mission_id") or "").strip()
    if not did:
        raise ValueError("mission registry missing default_mission_id")
    return load_mission_by_id(repo_root, did)


def _read_current_override(repo_root: Path) -> str | None:
    p = mission_runs_dir(repo_root) / _CURRENT_JSON
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    mid = raw.get("mission_id")
    if isinstance(mid, str) and mid.strip():
        return mid.strip()
    return None


def resolve_effective_mission_id(repo_root: Path) -> tuple[str, list[str]]:
    """
    Resolve **repository-level** mission id (fallback / test override when no product context).

    This does **not** read ``product.yaml``; use :func:`resolve_effective_mission_id_for_product`
    for per-product canonical resolution.

    Precedence: ``ARGUS_MISSION_ID`` env > ``runs/mission/current.json`` > registry ``default_mission_id``.
    Returns ``(mission_id, resolution_chain)`` where chain is human-readable steps.
    """
    root = repo_root.resolve()
    reg = load_mission_registry(root)
    default_id = str(reg.get("default_mission_id") or "").strip()
    if not default_id:
        raise ValueError("mission registry missing default_mission_id")

    chain: list[str] = [f"config default_mission_id={default_id!r}"]

    eg = _MISSION_EXPERIMENT_GLOBAL.get()
    if isinstance(eg, str) and eg.strip():
        return eg.strip(), [f"mission_experiment.global_mission_id={eg.strip()!r}"]

    env_id = os.environ.get("ARGUS_MISSION_ID")
    if isinstance(env_id, str) and env_id.strip():
        return env_id.strip(), [f"ARGUS_MISSION_ID={env_id.strip()!r}"]

    cur = _read_current_override(root)
    if cur:
        chain.append(f"runs/mission/{_CURRENT_JSON} mission_id={cur!r}")
        return cur, chain

    return default_id, chain


def read_product_mission_id(repo_root: Path, product_id: str) -> str | None:
    """
    Return the **objective** mission profile id from ``product.yaml`` if the product declares a mission.

    Legacy ``mission_id:`` and structured ``mission: { objective: ... }`` are supported.
    Raises ``ValueError`` if ``mission`` / ``mission_id`` is present but invalid.
    """
    from argus.mission.product_mission import parse_product_mission_from_yaml, read_product_yaml

    raw = read_product_yaml(repo_root, product_id)
    spec, err = parse_product_mission_from_yaml(raw)
    if err:
        raise ValueError(err)
    return spec.objective if spec else None


def resolve_effective_mission_id_for_product(repo_root: Path, product_id: str) -> tuple[str, list[str]]:
    """
    Canonical mission id for a **product**: product ``mission_id`` first, else repository fallback.

    Precedence:
    1. Active :func:`mission_experiment_scope` structured composition (objective profile id)
    2. Active :func:`mission_experiment_scope` product override (single profile id)
    3. ``products/<id>/product.yaml`` → structured ``mission`` or legacy ``mission_id`` (objective profile id)
    4. Else same as :func:`resolve_effective_mission_id` (env → ``runs/mission/current.json`` → default)
    """
    root = repo_root.resolve()
    pid = str(product_id).strip()
    exp_c = _MISSION_EXPERIMENT_COMPOSITION.get()
    if isinstance(exp_c, dict) and pid in exp_c:
        comp = exp_c[pid]
        oid = str(comp.objective).strip()
        if oid:
            return oid, [f"mission_experiment.composition objective={oid!r} (product {pid!r})"]
    exp = _MISSION_EXPERIMENT_PRODUCT.get()
    if isinstance(exp, dict):
        if pid in exp:
            mid = str(exp[pid]).strip()
            if mid:
                return mid, [f"mission_experiment.product_override={mid!r} (product {pid!r})"]
    try:
        pm = read_product_mission_id(root, product_id)
    except ValueError:
        raise
    if pm:
        return pm, [f"product.yaml mission objective={pm!r} (product {product_id!r})"]
    return resolve_effective_mission_id(root)


def resolve_effective_mission_for_product(repo_root: Path, product_id: str) -> dict[str, Any]:
    """
    Resolved mission for operator/policy consumers scoped to one product.

    Payload includes ``mission`` (the **objective** profile), optional ``structured_mission``
    (objective, drivers, guardrails, effective risk posture), and ``resolution_scope``:
    ``\"product\"`` when the product declares a mission; ``\"repository_fallback\"`` otherwise.

    Prefer :func:`resolve_product_mission` for operational use.
    """
    root = repo_root.resolve()
    pid = str(product_id).strip()

    exp_c = _MISSION_EXPERIMENT_COMPOSITION.get()
    if isinstance(exp_c, dict) and pid in exp_c:
        comp = exp_c[pid]
        comp.validate_registry(root)
        chain: list[str] = [
            f"mission_experiment.composition objective={comp.objective!r} (product {pid!r})",
        ]
        for d in comp.drivers:
            chain.append(f"mission_experiment.composition driver={d!r}")
        for g in comp.guardrails:
            chain.append(f"mission_experiment.composition guardrail={g!r}")
        profile = load_mission_by_id(root, comp.objective)
        prof_rp = str(profile.get("risk_posture") or "moderate").strip().lower()
        structured_mission: dict[str, Any] = {
            "objective": comp.objective,
            "drivers": list(comp.drivers),
            "guardrails": list(comp.guardrails),
            "risk_posture": None,
            "effective_risk_posture": prof_rp,
        }
        return {
            "schema": MISSION_EFFECTIVE_SCHEMA,
            "resolved_mission_id": comp.objective,
            "resolution_chain": chain,
            "mission": profile,
            "structured_mission": structured_mission,
            "resolved_at_utc": datetime.now(timezone.utc).isoformat(),
            "product_id": pid,
            "resolution_scope": "product",
        }

    exp = _MISSION_EXPERIMENT_PRODUCT.get()
    if isinstance(exp, dict) and pid in exp:
        mid = str(exp[pid]).strip()
        if not mid:
            raise ValueError("mission experiment product override must be non-empty")
        chain_ov = [f"mission_experiment.product_override={mid!r} (product {pid!r})"]
        profile = load_mission_by_id(root, mid)
        rp_eff = str(profile.get("risk_posture") or "moderate").strip().lower()
        structured_mission = {
            "objective": mid,
            "drivers": [],
            "guardrails": [],
            "risk_posture": None,
            "effective_risk_posture": rp_eff,
        }
        return {
            "schema": MISSION_EFFECTIVE_SCHEMA,
            "resolved_mission_id": mid,
            "resolution_chain": chain_ov,
            "mission": profile,
            "structured_mission": structured_mission,
            "resolved_at_utc": datetime.now(timezone.utc).isoformat(),
            "product_id": pid,
            "resolution_scope": "product",
        }

    from argus.mission.product_mission import parse_product_mission_from_yaml, read_product_yaml

    raw = read_product_yaml(root, pid)
    spec, perr = parse_product_mission_from_yaml(raw)
    if perr:
        raise ValueError(perr)
    if not spec:
        mid, chain = resolve_effective_mission_id(root)
        profile = load_mission_by_id(root, mid)
        return {
            "schema": MISSION_EFFECTIVE_SCHEMA,
            "resolved_mission_id": mid,
            "resolution_chain": chain,
            "mission": profile,
            "structured_mission": None,
            "resolved_at_utc": datetime.now(timezone.utc).isoformat(),
            "product_id": pid,
            "resolution_scope": "repository_fallback",
        }

    mid = spec.objective
    chain: list[str] = [f"product.yaml mission.objective={mid!r} (product {pid!r})"]
    if spec.drivers:
        chain.append(f"mission.drivers={list(spec.drivers)!r}")
    if spec.guardrails:
        chain.append(f"mission.guardrails={list(spec.guardrails)!r}")
    profile = load_mission_by_id(root, mid)
    prof_rp = str(profile.get("risk_posture") or "moderate").strip().lower()
    rp_eff = str(spec.risk_posture).strip().lower() if spec.risk_posture else prof_rp
    structured_mission = {
        "objective": mid,
        "drivers": list(spec.drivers),
        "guardrails": list(spec.guardrails),
        "risk_posture": spec.risk_posture,
        "effective_risk_posture": rp_eff,
    }
    return {
        "schema": MISSION_EFFECTIVE_SCHEMA,
        "resolved_mission_id": mid,
        "resolution_chain": chain,
        "mission": profile,
        "structured_mission": structured_mission,
        "resolved_at_utc": datetime.now(timezone.utc).isoformat(),
        "product_id": pid,
        "resolution_scope": "product",
    }


def resolve_product_mission(repo_root: Path, product_id: str) -> dict[str, Any]:
    """
    **Operational** mission for an existing product: structured ``mission:`` (objective/drivers/guardrails)
    or legacy ``mission_id`` (objective shorthand); repository fallback when absent.

    Payload includes ``structured_mission`` when the product declares a mission. Same shape as
    :func:`resolve_effective_mission_for_product`.
    """
    return resolve_effective_mission_for_product(repo_root, product_id)


def resolve_effective_mission(repo_root: Path) -> dict[str, Any]:
    """
    Load the **repository-level** effective mission (fallback / test override, ``mission_effective.json``).

    Does not read ``product.yaml``. For per-product resolution use
    :func:`resolve_effective_mission_for_product` or :func:`resolve_product_mission`.

    Raises ``ValueError`` if the resolved id is unknown or invalid.
    """
    mid, chain = resolve_effective_mission_id(repo_root)
    profile = load_mission_by_id(repo_root, mid)
    return {
        "schema": MISSION_EFFECTIVE_SCHEMA,
        "resolved_mission_id": mid,
        "resolution_chain": chain,
        "mission": profile,
        "resolved_at_utc": datetime.now(timezone.utc).isoformat(),
        "resolution_scope": "repository",
    }


def resolve_global_mission(repo_root: Path) -> dict[str, Any]:
    """
    **Global / default** mission for the repo: used when no product is in scope — fallback for
    products without ``mission_id``, **testing/simulation** overrides (``ARGUS_MISSION_ID``,
    ``runs/mission/current.json``), and repository tooling.

    Does **not** apply to :func:`load_operator_policy` when ``product_id`` is set and the product
    declares ``mission_id``; that path uses :func:`resolve_product_mission` only.

    Same payload as :func:`resolve_effective_mission`.
    """
    return resolve_effective_mission(repo_root)


def resolve_creation_mission(repo_root: Path) -> dict[str, Any]:
    """
    **Placeholder** — mission profile to use as **default bias** when **creating** new products
    (scaffold / future creation pipeline).

    Not wired into ``argus products create`` yet. Currently returns the same resolution as
    :func:`resolve_global_mission` (registry ``default_mission_id`` with env/current.json overrides).
    A future registry field (e.g. ``creation_default_mission_id``) may specialize this without
    changing operational :func:`resolve_product_mission` behavior.
    """
    return resolve_global_mission(repo_root)


def write_mission_effective_artifact(
    repo_root: Path, payload: dict[str, Any] | None = None
) -> Path:
    """
    Write ``runs/mission/mission_effective.json`` for inspection and downstream tooling.

    If ``payload`` is omitted, calls :func:`resolve_global_mission` / :func:`resolve_effective_mission`.
    """
    root = repo_root.resolve()
    pl = payload if payload is not None else resolve_global_mission(root)
    if str(pl.get("schema") or "") != MISSION_EFFECTIVE_SCHEMA:
        raise ValueError(f"payload schema must be {MISSION_EFFECTIVE_SCHEMA!r}")
    d = mission_runs_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    path = d / _EFFECTIVE_JSON
    path.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    return path


@dataclass(frozen=True)
class Mission:
    """Typed view of a normalized mission profile (optional convenience)."""

    id: str
    primary_objective: str
    drivers: tuple[str, ...]
    risk_posture: str
    policy_influence_hints: dict[str, Any] | None
    weights: dict[str, float] | None

    @classmethod
    def from_dict(cls, profile: dict[str, Any]) -> Mission:
        hints = profile.get("policy_influence_hints")
        w = profile.get("weights")
        weights_out: dict[str, float] | None = None
        if isinstance(w, dict):
            weights_out = {}
            for k, v in w.items():
                try:
                    weights_out[str(k)] = float(v)
                except (TypeError, ValueError):
                    raise ValueError(f"weights.{k} must be numeric") from None
        dr = profile.get("drivers") or []
        return cls(
            id=str(profile.get("id") or ""),
            primary_objective=str(profile.get("primary_objective") or ""),
            drivers=tuple(str(x) for x in dr if isinstance(x, str)),
            risk_posture=str(profile.get("risk_posture") or "").lower(),
            policy_influence_hints=dict(hints) if isinstance(hints, dict) else None,
            weights=weights_out,
        )
