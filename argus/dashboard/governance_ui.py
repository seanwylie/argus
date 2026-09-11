"""
Streamlit panels for governance + governed execution (uses :mod:`governance_data` only).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import streamlit as st

from argus.dashboard.governance_data import (
    count_active_grants,
    list_recent_governed_execution_runs,
    run_argus_cli,
    scan_pending_approval_rows,
)
from argus.dashboard.operator_guidance import recommended_product_actions
from argus.orchestrator.advancement import advance_orchestration
from argus.products.inventory import build_inventory
from argus.project_permissions.approvals import record_operator_response
from argus.project_permissions.gate import phase1_summary_for_product


def render_governance_operator_surface(repo: Path) -> None:
    """Main “Governance & actions” tab body."""
    repo = repo.resolve()

    gh = st.session_state.pop("gov_highlight", None)
    if gh == "pending":
        st.success(
            "**Focus:** Pending Phase 2 approvals — respond in the section below (same tab). "
            "Use **Needs you** for autonomy-boundary escalations."
        )
    elif gh == "triage":
        st.info("**Focus:** Product triage — policy × environment table and drill-in below.")
    elif gh == "safe":
        st.info("**Focus:** Safe actions — refresh / orchestration advance at the bottom of this tab.")

    st.subheader("Product triage (policy × environment)")
    st.caption(
        "Uses ``phase1_summary_for_product`` — same policy + environment alignment as CLI. "
        "Does not edit policy from this panel."
    )

    pending_scan = scan_pending_approval_rows(repo)
    pending_by_product = Counter(str(r.get("product_id") or "") for r in pending_scan)

    def _triage_rows_live(root: Path) -> list[dict[str, Any]]:
        inv = build_inventory(root)
        rows: list[dict[str, Any]] = []
        for pid in sorted(inv.valid.keys()):
            summ = phase1_summary_for_product(root, pid)
            mism = summ.get("policy_environment_mismatches") or []
            perr = summ.get("policy_load_error")
            rows.append(
                {
                    "product_id": pid,
                    "lifecycle_stage": getattr(
                        inv.valid[pid].node.lifecycle.stage,
                        "value",
                        str(inv.valid[pid].node.lifecycle.stage),
                    ),
                    "policy_ok": perr is None,
                    "env_mismatch_count": len(mism),
                    "mismatch_keys": ", ".join(str(x.get("permission_key")) for x in mism[:4]) if mism else "",
                    "pending_approvals": pending_by_product.get(pid, 0),
                    "active_grants": count_active_grants(root, pid),
                },
            )
        return rows

    tri = _triage_rows_live(repo)
    if tri:
        st.dataframe(tri, use_container_width=True, hide_index=True)
    else:
        st.info("No valid products in inventory — add ``products/<id>/product.yaml``.")

    pid_detail: str | None = None
    if tri:
        pid_detail = st.selectbox(
            "Drill-in: product",
            options=sorted([r["product_id"] for r in tri]),
            index=0,
            key="gov_product_drill",
        )
    if pid_detail:
        summ = phase1_summary_for_product(repo, pid_detail)
        pend_n = pending_by_product.get(pid_detail, 0)
        grants_n = count_active_grants(repo, pid_detail)
        recs = recommended_product_actions(
            summ,
            pending_count_for_product=pend_n,
            active_grants=grants_n,
        )
        st.write("**Recommended operator actions**")
        for line in recs:
            st.markdown(f"- {line}")

        if summ.get("policy_load_error"):
            st.error(f"Policy load error: {summ.get('policy_load_error')}")
        else:
            with st.expander("Policy snapshot (JSON — collapsed by default)", expanded=False):
                st.json(summ.get("policy") or {})
            with st.expander("Environment alignment (JSON — collapsed by default)", expanded=False):
                align = summ.get("environment_alignment") or {}
                st.json(align if isinstance(align, dict) else {})
            mm = summ.get("policy_environment_mismatches") or []
            if mm:
                st.warning("**Policy vs environment gaps** (yes/confirm but env not ready)")
                st.dataframe(mm, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown('<span id="argus-gov-pending"></span>', unsafe_allow_html=True)
    st.subheader("Pending Phase 2 approvals (artifact inbox)")
    pending = pending_scan
    if not pending:
        st.info("No files under ``runs/policy/pending_approvals/*/`` — nothing awaiting operator response.")
    else:
        st.dataframe(pending, use_container_width=True, hide_index=True)
        st.caption(
            "Responding writes real grants / audit under ``runs/policy/`` (same as "
            "``argus approval respond``)."
        )
        pr = st.selectbox(
            "Select pending request",
            options=range(len(pending)),
            format_func=lambda i: f"{pending[i]['product_id']} — {pending[i]['file_name']}",
            key="gov_pick_pending",
        )
        row = pending[pr]
        body_path = repo / row["rel_path"]
        try:
            raw_body = body_path.read_text(encoding="utf-8")
        except OSError:
            raw_body = ""
        with st.expander("Raw pending JSON"):
            st.code(raw_body[:8000] or "—", language="json")

        resp = st.selectbox(
            "Response",
            options=["confirm_once", "always", "no"],
            key="gov_resp_kind",
        )
        orch_aid = st.text_input(
            "Scoped action id (required for confirm_once — orchestration or subprocess ``action_id``)",
            value=str(row.get("orchestration_action_id") or ""),
            key="gov_orch_aid",
        )
        note = st.text_area("Note (optional)", key="gov_note")
        if st.button("Record response (writes grants / audit)", type="primary", key="gov_submit_resp"):
            field = str(row.get("phase1_policy_field") or "").strip()
            pid = str(row.get("product_id") or "").strip()
            if not field or not pid:
                st.error("Missing product_id or phase1_policy_field on pending artifact.")
            elif resp == "confirm_once" and not str(orch_aid).strip():
                st.error("confirm_once requires a scoped action id.")
            else:
                try:
                    out = record_operator_response(
                        repo,
                        product_id=pid,
                        phase1_policy_field=field,
                        response=resp,  # type: ignore[arg-type]
                        orchestration_action_id=str(orch_aid).strip() if resp == "confirm_once" else None,
                        note=note or None,
                    )
                    st.success(f"Recorded: {out}")
                    st.cache_data.clear()
                    st.rerun()
                except (OSError, ValueError) as e:
                    st.error(str(e))

    st.divider()
    st.subheader("Recent governed execution runs")
    st.caption("From ``runs/execution/*/run.json`` (subprocess + Phase 1 embed when present).")
    lim = st.slider("Max rows", min_value=10, max_value=80, value=35, step=5, key="gov_exec_lim")
    runs = list_recent_governed_execution_runs(repo, limit=int(lim))
    filt = st.multiselect(
        "Filter outcome",
        options=sorted({r.get("outcome") for r in runs if r.get("outcome")}),
        default=[],
        key="gov_run_filt",
    )
    shown = [r for r in runs if not filt or r.get("outcome") in filt]
    if shown:
        st.dataframe(shown, use_container_width=True, hide_index=True)
    else:
        st.caption("No execution runs found yet — run ``argus execution run`` with opt-in flags.")

    st.divider()
    st.subheader("Safe actions (invokes real Argus pathways)")
    st.caption(
        "These call the same entrypoints operators use in a terminal — no bypass of policy. "
        "Prefer **refresh summary** and **orchestration advance (dry)** before anything heavier."
    )
    ac1, ac2, ac3 = st.columns(3)
    with ac1:
        if st.button("Refresh dashboard operator summary", key="gov_act_summary"):
            code, out, err = run_argus_cli(repo, ["dashboard", "summary"])
            if code == 0:
                st.success("Wrote ``runs/dashboard/operator_summary/latest.json`` (and md).")
                st.text((out or "")[:4000])
            else:
                st.error(err or out or f"exit {code}")
    with ac2:
        op_pid = st.text_input("Product id (orchestration)", value=pid_detail or "", key="gov_adv_pid")
        if st.button("Orchestration advance (dry — no step execution)", key="gov_act_adv"):
            if not str(op_pid).strip():
                st.error("Enter a product id.")
            else:
                try:
                    _path, payload = advance_orchestration(repo, str(op_pid).strip(), execute=False)
                    st.success(f"Advancement written: ``{payload.get('selected_action')}``")
                    st.json(payload)
                    st.cache_data.clear()
                except (OSError, ValueError, RuntimeError) as e:
                    st.error(str(e))
    with ac3:
        if st.button("Refresh portfolio operator queue artifact", key="gov_act_queue"):
            code, out, err = run_argus_cli(repo, ["portfolio", "operator-queue"])
            if code == 0:
                st.success("Wrote ``runs/portfolio/operator_queue/latest.{json,md}``.")
                st.text((out or "")[:4000])
            else:
                st.warning("Check ``argus portfolio operator-queue --help`` if this fails.")
                st.text(err or out or f"exit {code}")
