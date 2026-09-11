"""
Deterministic combinatorial synthesis: products × signals × domains × monetization × channels.

All ideas are tagged ``IdeaType.INVENT`` and ``IdeaSource.SYNTHESIS``. Generation is
seeded and hash-driven so runs are reproducible without external APIs.

When ``product_id`` is set, synthesis prefers **finding- and signal-record-grounded** rows
(portfolio cross-product mashups are skipped) so ideas cite real artifacts.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from argus.core.models.enums import SignalType
from argus.core.models.signal import SignalRecord
from argus.findings.persistence import load_latest_findings
from argus.idea_generation.models import Idea, IdeaSource, IdeaType, new_idea_id
from argus.idea_generation.score import apply_scores
from argus.idea_generation.synthesis_copy import finalize_synthesis_description
from argus.idea_generation.synthesis_grounding import build_grounding
from argus.products.inventory import build_inventory
from argus.signals.manifest_collect import PLACEHOLDER_SOURCE
from argus.signals.persistence import load_latest_bundle

# --- Canonical vocabularies (not a competing type system — labels for prompts / UI) ---

MONETIZATION_TYPES: tuple[str, ...] = (
    "subscription",
    "ads",
    "usage_based",
    "affiliate",
    "marketplace_fees",
    "tipping",
    "enterprise_contract",
    "licensing",
    "data_licensing",
    "sponsorship",
    "hybrid",
)

CHANNEL_TYPES: tuple[str, ...] = (
    "web",
    "mobile_app",
    "api",
    "email_digest",
    "social_feed",
    "live_stream",
    "ar_layer",
    "voice_assistant",
    "sms_ussd",
    "community_forum",
    "events_ticketing",
    "browser_extension",
    "embed_widget",
    "hybrid",
)

# Domains / mechanics for "weird" crossovers (not product types — creative hooks).
UNEXPECTED_DOMAINS: tuple[str, ...] = (
    "real_time_commentary",
    "prediction_markets",
    "live_auctions",
    "crowdsourced_routing",
    "ambient_audio_presence",
    "spatial_storytelling",
    "micro_wagers",
    "reaction_graphs",
    "tip_jar_mobs",
    "sponsor_voiceovers",
    "collaborative_filters",
    "streak_rituals",
    "live_leaderboards",
    "weather_triggered_promos",
    "civic_pulse_feeds",
)

MECHANICS: tuple[str, ...] = (
    "gamification",
    "parlay_bundles",
    "commentary_layer",
    "reaction_streams",
    "loot_timing",
    "async_duels",
    "co_viewing_rooms",
    "proof_of_attention",
)


def _h(seed: str, *parts: str) -> bytes:
    return hashlib.sha256(":".join((seed,) + parts).encode()).digest()


def _pick(b: bytes, choices: tuple[str, ...]) -> str:
    return choices[int.from_bytes(b[:8], "big") % len(choices)]


def _short_name(pid: str, name: str | None) -> str:
    n = (name or "").strip()
    return n if n else pid


def _clip(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _non_manifest_records(records: list) -> list[SignalRecord]:
    return [r for r in records if getattr(r, "source", None) != PLACEHOLDER_SOURCE]


def _doctrine_repo_path(repo_root: Path, product_root_rel: str) -> str | None:
    p = (repo_root / product_root_rel / "doctrine.yaml").resolve()
    if not p.is_file():
        return None
    try:
        return str(p.relative_to(repo_root.resolve()))
    except ValueError:
        return f"{product_root_rel}/doctrine.yaml"


def _collect_signal_types(repo_root: Path, product_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    order: list[str] = []
    root = repo_root.resolve()
    for pid in product_ids:
        b = load_latest_bundle(root, pid)
        if b is None:
            continue
        for r in b.records:
            v = r.signal_type.value
            if v not in seen:
                seen.add(v)
                order.append(v)
    return order


def _collect_finding_kinds(repo_root: Path, product_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    order: list[str] = []
    root = repo_root.resolve()
    for pid in product_ids:
        fb = load_latest_findings(root, pid)
        if fb is None:
            continue
        for f in fb.findings:
            v = f.kind.value
            if v not in seen:
                seen.add(v)
                order.append(v)
    return order


def _temporal_signal_tags(repo_root: Path, product_ids: list[str]) -> list[str]:
    """Prefer metrics / analytics / health / temporal rows as 'live' hooks."""
    tags: list[str] = []
    root = repo_root.resolve()
    temporalish = frozenset(
        {
            SignalType.METRICS.value,
            SignalType.ANALYTICS.value,
            SignalType.HEALTH.value,
            SignalType.TEMPORAL.value,
        }
    )
    for pid in product_ids:
        b = load_latest_bundle(root, pid)
        if b is None:
            continue
        for r in b.records:
            if r.signal_type.value in temporalish:
                tags.append(f"{pid}:{r.signal_type.value}")
    return sorted(set(tags))


def _product_pairs(
    ids: list[str],
    types_by_id: dict[str, str],
    *,
    seed: str,
    max_pairs: int,
) -> list[tuple[str, str, str]]:
    """
    Return (id_a, id_b, reason) with reason in ('different_type', 'forced_weird', 'lexical').

    Prefer different product ``type`` strings to avoid trivial same-slab pairs when possible.
    """
    pairs: list[tuple[str, str, str]] = []
    n = len(ids)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = ids[i], ids[j]
            ta, tb = types_by_id.get(a, ""), types_by_id.get(b, "")
            if ta and tb and ta != tb:
                pairs.append((a, b, "different_type"))
            else:
                hb = _h(seed, "pair_weird", a, b)
                if int.from_bytes(hb[:4], "big") % 3 == 0:
                    pairs.append((a, b, "forced_weird"))
                else:
                    pairs.append((a, b, "lexical"))
            if len(pairs) >= max_pairs * 3:
                break
        if len(pairs) >= max_pairs * 3:
            break
    scored = sorted(
        pairs,
        key=lambda t: hashlib.sha256(f"{seed}:{t[0]}:{t[1]}".encode()).hexdigest(),
    )
    return scored[:max_pairs]


def synthesize_ideas(
    repo_root: Path,
    product_id: str | None = None,
    *,
    seed: str = "",
    max_ideas: int = 28,
) -> list[Idea]:
    """
    Build invent-tagged ideas from portfolio context.

    Patterns:
    - **finding_grounded** (single-product): explicit finding id + repo path (+ doctrine path if present)
    - **pair**: product A + product B (portfolio mode only)
    - **signal_monetization**: concrete signal records (single-product) or signal-type lattice (portfolio)
    - **channel_domain**: channel + domain hook; single-product adds ``repo_path`` (``file_context``)
    - **finding_twist**: portfolio alien mash-up OR single-product finding-led twist
    - **temporal_domain**: temporal tags when present
    """
    root = repo_root.resolve()
    inv = build_inventory(root)
    if not inv.valid:
        return []

    ids = sorted(inv.valid.keys())
    types_by_id: dict[str, str] = {}
    names_by_id: dict[str, str] = {}
    for pid, rec in inv.valid.items():
        node = rec.node
        names_by_id[pid] = node.name or pid
        if node.type_info and node.type_info.type:
            types_by_id[pid] = str(node.type_info.type)
        else:
            types_by_id[pid] = ""

    scope_ids = [product_id] if product_id and product_id in inv.valid else ids
    sig_types = _collect_signal_types(root, scope_ids)
    find_kinds = _collect_finding_kinds(root, scope_ids)
    temporal_tags = _temporal_signal_tags(root, scope_ids)

    single_product = bool(product_id and product_id in inv.valid)
    product_root_rel = inv.valid[product_id].node.product_root if single_product else ""
    doctrine_rel = _doctrine_repo_path(root, product_root_rel) if single_product else None
    findings_sorted: list[Any] = []
    nm_records: list[SignalRecord] = []
    if single_product:
        fb0 = load_latest_findings(root, product_id or "")
        if fb0 is not None:
            findings_sorted = sorted(fb0.findings, key=lambda f: f.id)
        b0 = load_latest_bundle(root, product_id or "")
        if b0 is not None:
            nm_records = _non_manifest_records(list(b0.records))

    ideas: list[Idea] = []
    seen_titles: set[str] = set()

    def emit(
        title: str,
        description: str,
        channel: str,
        monetization: str,
        rationale: str,
        *,
        pid: str | None,
        pattern: str,
        grounding: dict[str, Any] | None = None,
    ) -> None:
        key = title.strip().lower()[:240]
        if key in seen_titles:
            return
        seen_titles.add(key)
        desc_final = finalize_synthesis_description(
            description,
            grounding,
            pattern=pattern,
            product_label=pname or None,
            product_id=product_id if single_product else None,
            product_root_rel=product_root_rel,
            single_product=single_product,
        )
        idea = Idea(
            idea_id=new_idea_id("idea"),
            title=title[:220],
            description=desc_final[:1200],
            type=IdeaType.INVENT,
            source=IdeaSource.SYNTHESIS,
            novelty_score=0.0,
            adjacency_score=0.0,
            expected_value_score=0.0,
            confidence_score=0.0,
            cost_estimate="medium",
            channel_type=channel,
            monetization_type=monetization,
            rationale=f"{rationale} [synthesis_pattern={pattern}]",
            product_id=pid,
            grounding=grounding,
        )
        apply_scores(idea, seed=seed + key[:80])
        ideas.append(idea)

    pname = _short_name(product_id, names_by_id.get(product_id)) if product_id else ""

    # --- Single-product: findings first (high grounding) ---
    if single_product and findings_sorted:
        n_find = min(len(findings_sorted), 4, max(1, max_ideas // 5))
        for fi in range(n_find):
            if len(ideas) >= max_ideas:
                break
            f = findings_sorted[fi]
            srcs: list[dict[str, Any]] = [
                {"type": "finding", "id": f.id, "kind": f.kind.value},
                {"type": "repo_path", "path": product_root_rel},
                {"type": "artifact", "path": f"runs/findings/latest/{product_id}.json"},
            ]
            if doctrine_rel:
                srcs.append({"type": "doctrine", "path": doctrine_rel})
            g = build_grounding(
                grounding_kind="mixed" if doctrine_rel else "finding",
                grounding_strength="high",
                grounding_sources=srcs,
            )
            title = f"{pname}: respond to finding {f.id[:16]}… ({f.kind.value})"
            desc = (
                f"Grounded in finding `{f.id}` ({f.kind.value}): {_clip(f.title, 140)}. "
                f"Summary: {_clip(f.summary, 220)}. "
                f"Repo: `{product_root_rel}` — see `runs/findings/latest/{product_id}.json` for full text."
            )
            emit(
                title,
                desc,
                _pick(_h(seed, "fg_ch", f.id), CHANNEL_TYPES),
                _pick(_h(seed, "fg_mon", f.id), MONETIZATION_TYPES),
                "Finding-led synthesis for this product (inspectable ids).",
                pid=product_id,
                pattern="finding_grounded",
                grounding=g,
            )

    # --- Portfolio cross-product pairs (skipped in single-product mode) ---
    if not single_product:
        pair_budget = min(max(4, max_ideas // 4), 16)
        pairs = _product_pairs(ids, types_by_id, seed=seed, max_pairs=pair_budget)
        for idx, (ida, idb, reason) in enumerate(pairs):
            if len(ideas) >= max_ideas:
                break
            ha = _h(seed, "pair", ida, idb, str(idx))
            mech = _pick(ha, MECHANICS)
            mon = _pick(ha[8:], MONETIZATION_TYPES)
            ch = _pick(ha[16:], CHANNEL_TYPES)
            dom = _pick(ha[4:], UNEXPECTED_DOMAINS)
            na, nb = _short_name(ida, names_by_id.get(ida)), _short_name(idb, names_by_id.get(idb))
            title = f"{na} × {nb}: {mech} + {dom}"
            desc = (
                f"Cross-pollinate “{na}” with “{nb}” ({reason} pairing). "
                f"Ship a thin slice that combines {mech} with {dom} — monetize via {mon}, "
                f"reach through {ch}. Start with one shared audience hypothesis and one measurable event."
            )
            g = build_grounding(
                grounding_kind="weak",
                grounding_strength="low",
                grounding_sources=[{"type": "portfolio_pair", "product_a": ida, "product_b": idb}],
            )
            emit(
                title,
                desc,
                ch,
                mon,
                "Portfolio pair synthesis; invent-type crossover.",
                pid=product_id or ida,
                pattern="product_pair",
                grounding=g,
            )

    # --- Signal + monetization: concrete records when possible ---
    if single_product and nm_records:
        cap = min(8, max(1, max_ideas // 4), len(nm_records))
        for i, r in enumerate(nm_records[:cap]):
            if len(ideas) >= max_ideas:
                break
            hb = _h(seed, "sigrec", r.id, str(i))
            mon = _pick(hb, MONETIZATION_TYPES)
            ch = _pick(hb[8:], CHANNEL_TYPES)
            mech = _pick(hb[4:], MECHANICS)
            title = f"{pname}: use {r.signal_type.value} signal ({r.id[:12]}) via {r.source}"
            desc = (
                f"Build on collected signal record `{r.id}` (adapter `{r.source}`, type `{r.signal_type.value}`). "
                f"Artifact: `runs/signals/latest/{product_id}.json`. "
                f"Hook: {mech}; first channel {ch}; monetization probe {mon}."
            )
            g = build_grounding(
                grounding_kind="signal_cluster",
                grounding_strength="medium",
                grounding_sources=[
                    {"type": "signal_record", "id": r.id, "signal_type": r.signal_type.value, "adapter_source": r.source},
                    {"type": "artifact", "path": f"runs/signals/latest/{product_id}.json"},
                ],
            )
            emit(
                title,
                desc,
                ch,
                mon,
                "Concrete signal row + monetization (product-scoped).",
                pid=product_id,
                pattern="signal_record_monetization",
                grounding=g,
            )
    else:
        for i, st in enumerate(sig_types[: min(8, max(1, max_ideas // 5))]):
            if len(ideas) >= max_ideas:
                break
            hb = _h(seed, "sigmon", st, str(i))
            mon = _pick(hb, MONETIZATION_TYPES)
            ch = _pick(hb[8:], CHANNEL_TYPES)
            mech = _pick(hb[4:], MECHANICS)
            title = f"{st} signals × {mon}: {mech} offer"
            desc = (
                f"Evidence: signal family name only (`{st}`) — inspect `runs/signals/latest/<product_id>.json` "
                f"for concrete rows. "
                f"Build a wedge where {st} telemetry could trigger a {mon} experience; "
                f"use {mech} as the hook and {ch} as the first distribution surface."
            )
            g = build_grounding(
                grounding_kind="signal_cluster",
                grounding_strength="low",
                grounding_sources=[{"type": "signal_type_only", "value": st}],
            )
            emit(
                title,
                desc,
                ch,
                mon,
                "Signal family + monetization lattice (abstract).",
                pid=product_id,
                pattern="signal_monetization",
                grounding=g,
            )

    # Channel + unexpected domain
    n_cd = min(2, max(1, max_ideas // 6)) if single_product else min(6, max(1, max_ideas // 6))
    for i in range(n_cd):
        if len(ideas) >= max_ideas:
            break
        hc = _h(seed, "chdom", str(i))
        ch = _pick(hc, CHANNEL_TYPES)
        dom = _pick(hc[8:], UNEXPECTED_DOMAINS)
        mon = _pick(hc[16:], MONETIZATION_TYPES)
        title = f"{ch} + {dom}: asymmetric wedge"
        ph = _clip(product_root_rel, 80) if single_product else "portfolio"
        desc = (
            f"Combinatorial hook (weak grounding): distribute through {ch}, borrow loop shape from {dom}. "
            f"Monetization probe: {mon}. "
            + (
                f"Product tree: `{ph}` — no finding or signal row cited for this hook; "
                f"tie to evidence before shipping."
                if single_product
                else "Portfolio-wide brainstorm (no single-product artifact anchor)."
            )
        )
        ch_srcs: list[dict[str, Any]] = [{"type": "combinatorial", "pattern": "channel_domain"}]
        ch_kind = "weak"
        if single_product and product_root_rel:
            ch_srcs.insert(0, {"type": "repo_path", "path": product_root_rel})
            ch_kind = "file_context"
        g = build_grounding(
            grounding_kind=ch_kind,
            grounding_strength="low",
            grounding_sources=ch_srcs,
        )
        emit(
            title,
            desc,
            ch,
            mon,
            "Channel × domain shock synthesis.",
            pid=product_id,
            pattern="channel_domain",
            grounding=g,
        )

    # Finding twist: single-product uses real findings; portfolio keeps alien novelty
    if single_product and findings_sorted and len(ideas) < max_ideas:
        n_tw = min(3, len(findings_sorted))
        for fi in range(n_tw):
            if len(ideas) >= max_ideas:
                break
            f = findings_sorted[fi]
            hd = _h(seed, "findtwist_local", f.id)
            pivot = _pick(hd[8:], MECHANICS)
            ch = _pick(hd[16:], CHANNEL_TYPES)
            mon = _pick(hd[4:], MONETIZATION_TYPES)
            title = f"{pname}: {f.kind.value.replace('_', ' ')} experiment — {pivot}"
            desc = (
                f"Same-product twist on finding `{f.id}`: prototype `{pivot}` for “{pname}” only; "
                f"distribute via {ch}; revenue hypothesis {mon}. "
                f"Does not rely on another product’s surface area."
            )
            g = build_grounding(
                grounding_kind="finding",
                grounding_strength="high",
                grounding_sources=[
                    {"type": "finding", "id": f.id, "kind": f.kind.value},
                    {"type": "product", "product_id": product_id},
                ],
            )
            emit(
                title,
                desc,
                ch,
                mon,
                "Finding theme on current product only.",
                pid=product_id,
                pattern="finding_twist_local",
                grounding=g,
            )
    elif not single_product:
        for k in find_kinds[: min(6, len(find_kinds))]:
            if len(ideas) >= max_ideas:
                break
            if len(ids) < 2:
                break
            hd = _h(seed, "findtwist", k)
            alien = _pick(hd, tuple(ids))
            pivot = _pick(hd[8:], MECHANICS)
            ch = _pick(hd[16:], CHANNEL_TYPES)
            mon = _pick(hd[4:], MONETIZATION_TYPES)
            an = _short_name(alien, names_by_id.get(alien))
            title = f"{k.replace('_', ' ')} stress-test on “{an}”: {pivot}"
            desc = (
                f"Portfolio mash-up: finding theme `{k}` prototyped on `{an}` using {pivot}; "
                f"{ch} / {mon}. (Low product grounding — cross-product novelty.)"
            )
            g = build_grounding(
                grounding_kind="weak",
                grounding_strength="low",
                grounding_sources=[{"type": "finding_kind", "value": k}, {"type": "alien_product", "id": alien}],
            )
            emit(
                title,
                desc,
                ch,
                mon,
                "Finding kind crossed with unrelated product name for novelty.",
                pid=alien,
                pattern="finding_twist",
                grounding=g,
            )

    # Temporal hook bundle (if any)
    if temporal_tags and len(ideas) < max_ideas:
        ht = _h(seed, "temporal", temporal_tags[0])
        mon = _pick(ht, MONETIZATION_TYPES)
        ch = _pick(ht[8:], CHANNEL_TYPES)
        dom = _pick(ht[4:], UNEXPECTED_DOMAINS)
        sig_path = f"runs/signals/latest/{product_id}.json" if single_product else "runs/signals/latest/<product>.json"
        title = f"Live context layer: {dom} on telemetry hooks"
        desc = (
            f"Observed temporal hooks: {', '.join(temporal_tags[:4])}. "
            f"Uses aggregated tags from signals bundles under `runs/signals/latest/`. "
            f"Shape a {dom} experience; monetize with {mon}; first channel {ch}."
        )
        g = build_grounding(
            grounding_kind="signal_cluster",
            grounding_strength="medium",
            grounding_sources=[{"type": "temporal_tags", "tags": temporal_tags[:8]}, {"type": "artifact_hint", "path": sig_path}],
        )
        emit(
            title,
            desc,
            ch,
            mon,
            "Temporal signal tags fused with domain hook.",
            pid=product_id,
            pattern="temporal_domain",
            grounding=g,
        )

    # Pad with combinatorial singles (low grounding) — fewer pads when findings/signals already anchor rows
    pad_limit = 4 if single_product else 12
    if single_product and (findings_sorted or nm_records):
        pad_limit = min(pad_limit, 2)
    pad_i = 0
    while len(ideas) < max_ideas and pad_i < pad_limit:
        hp = _h(seed, "pad", str(pad_i))
        dom_a = _pick(hp, UNEXPECTED_DOMAINS)
        dom_b = _pick(hp[8:], UNEXPECTED_DOMAINS)
        mon = _pick(hp[16:], MONETIZATION_TYPES)
        ch = _pick(hp[4:], CHANNEL_TYPES)
        if dom_a == dom_b:
            pad_i += 1
            continue
        title = f"{dom_a} ⊗ {dom_b}: double domain fuse"
        desc = (
            f"Pure combinatorial pad (low grounding): {dom_a} × {dom_b}; {ch} / {mon}. "
            + (
                f"Product tree: `{product_root_rel}` — not tied to a specific finding or signal id."
                if single_product
                else ""
            )
        )
        fuse_srcs: list[dict[str, Any]] = [{"type": "combinatorial", "pattern": "domain_fuse"}]
        fuse_kind = "weak"
        if single_product and product_root_rel:
            fuse_srcs.insert(0, {"type": "repo_path", "path": product_root_rel})
            fuse_kind = "file_context"
        g = build_grounding(
            grounding_kind=fuse_kind,
            grounding_strength="low",
            grounding_sources=fuse_srcs,
        )
        emit(
            title,
            desc,
            ch,
            mon,
            "Pure combinatorial pad — testable but not artifact-anchored.",
            pid=product_id,
            pattern="domain_fuse",
            grounding=g,
        )
        pad_i += 1

    return ideas[:max_ideas]


__all__ = [
    "MONETIZATION_TYPES",
    "CHANNEL_TYPES",
    "UNEXPECTED_DOMAINS",
    "MECHANICS",
    "synthesize_ideas",
]
