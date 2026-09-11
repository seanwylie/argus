"""
Streamlit operator console — reads ``runs/**/latest.json`` artifacts only (no pipeline execution).

Run::
    uv run --group dashboard streamlit run argus/dashboard/app.py --server.port 8501

Or: ``tools/run_dashboard.sh``

The **Governance & actions** tab surfaces pending approvals, governed execution outcomes, and
thin CLI/orchestration triggers (see ``argus.dashboard.governance_ui``).
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

# Streamlit is an optional dependency (see pyproject dependency-groups `dashboard`).
import streamlit as st
import streamlit.components.v1 as components

from argus.builder.multi_product_view import build_builder_multi_product_view
from argus.builder.status import compute_builder_status, format_builder_operator_summary_human
from argus.core.serialize import dumps_json
from argus.dashboard.console_data import (
    LoadedArtifact,
    artifact_coherence_view,
    autonomous_session_view,
    build_operator_console_snapshot,
    intervention_inbox_view,
    learning_view,
    lifecycle_view,
    load_operator_console_bundle,
    needs_you_view,
    overview_from_narrative,
    overview_from_summary,
    queue_view,
    runner_service_view,
    strategy_view,
    world_context_view,
)
from argus.dashboard.governance_data import scan_pending_approval_rows
from argus.dashboard.governance_ui import render_governance_operator_surface
from argus.dashboard.operator_guidance import (
    GOV_FOCUS_BY_HINT,
    compute_start_here,
    jump_hint_caption,
)
from argus.portfolio.builder_activity import (
    PORTFOLIO_BUILDER_ACTIVITY_SCHEMA,
    build_operator_summary_builder_snapshot,
)
from argus.portfolio.builder_activity_attention import (
    ATTENTION_GROUP_KEYS,
    ATTENTION_GROUP_TITLES,
)

# Caption only; must match ``AUTONOMOUS_PER_CYCLE_TABLE_MAX_ROWS`` in ``console_data.py``.
_DASHBOARD_PER_CYCLE_TABLE_MAX_ROWS = 10

# Clipboard helper embeds base64 in HTML; very large snapshots fall back to download-only.
_CLIPBOARD_SNAPSHOT_MAX_CHARS = 750_000


@st.cache_data(ttl=15)
def _cached_builder_multi_product_view(repo_root_s: str) -> dict:
    """Same truth as ``argus builder portfolio-view``; cached for Overview tab."""
    return build_builder_multi_product_view(Path(repo_root_s))


def _repo_root() -> Path:
    env = os.environ.get("ARGUS_REPO_ROOT", "").strip()
    if env:
        return Path(env).resolve()
    # argus/dashboard/app.py -> repo root is parents[2]
    return Path(__file__).resolve().parents[2]


def _artifact_link(repo: Path, rel: str) -> str:
    return str((repo / rel).resolve())


def _mtime_line(art: LoadedArtifact | None) -> str:
    if not art:
        return "Artifact: — · last updated: —"
    ts = art.mtime_utc or "—"
    status = "OK" if art.exists and art.data is not None else ("parse error" if art.error else "missing")
    return f"Artifact: `{art.rel_path}` · last updated (file mtime, UTC): **{ts}** · status: {status}"


def _substrate_coherence_panel(
    *,
    cv: dict,
    loaded: LoadedArtifact | None,
) -> None:
    """Read-only durable coherence audit (``runs/debug/artifact_coherence/latest.json``)."""
    st.subheader("Artifact coherence (substrate trust)")
    st.caption(_mtime_line(loaded))
    if cv.get("note"):
        st.warning(str(cv["note"]))
        return
    if cv.get("empty") and not cv.get("present"):
        st.info(
            "No durable `runs/debug/artifact_coherence/latest.json`. "
            "Run **`argus portfolio artifact-coherence`** or **`argus portfolio cycle --artifact-coherence`**."
        )
        return
    os = str(cv.get("overall_status") or "")
    ui = str(cv.get("ui_severity") or "")
    if ui == "invalid":
        st.error(f"**invalid** — overall_status `{os}` (treat downstream readings as unreliable).")
    elif ui == "caution":
        st.warning(f"**degraded / warning** — overall_status `{os}` (interpret strategy cautiously).")
    elif ui == "ok":
        st.success(f"**valid** — overall_status `{os}`")
    else:
        st.info(f"overall_status `{os}`")
    st.write("**evaluated_at_utc:**", cv.get("evaluated_at_utc") or "—")
    st.write("**run_id:**", f"`{cv.get('run_id')}`" if cv.get("run_id") else "—")
    summ = str(cv.get("summary") or "").strip()
    if summ:
        st.caption(summ[:900] + ("…" if len(summ) > 900 else ""))
    prev = cv.get("checks_preview") or []
    if prev:
        st.caption("Top failing / warning checks (from durable report)")
        st.dataframe(prev, use_container_width=True, hide_index=True)


def _render_operator_start_here(sh: dict) -> None:
    """Global triage strip — above tabs (artifact-derived only)."""
    st.subheader("Start here")
    st.markdown(f"**{sh.get('headline', '—')}**")
    for line in sh.get("why_lines") or []:
        st.caption(line)
    st.markdown(sh.get("recommended_next") or "—")
    jh = sh.get("jump_hints") or []
    if jh:
        st.caption("**Where to go** (tabs are manual — use Focus buttons where available):")
        for h in jh[:10]:
            st.caption(f"• {jump_hint_caption(str(h))}")
        gov_hints = [h for h in jh if h in GOV_FOCUS_BY_HINT]
        if gov_hints:
            cols = st.columns(min(3, len(set(gov_hints))))
            for i, gh in enumerate(dict.fromkeys(gov_hints)):
                label = {
                    "governance_pending": "Focus Governance: Pending",
                    "governance_triage": "Focus Governance: Triage",
                    "governance_safe_actions": "Focus Governance: Safe actions",
                }.get(str(gh), "Focus Governance")
                with cols[i % len(cols)]:
                    if st.button(label, key=f"sh_focus_{gh}"):
                        st.session_state["gov_highlight"] = GOV_FOCUS_BY_HINT[str(gh)]
        if any(str(x) == "tab_needs_you" for x in jh):
            if st.button("Focus Needs you tab (scroll to actionable)", key="sh_focus_needs"):
                st.session_state["needs_highlight"] = True


def _substrate_coherence_banner(cv: dict) -> None:
    """Compact operational hint for Overview / Needs you (read-only)."""
    if cv.get("empty") and not cv.get("present"):
        st.info("**Substrate coherence:** missing — durable audit not materialized.")
        return
    ui = str(cv.get("ui_severity") or "")
    os = str(cv.get("overall_status") or "")
    if ui == "invalid":
        st.error(f"**Substrate coherence:** invalid (`{os}`) — downstream readings may be unreliable.")
    elif ui == "caution":
        st.warning(f"**Substrate coherence:** degraded (`{os}`) — interpret portfolio signals cautiously.")


def main() -> None:
    st.set_page_config(
        page_title="Argus operator console",
        page_icon="◆",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    repo = _repo_root()
    st.sidebar.title("Argus")
    st.sidebar.caption(f"Repo: `{repo}`")
    wall = st.sidebar.toggle("Wall mode (auto-reload 90s)", value=False)
    if wall:
        st.markdown(
            '<meta http-equiv="refresh" content="90" />',
            unsafe_allow_html=True,
        )
    if st.sidebar.button("Refresh data", type="primary", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.sidebar.subheader("Builder snapshot")
    st.sidebar.caption(
        "Read-only summary from latest invoke/reconcile (same as "
        "`argus builder status <PRODUCT_ID> --brief`)."
    )
    _bpid = st.sidebar.text_input(
        "Product ID",
        value="",
        placeholder="e.g. example-app",
        key="builder_operator_product_id",
    )
    if str(_bpid).strip():
        _bs = compute_builder_status(repo, str(_bpid).strip())
        st.sidebar.text(format_builder_operator_summary_human(_bs))

    @st.cache_data(ttl=15)
    def _bundle(root: str) -> dict:
        return load_operator_console_bundle(Path(root))

    bundle = _bundle(str(repo))

    coh_loaded = bundle.get("artifact_coherence")
    coh_view = artifact_coherence_view(coh_loaded.data if coh_loaded else None)

    snap = build_operator_console_snapshot(repo, bundle)
    snap_json = dumps_json(snap) + "\n"
    with st.sidebar:
        st.subheader("Snapshot (all tabs)")
        st.caption(
            "Single JSON with every console artifact (autonomous, needs-you, overview, queue, "
            "lifecycle, learning, interventions, artifact coherence) plus compact views. Portable to other tools."
        )
        st.download_button(
            label="Download JSON snapshot",
            data=snap_json.encode("utf-8"),
            file_name="argus_operator_console_snapshot.json",
            mime="application/json",
            use_container_width=True,
            key="download_operator_console_snapshot",
        )
        if len(snap_json) <= _CLIPBOARD_SNAPSHOT_MAX_CHARS:
            b64 = base64.b64encode(snap_json.encode("utf-8")).decode("ascii")
            components.html(
                f"""<!DOCTYPE html><html><body style="margin:0;">
<button type="button" style="width:100%;padding:0.45rem 0.5rem;font:inherit;cursor:pointer;border-radius:0.25rem;border:1px solid rgba(49,51,63,0.2);background:rgba(255,255,255,0.08);color:inherit;"
onclick="(function(){{var t=atob('{b64}');navigator.clipboard.writeText(t).then(function(){{var b=document.getElementById('argus-snap-btn');if(b){{b.textContent='Copied';}}}}).catch(function(){{var b=document.getElementById('argus-snap-btn');if(b){{b.textContent='Copy failed — use Download';}}}});}})();"
id="argus-snap-btn">Copy snapshot to clipboard</button>
<p style="font-size:11px;opacity:0.75;margin:0.35rem 0 0 0;">Requires a secure context (HTTPS or localhost). If copy fails, use Download.</p>
</body></html>""",
                height=78,
            )
        else:
            st.caption(
                f"Snapshot is large ({len(snap_json)} chars) — use Download; clipboard helper disabled."
            )

    # Sidebar: artifact health
    st.sidebar.subheader("Artifacts")
    for key, art in bundle.items():
        if art.exists and art.data is not None:
            st.sidebar.success(f"{key}: OK")
        elif art.exists and art.error:
            st.sidebar.warning(f"{key}: parse error")
        else:
            st.sidebar.info(f"{key}: missing")

    summ = bundle.get("operator_summary")
    nar = bundle.get("narrative")
    q = bundle.get("operator_queue")
    life = bundle.get("portfolio_lifecycle")
    strat = bundle.get("portfolio_strategy")
    learn = bundle.get("learning_synthesis")
    inbox = bundle.get("intervention_inbox")
    autonomous = bundle.get("autonomous_runner")
    runner_svc = bundle.get("runner_service")
    esc = bundle.get("escalation_inbox")
    wc = bundle.get("world_context")
    wci = bundle.get("world_context_interpretation")
    wcc = bundle.get("world_context_creation_candidates")

    nv = needs_you_view(esc.data if esc else None)
    av = autonomous_session_view(autonomous.data if autonomous else None)
    rv = runner_service_view(
        runner_svc.data if runner_svc else None,
        artifact_mtime_utc=runner_svc.mtime_utc if runner_svc else None,
    )
    pending_rows = scan_pending_approval_rows(repo)
    ov_sum = overview_from_summary(summ.data if summ and summ.data else None)
    wc_brief = (
        str(ov_sum.get("external_context_situation_brief") or "").strip() or None
        if not ov_sum.get("empty") and bool(ov_sum.get("zero_state"))
        else None
    )
    wc_advisory = (
        str(ov_sum.get("external_context_advisory") or "").strip() or None
        if not ov_sum.get("empty") and bool(ov_sum.get("zero_state"))
        else None
    )
    wc_zero_ctx = wc_brief or wc_advisory
    wc_creation_hint = (
        str(ov_sum.get("zero_state_creation_candidates_summary") or "").strip() or None
        if not ov_sum.get("empty") and bool(ov_sum.get("zero_state"))
        else None
    )
    start_here = compute_start_here(
        escalation_actionable_raw=nv.get("actionable_raw") or [],
        pending_approval_count=len(pending_rows),
        autonomous_primary_status=av.get("primary_status") if not av.get("empty") else None,
        autonomous_stop_reason=str(av.get("stop_reason") or "").strip() or None
        if not av.get("empty")
        else None,
        runner_stale_note=str(rv.get("stale_note") or "").strip() or None if not rv.get("empty") else None,
        operator_headline=str(ov_sum.get("headline_status") or "").strip() or None
        if not ov_sum.get("empty")
        else None,
        operator_next_step=str(ov_sum.get("recommended_next_step") or "").strip() or None
        if not ov_sum.get("empty")
        else None,
        portfolio_zero_state=bool(ov_sum.get("zero_state")),
        zero_state_world_context=wc_zero_ctx,
        zero_state_creation_hint=wc_creation_hint,
    )
    _render_operator_start_here(start_here)
    st.divider()

    tab_gov, tab_auto, tab_need, tab_over, tab_q, tab_life, tab_learn, tab_iv = st.tabs(
        [
            "Governance & actions",
            "Autonomous session",
            "Needs you",
            "Overview",
            "Queue",
            "Lifecycle",
            "Learning",
            "Interventions",
        ]
    )

    with tab_gov:
        st.header("Governance & actions")
        st.caption(
            "Actionable control surface: policy×environment triage, Phase 2 approval responses, "
            "recent subprocess execution artifacts, and safe refresh/advance triggers. "
            "All writes use the same modules as CLI (`record_operator_response`, `advance_orchestration`, …)."
        )
        render_governance_operator_surface(repo)

    with tab_auto:
        st.subheader("Runner service (heartbeat)")
        if rv.get("empty"):
            if runner_svc and runner_svc.exists and runner_svc.data is None and runner_svc.error:
                st.warning(f"Could not parse runner service artifact: {runner_svc.error}")
            elif rv.get("note"):
                st.warning(str(rv.get("note")))
            else:
                st.info(
                    "No `runs/portfolio/runner_service/latest.json` yet. "
                    "Run **`argus portfolio run-service`** (cadence wrapper) to emit a heartbeat."
                )
            st.caption(_mtime_line(runner_svc))
        else:
            rb = rv.get("status_badges") or []
            if rb:
                st.markdown("**Status:** " + " · ".join(f"`{b}`" for b in rb))
            else:
                st.markdown("**Status:** —")
            st.caption(_mtime_line(runner_svc))
            if rv.get("stale_note"):
                st.warning(str(rv.get("stale_note")))

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Loop count", rv.get("loop_count") if rv.get("loop_count") is not None else "—")
            c2.metric(
                "Heartbeat age (s)",
                f"{int(rv['heartbeat_age_seconds'])}"
                if rv.get("heartbeat_age_seconds") is not None
                else "—",
            )
            c3.metric("Cadence interval (s)", rv.get("inputs_interval_seconds"))
            c4.metric("Embedded session JSON", "yes" if rv.get("has_embedded_autonomous_session") else "no")

            st.write("**Service run id:**", f"`{rv.get('service_run_id')}`")
            st.write("**Service started (UTC):**", rv.get("service_started_at_utc") or "—")
            st.write("**Service finished (UTC):**", rv.get("service_finished_at_utc") or "—")
            st.write("**Updated (UTC):**", rv.get("updated_at_utc") or "—")
            st.write("**Last run started (UTC):**", rv.get("last_run_started_at_utc") or "—")
            st.write("**Last run finished (UTC):**", rv.get("last_run_finished_at_utc") or "—")
            st.write("**Last session id:**", f"`{rv.get('last_session_id')}`" if rv.get("last_session_id") else "—")
            st.write("**Last autonomous stop reason:**", f"`{rv.get('last_autonomous_stop_reason')}`" if rv.get("last_autonomous_stop_reason") else "—")

            npr = rv.get("next_planned_run_at_utc")
            if npr:
                st.write("**Next planned run (UTC):**", npr)
            else:
                st.caption("No next run scheduled (one-shot complete, service stopped, or between cadence writes).")

            if str(rv.get("current_status") or "") == "stopped" and rv.get("stop_reason"):
                st.write("**Service stop reason:**", f"`{rv.get('stop_reason')}`")
                src = rv.get("stop_reason_codes") or []
                if src:
                    st.write("**Service stop codes:**", ", ".join(f"`{c}`" for c in src))

            summ_rs = rv.get("last_run_summary") or ""
            st.subheader("Last run summary (service)")
            st.text(summ_rs[:4000] if len(summ_rs) > 4000 else summ_rs or "—")

            if runner_svc and runner_svc.data:
                with st.expander("Raw runner service heartbeat (JSON)"):
                    st.json(runner_svc.data)

        st.divider()
        _substrate_coherence_panel(cv=coh_view, loaded=coh_loaded)
        st.divider()
        st.header("Latest autonomous session")
        if av.get("empty"):
            if autonomous and autonomous.exists and autonomous.data is None and autonomous.error:
                st.warning(f"Could not parse autonomous runner artifact: {autonomous.error}")
            elif av.get("note"):
                st.warning(str(av.get("note")))
            else:
                st.info(
                    "No `runs/portfolio/autonomous_runner/latest.json` yet. "
                    "Run **`argus portfolio run-autonomous`** to produce a bounded session record."
                )
            st.caption(_mtime_line(autonomous))
        else:
            badges = av.get("status_badges") or []
            if badges:
                st.markdown("**Status:** " + " · ".join(f"`{b}`" for b in badges))
            else:
                st.markdown("**Status:** —")
            st.caption(_mtime_line(autonomous))

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Cycles run", av.get("cycles_run") if av.get("cycles_run") is not None else "—")
            c2.metric("Iterations (records)", av.get("per_cycle_count") if av.get("per_cycle_count") is not None else "—")
            c3.metric("Primary outcome", str(av.get("primary_status") or "—").replace("_", " "))
            c4.metric("Dry run", "yes" if (av.get("inputs") or {}).get("dry_run") else "no")

            pcr = av.get("per_cycle_table_rows") or []
            pct_total = av.get("per_cycle_table_total")
            pct_trunc = bool(av.get("per_cycle_table_truncated"))
            pct_mm = int(av.get("per_cycle_table_schema_mismatch_items") or 0)
            st.subheader("Per-cycle progression")
            if pcr:
                cap_parts: list[str] = []
                if pct_total is not None:
                    cap_parts.append(f"{len(pcr)} row(s) shown")
                    if pct_trunc and isinstance(pct_total, int):
                        cap_parts.append(
                            f"of {pct_total} total (oldest cycles omitted; max {_DASHBOARD_PER_CYCLE_TABLE_MAX_ROWS} visible)"
                        )
                if pct_mm:
                    cap_parts.append(f"{pct_mm} non-object entr(y/ies) skipped")
                if cap_parts:
                    st.caption(" · ".join(cap_parts))
                st.dataframe(pcr, use_container_width=True, hide_index=True)
            elif av.get("per_cycle_table_empty"):
                st.caption(
                    "No per-cycle outcome rows on this session payload (empty list or missing "
                    "`per_cycle_outcomes`)."
                )
            with st.expander("Per-cycle outcomes (raw JSON)"):
                raw_pc = (autonomous.data or {}).get("per_cycle_outcomes") if autonomous and autonomous.data else None
                if raw_pc is not None:
                    st.json(raw_pc)
                else:
                    st.caption("—")

            st.subheader("Session")
            st.write("**Session id:**", f"`{av.get('session_id')}`")
            st.write("**Started (UTC):**", av.get("started_at_utc") or "—")
            st.write("**Finished (UTC):**", av.get("finished_at_utc") or "—")
            st.write("**Stop reason:**", f"`{av.get('stop_reason')}`")
            if str(av.get("stop_reason") or "") == "empty_portfolio":
                st.info(
                    "**Zero-state:** refresh completed successfully with an empty inventory — there is nothing "
                    "to cycle yet. Add a validated product under `products/`, run `argus portfolio refresh`, "
                    "then retry autonomous. This is not a generic pipeline failure."
                )
            codes = av.get("stop_reason_codes") or []
            if codes:
                st.write("**Stop codes:**", ", ".join(f"`{c}`" for c in codes))
            summ_text = av.get("session_summary") or ""
            st.subheader("Session summary")
            st.text(summ_text[:6000] if len(summ_text) > 6000 else summ_text or "—")
            if len(summ_text) > 6000:
                st.caption("Summary truncated for display — see raw JSON for full text.")

            st.subheader("Lifecycle-aware context")
            ps = av.get("lifecycle_primary_signal")
            st.write("**Primary signal:**", f"`{ps}`" if ps else "—")
            for note in av.get("lifecycle_session_notes") or []:
                st.write(f"- {note}")
            for h in av.get("lifecycle_priority_hints") or []:
                st.caption(f"Hint: {h}")
            sc = av.get("stop_continue") or {}
            if sc:
                st.write(
                    f"**Stop / continue bias:** `{sc.get('bias')}` — {sc.get('note', '')}"
                )

            st.subheader("Promotions")
            pr = av.get("promotion_recommendations") or []
            if pr:
                for line in pr:
                    st.write(f"- {line}")
            else:
                st.caption("No promotion recommendations on this payload.")
            pex_skip = av.get("promotion_execution_skipped_reason")
            if pex_skip:
                st.caption(f"Promotion execution: {pex_skip}")
            eff_dry = av.get("promotion_effective_dry_run")
            if eff_dry is not None:
                st.caption(f"Promotion effective dry-run: {eff_dry}")

            par = av.get("promotable_actions_rows") or []
            if par:
                st.write("**Promotable actions**")
                st.dataframe(par, use_container_width=True, hide_index=True)
            else:
                st.caption("No promotable actions listed.")

            per = av.get("promotion_execution_rows") or []
            if per:
                st.write("**Promotions executed (steps)**")
                st.dataframe(per, use_container_width=True, hide_index=True)
            else:
                st.caption("No promotion execution steps (detection-only or no matches).")

            bpr = av.get("blocked_promotions_rows") or []
            if bpr:
                st.write("**Blocked promotions**")
                st.dataframe(bpr, use_container_width=True, hide_index=True)
            else:
                st.caption("No blocked promotions.")

            arts = av.get("artifacts_refreshed") or []
            st.subheader("Artifacts refreshed")
            if arts:
                for p in arts:
                    st.write(f"- `{p}`")
            else:
                st.caption("—")

            if autonomous and autonomous.data:
                with st.expander("Raw autonomous session (JSON)"):
                    st.json(autonomous.data)

    with tab_need:
        st.header("Needs you")
        if st.session_state.pop("needs_highlight", False):
            st.success(
                "**Focus:** Actionable items are below — expand cards for steps and routing hints."
            )
        _substrate_coherence_banner(coh_view)
        st.caption(
            "Autonomy-boundary queue — **escalation inbox** only. "
            "For routine intervention rows, see **Interventions**."
        )
        if nv.get("empty"):
            if esc and esc.exists and esc.data is None and esc.error:
                st.warning(f"Could not parse escalation inbox: {esc.error}")
            elif nv.get("note"):
                st.warning(str(nv.get("note")))
            else:
                st.info(
                    "No `runs/portfolio/escalation_inbox/latest.json` yet. "
                    "Run **`argus portfolio escalation-inbox`** to build the autonomy-boundary inbox."
                )
            st.caption(_mtime_line(esc))
        else:
            st.caption(_mtime_line(esc))
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Actionable", nv.get("actionable_count", 0))
            c2.metric("Informational", nv.get("informational_count", 0))
            c3.metric("Snoozed / resolved", nv.get("settled_count", 0))
            c4.metric("Total items", nv.get("total_items", 0))

            st.subheader("Action required")
            if (nv.get("total_items") or 0) == 0:
                st.info(
                    "Escalation inbox has no open items. "
                    "Run **`argus portfolio escalation-inbox`** after portfolio / autonomous activity to refresh."
                )
            elif nv.get("no_actionable_items"):
                st.success("No operator action required — nothing in the escalation inbox is blocking your attention.")
            else:
                ag = nv.get("actionable_guidance") or []
                for i, g in enumerate(ag[:16]):
                    title = (
                        f"{g.get('blocker_label', 'Item')} — {g.get('product_id', '—')} · "
                        f"{g.get('severity', '')}"
                    )
                    with st.expander(title, expanded=(i == 0 and len(ag) <= 3)):
                        st.markdown(str(g.get("plain_explanation") or "—"))
                        ra = g.get("requested_action_short")
                        if ra:
                            st.caption(f"Requested action: {ra}")
                        for stp in g.get("recommended_steps") or []:
                            st.markdown(f"- {stp}")
                        st.caption("**Where in this console:**")
                        for h in g.get("jump_hints") or []:
                            st.caption(f"• {jump_hint_caption(str(h))}")
                        ghints = [x for x in (g.get("jump_hints") or []) if x in GOV_FOCUS_BY_HINT]
                        if ghints:
                            c1, c2, c3 = st.columns(3)
                            for j, gh in enumerate(dict.fromkeys(ghints)):
                                lab = {
                                    "governance_pending": "Focus Governance: Pending",
                                    "governance_triage": "Focus Governance: Triage",
                                    "governance_safe_actions": "Focus Governance: Safe actions",
                                }.get(str(gh), "Focus Governance")
                                col = (c1, c2, c3)[j % 3]
                                with col:
                                    if st.button(
                                        lab,
                                        key=f"ny_{i}_{j}_{gh}",
                                        use_container_width=True,
                                    ):
                                        st.session_state["gov_highlight"] = GOV_FOCUS_BY_HINT[str(gh)]
                ar = nv.get("actionable_rows") or []
                if ar:
                    with st.expander("All actionable rows (table view)", expanded=False):
                        st.dataframe(ar, use_container_width=True, hide_index=True)
                else:
                    st.caption("—")

            cc = nv.get("category_counts_actionable") or {}
            sv = nv.get("severity_counts_actionable") or {}
            if cc or sv:
                a1, a2 = st.columns(2)
                with a1:
                    st.write("**Categories (actionable)**")
                    for k, v in list(cc.items())[:12]:
                        st.write(f"- `{k}`: **{v}**")
                with a2:
                    st.write("**Severity (actionable)**")
                    for k, v in list(sv.items())[:12]:
                        st.write(f"- `{k}`: **{v}**")

            ca_all = nv.get("category_counts_all") or {}
            se_all = nv.get("severity_counts_all") or {}
            if (ca_all or se_all) and (nv.get("actionable_count") or 0) > 0:
                st.caption("All-item category/severity counts include informational and settled rows.")

            with st.expander("Informational (routine context)", expanded=False):
                ir = nv.get("informational_rows") or []
                if ir:
                    st.dataframe(ir, use_container_width=True, hide_index=True)
                else:
                    st.caption("No informational-only rows.")

            with st.expander("Snoozed / resolved", expanded=False):
                sr = nv.get("settled_rows") or []
                if sr:
                    st.dataframe(sr, use_container_width=True, hide_index=True)
                else:
                    st.caption("No snoozed or resolved items.")

            with st.expander("All categories & severities (full inbox)", expanded=False):
                cca, ccs = st.columns(2)
                with cca:
                    for k, v in (ca_all or {}).items():
                        st.write(f"- `{k}`: {v}")
                with ccs:
                    for k, v in (se_all or {}).items():
                        st.write(f"- `{k}`: {v}")

            if esc and esc.data:
                with st.expander("Raw escalation inbox (JSON)"):
                    st.json(esc.data)

    with tab_over:
        st.header("Overview")
        _substrate_coherence_banner(coh_view)

        ov_overview = overview_from_summary(summ.data if summ and summ.data else None)

        st.subheader("Builder (multi-product)")
        _bas_ov: dict = {}
        if isinstance(ov_overview, dict) and not ov_overview.get("empty"):
            _x = ov_overview.get("builder_activity_snapshot")
            _bas_ov = _x if isinstance(_x, dict) else {}
        if not _bas_ov:
            _bas_ov = build_operator_summary_builder_snapshot(repo)
        if isinstance(_bas_ov, dict) and _bas_ov.get("artifact_present"):
            _c1, _c2, _c3 = st.columns(3)
            _c1.metric("Products in rollup", int(_bas_ov.get("product_count") or 0))
            _c2.metric("Attention", int(_bas_ov.get("attention_count") or 0))
            _c3.metric("Routine", int(_bas_ov.get("routine_count") or 0))
            st.caption(str(_bas_ov.get("trust_summary_line") or ""))
            st.write("**Next (Builder):**", str(_bas_ov.get("next_action_hint") or "—"))
            _att = _bas_ov.get("attention_products") or []
            _grp = _bas_ov.get("attention_products_grouped")
            if _att:
                with st.expander("Attention needed (rollup)", expanded=len(_att) <= 6):
                    if isinstance(_grp, dict):
                        _any = False
                        for _gk in ATTENTION_GROUP_KEYS:
                            _rows = _grp.get(_gk) or []
                            if not _rows:
                                continue
                            _any = True
                            st.markdown(f"**{ATTENTION_GROUP_TITLES.get(_gk, _gk)}**")
                            for _a in _rows[:10]:
                                if isinstance(_a, dict):
                                    st.markdown(
                                        f"- **`{_a.get('product_id')}`** — "
                                        f"{', '.join(_a.get('reasons') or [])}"
                                    )
                        if not _any:
                            for _a in _att[:12]:
                                if isinstance(_a, dict):
                                    st.markdown(
                                        f"- **`{_a.get('product_id')}`** — "
                                        f"{', '.join(_a.get('reasons') or [])}"
                                    )
                    else:
                        for _a in _att[:12]:
                            if isinstance(_a, dict):
                                st.markdown(
                                    f"- **`{_a.get('product_id')}`** — "
                                    f"{', '.join(_a.get('reasons') or [])}"
                                )
            else:
                st.caption("No attention flags in portfolio Builder rollup (scope / outcomes / escalation / trust).")
            _rr = _bas_ov.get("recent_runs") or []
            if _rr:
                st.caption("Recent activity (latest timestamps in rollup)")
                st.dataframe(_rr, use_container_width=True, hide_index=True)
        elif isinstance(_bas_ov, dict) and _bas_ov.get("operator_hint"):
            st.info(str(_bas_ov.get("operator_hint")))
        st.caption(
            "Validated inventory products with Builder invoke/reconcile or a Builder escalation packet — "
            "read-only; CLI: `argus builder portfolio-view`."
        )
        mpv = _cached_builder_multi_product_view(str(repo))
        if int(mpv.get("row_count") or 0) == 0:
            st.info(str(mpv.get("empty_message") or "No Builder activity across inventory products."))
        else:
            st.caption(
                f"**{mpv.get('row_count')}** product(s) shown · scanned **{mpv.get('products_scanned')}** "
                f"inventory id(s); **{mpv.get('candidates_considered')}** had invoke/reconcile paths or escalation."
            )
            st.dataframe(
                mpv.get("rows") or [],
                use_container_width=True,
                hide_index=True,
            )

        with st.expander("Portfolio Builder activity (persisted artifact)", expanded=False):
            st.caption(
                "Frozen rollup from `argus portfolio builder-activity` (coordination snapshot only; "
                "not a learning or outcome artifact)."
            )
            _ba_path = repo / "runs" / "portfolio" / "builder_activity" / "latest.json"
            if _ba_path.is_file():
                try:
                    _ba_raw = json.loads(_ba_path.read_text(encoding="utf-8"))
                    if _ba_raw.get("schema") == PORTFOLIO_BUILDER_ACTIVITY_SCHEMA:
                        st.caption(
                            f"run_id `{_ba_raw.get('run_id')}` · generated `{_ba_raw.get('generated_at_utc')}`"
                        )
                        st.dataframe(
                            _ba_raw.get("products") or [],
                            use_container_width=True,
                            hide_index=True,
                        )
                        st.caption(str(_ba_raw.get("disclaimer") or "")[:500])
                    else:
                        st.warning("File exists but schema is not the expected portfolio Builder activity payload.")
                except (OSError, json.JSONDecodeError) as ex:
                    st.warning(str(ex))
            else:
                st.info(
                    "No `runs/portfolio/builder_activity/latest.json` yet — run **`argus portfolio builder-activity`**."
                )

        wcv = world_context_view(
            wc.data if wc and wc.data else None,
            wci.data if wci and wci.data else None,
            wcc.data if wcc and wcc.data else None,
        )
        if not ov_overview.get("empty") and ov_overview.get("zero_state"):
            st.info(
                "**Portfolio zero-state:** there are no validated products under `products/` yet. "
                "This is a normal starting point — follow **Next step** below or **Start here** above; "
                "it is not a failed pipeline."
            )
        if wcv.get("present"):
            with st.expander("World context (advisory external signals)", expanded=False):
                st.caption(str(wcv.get("disclaimer") or ""))
                if wcv.get("interpretation_present") and wcv.get("operator_narrative"):
                    st.markdown("**Interpretation (deterministic, advisory)**")
                    st.write(str(wcv.get("operator_narrative") or "")[:1200])
                    if wcv.get("notable_patterns"):
                        st.caption("Patterns: " + ", ".join(str(p) for p in (wcv.get("notable_patterns") or [])[:12]))
                if wcv.get("creation_candidates_present") and wcv.get("creation_candidates_rows"):
                    ss = str(wcv.get("creation_candidates_situation_summary") or "").strip()
                    if ss:
                        st.markdown("**Situation (brief)**")
                        st.write(ss[:900])
                    pid = str(wcv.get("creation_candidates_primary_id") or "").strip()
                    if pid:
                        st.caption(f"Primary direction (advisory, not a rank): `{pid}`")
                    st.markdown("**Advisory creation directions (hypothesis-level)**")
                    st.caption(str(wcv.get("creation_candidates_disclaimer") or "")[:500])
                    for c in (wcv.get("creation_candidates_rows") or [])[:4]:
                        if not isinstance(c, dict):
                            continue
                        st.write(f"**{c.get('title') or c.get('candidate_id')}** — `{c.get('strength')}`")
                        st.caption(str(c.get("rationale") or "")[:600])
                st.caption("Aggregate headline (raw summary)")
                st.write(str(wcv.get("headline") or "—"))
                spr = wcv.get("signals_preview_rows") or []
                if spr:
                    st.dataframe(spr, use_container_width=True, hide_index=True)
                st.caption(_mtime_line(wc))
                if wci:
                    st.caption(_mtime_line(wci))
        elif not ov_overview.get("empty") and ov_overview.get("zero_state"):
            st.caption(
                "Optional: `argus world-context ingest --file <signals.json>` records advisory external signals "
                "under `runs/world_context/latest.json`."
            )
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Operator summary")
            if summ and summ.data:
                ov = ov_overview
                st.metric("Status", str(ov.get("headline_status") or "—").replace("_", " "))
                st.metric("Confidence", str(ov.get("confidence_level") or "—"))
                st.write("**Next step:**", ov.get("recommended_next_step") or "—")
                st.caption(f"evaluated_at: {ov.get('evaluated_at_utc') or '—'} · run `{ov.get('run_id')}`")
                with st.expander("Raw summary (JSON)"):
                    st.json(summ.data)
            else:
                st.info("No `runs/dashboard/operator_summary/latest.json` yet. Run `argus dashboard summary`.")
            st.caption(_mtime_line(summ))

        with c2:
            st.subheader("Narrative")
            if nar and nar.data:
                nv = overview_from_narrative(nar.data)
                st.write("**Trajectory:**", f"`{nv.get('overall_trajectory')}`")
                st.write(nv.get("narrative_preview") or "—")
                if len(nar.data.get("narrative_text") or "") > 800:
                    st.caption("Preview truncated — see full JSON.")
                st.caption(f"evaluated_at: {nv.get('evaluated_at_utc') or '—'} · run `{nv.get('run_id')}`")
                with st.expander("Raw narrative (JSON)"):
                    st.json(nar.data)
            else:
                st.info("No `runs/dashboard/narrative/latest.json` yet. Run `argus dashboard narrative`.")
            st.caption(_mtime_line(nar))

        if strat and strat.data:
            st.subheader("Strategy (snapshot)")
            sv = strategy_view(strat.data)
            st.write("**Posture:**", f"`{sv.get('strategic_posture')}`")
            rat = strat.data.get("rationale")
            if isinstance(rat, list):
                for line in rat[:6]:
                    st.write(f"- {line}")
            elif rat:
                st.write(rat)
            st.caption(f"evaluated_at: {sv.get('evaluated_at_utc') or '—'}")
        else:
            st.subheader("Strategy")
            st.info("No `runs/portfolio/strategy/latest.json` yet. Run `argus portfolio strategy`.")
        st.caption(_mtime_line(strat))

    with tab_q:
        st.header("Operator queue")
        if q and q.data:
            qv = queue_view(q.data)
            st.caption(
                f"**{qv.get('entry_count', 0)}** entries · generated `{qv.get('generated_at_utc') or '—'}`"
            )
            if qv.get("entries"):
                st.dataframe(qv["entries"], use_container_width=True, hide_index=True)
            else:
                st.warning("Queue payload has no entries.")
            with st.expander("Raw queue (JSON)"):
                st.json(q.data)
        else:
            st.info("No `runs/portfolio/operator_queue/latest.json` yet. Run portfolio refresh / operator queue.")
        _qcap = _mtime_line(q)
        if q:
            _qcap += f" · absolute: `{_artifact_link(repo, q.rel_path)}`"
        st.caption(_qcap)

    with tab_life:
        st.header("Portfolio lifecycle")
        if life and life.data:
            lv = lifecycle_view(life.data)
            st.write("**Strategy posture:**", lv.get("strategy_posture") or "—")
            if lv.get("summary_narrative"):
                st.write(lv["summary_narrative"])
            counts = lv.get("lifecycle_counts") or {}
            if counts:
                cols = st.columns(min(4, len(counts)))
                for i, (k, v) in enumerate(sorted(counts.items(), key=lambda x: x[0])):
                    cols[i % len(cols)].metric(k, v)
            c1, c2 = st.columns(2)
            with c1:
                st.write("**Entering**")
                st.write(", ".join(lv.get("products_entering") or []) or "—")
            with c2:
                st.write("**Exit-oriented**")
                st.write(", ".join(lv.get("products_exiting") or []) or "—")
            st.write("**Repair pressure:**", ", ".join(lv.get("repair_pressure") or []) or "—")
            st.write("**Retirement pressure:**", ", ".join(lv.get("retirement_pressure") or []) or "—")
            st.caption(f"evaluated_at: {lv.get('evaluated_at_utc') or '—'}")
            with st.expander("Raw lifecycle (JSON)"):
                st.json(life.data)
        else:
            st.info("No `runs/portfolio/lifecycle/latest.json` yet. Run `argus portfolio lifecycle`.")
        st.caption(_mtime_line(life))

    with tab_learn:
        st.header("Learning synthesis")
        if learn and learn.data:
            lv = learning_view(learn.data)
            for t in lv.get("top_lessons") or []:
                st.write(f"- {t}")
            sw = lv.get("sparse_warnings") or []
            if sw:
                st.warning("Sparse / conflicting signals:\n" + "\n".join(f"- `{w}`" for w in sw))
            st.caption(f"evaluated_at: {lv.get('evaluated_at_utc') or '—'}")
            with st.expander("Raw learning synthesis (JSON)"):
                st.json(learn.data)
        else:
            st.info("No `runs/policy/learning_synthesis/latest.json` yet. Run `argus policy learning-synthesis` (if CLI exists) or generate that artifact.")
        st.caption(_mtime_line(learn))

    with tab_iv:
        st.header("Intervention inbox")
        if inbox and inbox.data:
            iv = intervention_inbox_view(inbox.data)
            m1, m2, m3 = st.columns(3)
            m1.metric("Open items", iv.get("open_items_count", 0))
            m2.metric("Active in queue", iv.get("active_in_queue", 0))
            m3.metric("High severity (active)", iv.get("high_severity_active", 0))
            prev = iv.get("items_preview") or []
            if prev:
                st.dataframe(prev, use_container_width=True, hide_index=True)
            st.caption(f"built_at: {iv.get('built_at_utc') or '—'} · source intervention `{iv.get('source_intervention_run_id')}`")
            with st.expander("Raw inbox (JSON)"):
                st.json(inbox.data)
        else:
            st.info("No `runs/portfolio/intervention_inbox/latest.json` yet. Run `argus portfolio intervention-inbox`.")
        st.caption(_mtime_line(inbox))

    st.divider()
    st.caption(
        "Artifact-driven UI — refresh the pipeline or use **Refresh data** in the sidebar. "
        "Set `ARGUS_REPO_ROOT` to override repo detection when not running from repo root."
    )


if __name__ == "__main__":
    main()
