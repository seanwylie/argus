"""Write the static HTML dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.dashboard.data import build_dashboard_payload


def dashboard_html_template() -> str:
    """Single-page HTML with embedded CSS/JS (no external assets)."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Argus portfolio dashboard</title>
  <style>
    :root { font-family: system-ui, sans-serif; background: #1a1d23; color: #e8eaed; }
    body { margin: 0; padding: 1rem 1.25rem; max-width: 1600px; margin-inline: auto; }
    h1 { font-size: 1.25rem; font-weight: 600; }
    h2 { font-size: 1rem; margin-top: 1.5rem; color: #9aa0a6; }
    h3 { font-size: 0.95rem; margin: 0.75rem 0 0.35rem; color: #bdc1c6; }
    .meta { color: #9aa0a6; font-size: 0.85rem; margin-bottom: 1rem; }
    .controls { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; margin: 1rem 0; }
    .controls label { font-size: 0.8rem; color: #9aa0a6; }
    select, input { background: #2d3139; border: 1px solid #444; color: #e8eaed; padding: 0.35rem 0.5rem; border-radius: 4px; }
    .wrap-table { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
    th, td { text-align: left; padding: 0.4rem 0.45rem; border-bottom: 1px solid #333; vertical-align: top; }
    th { color: #9aa0a6; font-weight: 500; cursor: pointer; user-select: none; white-space: nowrap; }
    th:hover { color: #bdc1c6; }
    tr:hover td { background: #252830; }
    tr.selected td { background: #2a3344; }
    .num { text-align: right; font-variant-numeric: tabular-nums; }
    .tag { display: inline-block; background: #333; padding: 0.1rem 0.35rem; border-radius: 3px; font-size: 0.7rem; margin: 0.1rem; }
    .tag.improving { background: #174ea6; }
    .tag.risk { background: #8b1d10; }
    .tag.warn { background: #5c4a00; }
    .warn { color: #f9ab00; }
    .detail { margin-top: 1.5rem; padding: 1rem; background: #252830; border-radius: 8px; border: 1px solid #333; }
    .detail pre { overflow: auto; font-size: 0.75rem; background: #1a1d23; padding: 0.75rem; border-radius: 4px; }
    .links a { color: #8ab4f8; margin-right: 0.75rem; font-size: 0.82rem; }
    ul { margin: 0.25rem 0; padding-left: 1.2rem; }
    .empty { color: #9aa0a6; font-style: italic; }
    .spark-wrap { display: inline-block; vertical-align: middle; }
    svg.spark { display: block; }
    .mini { font-size: 0.72rem; color: #9aa0a6; }
    .hist-table { width: 100%; font-size: 0.72rem; border-collapse: collapse; }
    .hist-table th, .hist-table td { border: 1px solid #444; padding: 0.2rem 0.35rem; }
    .actions-panel { margin-top: 1.5rem; }
    .actions-panel h2 { margin-bottom: 0.35rem; }
    .actions-panel h3 { font-size: 0.88rem; margin: 1rem 0 0.4rem; color: #bdc1c6; }
    .risk-high { color: #f28b82; font-weight: 600; }
    .risk-medium { color: #fdd663; }
    .risk-low { color: #81c995; }
    .appr-needs { color: #f9ab00; }
    .appr-pending { color: #8ab4f8; }
    .appr-approved { color: #81c995; }
    .appr-auto { color: #c58af9; }
    .appr-rejected { color: #f28b82; }
    .cmd { font-family: ui-monospace, monospace; font-size: 0.72rem; word-break: break-all; }
    .diag-strip { margin: 0.75rem 0 1rem; padding: 0.75rem 1rem; border-radius: 8px; border: 1px solid #444; background: #252830; }
    .diag-strip h2 { margin: 0 0 0.5rem; font-size: 0.95rem; color: #f9ab00; }
    .diag-strip.strict-fail h2 { color: #f28b82; }
    .diag-strip ul { margin: 0.25rem 0; font-size: 0.8rem; }
    .diag-strip li.warn { color: #fdd663; }
    .diag-strip li.err { color: #f28b82; }
    .diag-strip li.info { color: #9aa0a6; }
    .integrity-row { display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; margin-top: 0.5rem; font-size: 0.78rem; color: #9aa0a6; }
    .badge { display: inline-block; padding: 0.15rem 0.45rem; border-radius: 4px; background: #333; }
    .badge.bad { background: #5c2018; color: #fad2cf; }
    .badge.ok { background: #1e3a2f; color: #ceead6; }
    .fatal-banner { background: #5c2018; color: #fff; padding: 1rem; border-radius: 8px; margin-bottom: 1rem; }
    .tag.t-ok { background: #1e3a2f; color: #ceead6; }
    .tag.t-warn { background: #5c4a00; color: #fdd663; }
    .tag.t-bad { background: #5c2018; color: #fad2cf; }
    .tag.t-muted { background: #3c4043; color: #9aa0a6; }
    .operator-alerts { margin: 0.75rem 0; padding: 0.75rem 1rem; border-radius: 8px; border: 1px solid #5c4a00; background: #2a2618; }
    .operator-alerts h2 { margin: 0 0 0.5rem; font-size: 0.95rem; color: #fdd663; }
    .operator-alerts ul { margin: 0.25rem 0; padding-left: 1.2rem; }
    .operator-alerts li.warn { color: #fdd663; }
    .operator-alerts li.info { color: #9aa0a6; }
    .bar-track { display: inline-block; width: 52px; height: 8px; background: #333; border-radius: 4px; vertical-align: middle; margin-right: 0.35rem; }
    .bar-fill { display: block; height: 100%; border-radius: 4px; background: #8ab4f8; max-width: 100%; }
    .bar-fill.warn { background: #f9ab00; }
    .bar-fill.bad { background: #f28b82; }
    .ov-mini { font-size: 0.68rem; color: #9aa0a6; white-space: nowrap; }
    .ideas-panel .idea-invent { border-left: 3px solid #a855f7; padding-left: 0.5rem; }
    .ideas-panel .idea-reject { opacity: 0.85; color: #9aa0a6; }
    .ideas-panel .idea-hrr { border-left: 3px solid #f28b82; padding-left: 0.5rem; }
    .ideas-panel table.ideas-mini { font-size: 0.78rem; width: 100%; }
    .ideas-panel table.ideas-mini td { vertical-align: top; }
    .tag.idea-inv { background: #4a1d6e; color: #e9d5ff; }
    .tag.idea-rr { background: #5c2018; color: #fad2cf; }
    .tag.idea-dup { background: #3c4043; color: #bdc1c6; }
    .refine-details { margin: 0.35rem 0 0.75rem 0; padding: 0.5rem 0.75rem; border-radius: 6px; border: 1px solid #333; background: #1e2128; }
    .refine-details summary { cursor: pointer; color: #8ab4f8; font-size: 0.78rem; user-select: none; }
    .refine-details pre { margin: 0.35rem 0 0; font-size: 0.72rem; max-height: 16rem; overflow: auto; white-space: pre-wrap; word-break: break-word; }
    .refine-chain { font-size: 0.72rem; color: #9aa0a6; margin: 0.25rem 0 0.35rem; }
    .refine-warn { color: #fdd663; font-size: 0.72rem; }
    .refine-err { color: #f28b82; font-size: 0.72rem; }
  </style>
</head>
<body>
  <h1>Argus portfolio</h1>
  <div id="fatalPayload" class="fatal-banner" style="display:none;"></div>
  <div id="operatorAlerts" class="operator-alerts" style="display:none;"></div>
  <div id="diagRoot" class="diag-strip" style="display:none;"><h2>Build diagnostics</h2><div id="diagBody"></div></div>
  <p class="meta" id="meta"></p>
  <div class="detail actions-panel" id="orchPortfolioPriRoot" style="display:none">
    <h2>Orchestration — portfolio priorities</h2>
    <p class="mini" id="orchPriIntro"></p>
    <div id="orchPriBody"><p class="empty">Loading…</p></div>
  </div>
  <div class="detail actions-panel" id="orchPortfolioTrendsRoot" style="display:none">
    <h2>Orchestration — portfolio priority trends</h2>
    <p class="mini" id="orchTrendsIntro"></p>
    <div id="orchTrendsBody"><p class="empty">Loading…</p></div>
  </div>
  <div class="detail actions-panel" id="temporalPanelRoot">
    <h2>Temporal — pipeline freshness</h2>
    <p class="mini" id="temporalIntro"></p>
    <div id="temporalPanelBody"><p class="empty">Loading…</p></div>
  </div>
  <div class="detail actions-panel ideas-panel" id="ideasPanelRoot">
    <h2>Ideas — generation engine</h2>
    <p class="mini" id="ideasIntro"></p>
    <div id="ideasPanelBody"><p class="empty">Loading…</p></div>
  </div>
  <div class="detail actions-panel" id="refinementPanelRoot">
    <h2>Refinement — artifact review loops</h2>
    <p class="mini" id="refinementIntro"></p>
    <div id="refinementPanelBody"><p class="empty">Loading…</p></div>
  </div>
  <div class="controls">
    <label>History window <select id="histWin">
      <option value="3">Latest 3 snapshots</option>
      <option value="5">Latest 5 snapshots</option>
      <option value="all" selected>All snapshots</option>
    </select></label>
    <label>State <select id="fState"><option value="">(all)</option></select></label>
    <label>Status <select id="fStatus"><option value="">(all)</option></select></label>
    <label>Cost ≤ <input type="number" id="fCost" step="any" placeholder="max USD"/></label>
    <label>Severity min <select id="fSev">
      <option value="">(any)</option>
      <option value="critical">critical</option>
      <option value="high">high+</option>
      <option value="medium">medium+</option>
      <option value="low">low+</option>
      <option value="info">info+</option>
    </select></label>
    <label>Action / intent <input type="text" id="fAction" placeholder="substring"/></label>
    <label>Sort by <select id="sortKey">
      <option value="priority_score">priority_score</option>
      <option value="monthly_cost_usd">monthly_cost_usd</option>
      <option value="lifecycle_stage">lifecycle_stage</option>
      <option value="active_findings_count">active_findings_count</option>
      <option value="temporal_overall">temporal status</option>
      <option value="temporal_findings_count">temporal_findings_count</option>
      <option value="ideas_count">ideas_count</option>
      <option value="ideas_invent_count">ideas_invent</option>
      <option value="ov_uncertainty">uncertainty</option>
      <option value="ov_risk">risk</option>
      <option value="ov_esc_pressure">escalation pressure</option>
      <option value="ov_exploratory">exploratory</option>
      <option value="product_id">product_id</option>
      <option value="delta_findings">Δ findings</option>
      <option value="delta_cost">Δ cost</option>
    </select></label>
    <label><input type="checkbox" id="sortDir"/> desc</label>
  </div>
  <div class="wrap-table">
  <table>
    <thead>
      <tr>
        <th data-k="product_id">product_id</th>
        <th data-k="type">type</th>
        <th data-k="state">state</th>
        <th data-k="status">status</th>
        <th data-k="monthly_cost_usd" class="num">cost</th>
        <th data-k="last_signal_at">last_signal</th>
        <th data-k="temporal_overall">temporal</th>
        <th data-k="temporal_freshness">time context</th>
        <th data-k="active_findings_count" class="num">findings</th>
        <th data-k="temporal_findings_count" class="num">temp. findings</th>
        <th data-k="ideas_count" class="num">ideas</th>
        <th data-k="ideas_invent_count" class="num">inv</th>
        <th data-k="delta_findings" class="num">Δ find</th>
        <th data-k="delta_cost" class="num">Δ $</th>
        <th data-k="delta_priority" class="num">Δ pri</th>
        <th data-k="delta_esc" class="num">Δ esc</th>
        <th data-k="hist_n" class="num">#hist</th>
        <th data-k="trend_flags">trend / drift</th>
        <th data-k="top_recommended_action">top_action</th>
        <th data-k="priority_score" class="num">priority</th>
        <th data-k="top_confidence" class="num">conf</th>
        <th data-k="ov_uncertainty" class="num">unc</th>
        <th data-k="ov_risk" class="num">risk</th>
        <th data-k="ov_esc_pressure" class="num">esc</th>
        <th data-k="ov_exploratory">exploratory</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
  </div>
  <div class="detail actions-panel" id="lastLoopPanelRoot">
    <h2>Last <code>loop full</code> run</h2>
    <p class="mini" id="lastLoopIntro"></p>
    <div id="lastLoopPanelBody"><p class="empty">Loading…</p></div>
  </div>
  <div class="detail actions-panel" id="autonomyPanelRoot">
    <h2>Autonomy &amp; execution safety</h2>
    <p class="mini" id="autonomyIntro"></p>
    <div id="autonomyPanelBody"><p class="empty">Loading…</p></div>
  </div>
  <div class="detail actions-panel" id="economicsPanelRoot">
    <h2>Economics — cost resources</h2>
    <p class="mini" id="economicsIntro"></p>
    <div id="economicsPanelBody"><p class="empty">Loading…</p></div>
  </div>
  <div class="detail actions-panel" id="actionsPanelRoot">
    <h2>Actions — execution preview</h2>
    <p class="mini" id="actionsIntro"></p>
    <div id="actionsPanelBody"><p class="empty">Loading…</p></div>
  </div>
  <h2>Product detail</h2>
  <div id="detail" class="detail"><p class="empty">Select a row in the table.</p></div>
  <script id="payload" type="application/json"></script>
  <script>
  const ORDER_SEV = { info: 0, low: 1, medium: 2, high: 3, critical: 4 };
  function sevRank(s) { return ORDER_SEV[s] != null ? ORDER_SEV[s] : -1; }
  function maxSevPresent(fbs) {
    let m = -1;
    for (const k of Object.keys(fbs || {})) {
      if ((fbs[k] || 0) > 0) m = Math.max(m, sevRank(k));
    }
    return m;
  }
  function passesSevFilter(fbs, minSev) {
    if (!minSev) return true;
    const mx = maxSevPresent(fbs);
    if (minSev === 'high') return mx >= sevRank('high');
    return mx >= sevRank(minSev);
  }
  let DATA = {};
  function renderDiagnosticsStrip() {
    const root = document.getElementById('diagRoot');
    const body = document.getElementById('diagBody');
    if (!root || !body) return;
    if (!DATA.diagnostics) {
      root.style.display = 'none';
      return;
    }
    const d = DATA.diagnostics || {};
    const warns = d.warnings || [];
    const errs = d.errors || [];
    const info = d.info || [];
    const integ = DATA.integrity || {};
    const hs = integ.history_snapshots || {};
    const show = warns.length || errs.length || info.length || DATA.strict ||
      (hs.skipped_invalid > 0) || integ.has_diagnostics_errors === true;
    if (!show) {
      root.style.display = 'none';
      return;
    }
    root.style.display = 'block';
    if (DATA.strict && errs.length) root.classList.add('strict-fail');
    let html = '';
    if (errs.length) {
      html += '<p class="warn" style="margin:0 0 0.5rem"><strong>Strict issues</strong> (' + errs.length + ')</p><ul>';
      errs.forEach(x => {
        html += '<li class="err">' + esc(x.message || x.code || '') + '</li>';
      });
      html += '</ul>';
    }
    if (warns.length) {
      html += '<p class="warn" style="margin:0 0 0.5rem"><strong>Warnings</strong> (' + warns.length + ')</p><ul>';
      warns.forEach(x => {
        html += '<li class="warn">' + esc(x.message || x.code || '') + '</li>';
      });
      html += '</ul>';
    }
    if (info.length) {
      html += '<p class="mini" style="margin:0.35rem 0 0.25rem">Info</p><ul>';
      info.forEach(x => {
        html += '<li class="info">' + esc(x.message || '') + '</li>';
      });
      html += '</ul>';
    }
    html += '<div class="integrity-row">';
    html += '<span class="badge ' + (hs.skipped_invalid > 0 ? 'bad' : 'ok') + '">history snapshots: ' +
      esc(String(hs.loaded || 0)) + ' loaded';
    if (hs.skipped_invalid > 0) html += ', ' + esc(String(hs.skipped_invalid)) + ' skipped (invalid JSON)';
    html += '</span>';
    if (integ.inventory) {
      html += '<span class="badge">inventory valid: ' + esc(String(integ.inventory.valid)) +
        ' · invalid dirs: ' + esc(String(integ.inventory.invalid)) + '</span>';
    }
    if (DATA.strict) html += '<span class="badge bad">strict mode</span>';
    html += '</div>';
    body.innerHTML = html;
  }
  function renderOperatorAlerts() {
    const el = document.getElementById('operatorAlerts');
    if (!el) return;
    const alerts = DATA.operator_alerts || [];
    if (!alerts.length) {
      el.style.display = 'none';
      el.innerHTML = '';
      return;
    }
    el.style.display = 'block';
    el.innerHTML = '<h2>Operator alerts</h2><ul>' + alerts.map(a => {
      const lv = (a.level || 'info') === 'warn' ? 'warn' : 'info';
      return '<li class="' + lv + '"><strong>' + esc(a.product_id || '') + '</strong> — ' + esc(a.message || '') +
        (a.code ? ' <span class="ov-mini">(' + esc(a.code) + ')</span>' : '') + '</li>';
    }).join('') + '</ul>';
  }
  function renderOrchestrationPortfolioPriorities() {
    const root = document.getElementById('orchPortfolioPriRoot');
    const intro = document.getElementById('orchPriIntro');
    const body = document.getElementById('orchPriBody');
    if (!root || !body) return;
    const opp = DATA.orchestration_portfolio_priorities || {};
    root.style.display = 'block';
    if (opp.load_error) {
      intro.textContent = 'Artifact issue (see build diagnostics if strict).';
      body.innerHTML = '<p class="warn">' + esc(String(opp.load_error)) + '</p>' +
        '<p class="mini">Path: <code class="cmd">' + esc(opp.artifact_path_repo || '') + '</code></p>';
      return;
    }
    if (!opp.present) {
      intro.textContent = 'No portfolio_priorities.json yet — run `argus orchestration portfolio-priorities --all` after orchestration state.';
      body.innerHTML = '<p class="empty">Not generated.</p>';
      return;
    }
    const gen = opp.generated_at_utc || '—';
    intro.textContent = 'Generated ' + esc(String(gen)) +
      ' · recommended: <strong>' + esc(String(opp.recommended_product_id || '—')) + '</strong>' +
      ' → next_action <code class="cmd">' + esc(String(opp.recommended_next_action != null ? opp.recommended_next_action : 'none')) + '</code>';
    const r1r = opp.rank_1_priority_reasons || [];
    let why = '';
    if (r1r.length) {
      why = '<p class="mini"><strong>Why rank 1</strong></p><ul>' +
        r1r.map(r => '<li>' + esc(String(r)) + '</li>').join('') + '</ul>';
    }
    const rs = opp.rank_1_evidence_summary ? '<p class="mini">' + esc(String(opp.rank_1_evidence_summary)) + '</p>' : '';
    const rows = opp.top_products || [];
    let table = '<table><thead><tr><th>rank</th><th>product</th><th>score</th><th>orch status</th><th>next_action</th><th>strategy</th><th>planning</th><th>freshness (plain)</th><th>reasons</th></tr></thead><tbody>';
    rows.forEach(r => {
      const pr = (r.priority_reasons || []).slice(0, 4).join('; ');
      const fel = r.freshness_explanation_lines || [];
      const felCell = fel.length
        ? '<ul class="mini" style="margin:0;padding-left:1rem;max-width:28rem">' +
          fel.map(function (x) { return '<li>' + esc(String(x)) + '</li>'; }).join('') + '</ul>'
        : '<span class="mini">—</span>';
      table += '<tr><td class="num">' + esc(String(r.rank != null ? r.rank : '')) + '</td>' +
        '<td>' + esc(String(r.product_id || '')) + '</td>' +
        '<td class="num">' + esc(String(r.priority_score != null ? r.priority_score : '—')) + '</td>' +
        '<td>' + esc(String(r.orchestration_status || '—')) + '</td>' +
        '<td><code class="cmd">' + esc(String(r.next_action || '—')) + '</code></td>' +
        '<td>' + esc(String(r.strategy_posture != null ? r.strategy_posture : '—')) + '</td>' +
        '<td>' + esc(String(r.planning_mode != null ? r.planning_mode : '—')) + '</td>' +
        '<td class="mini">' + felCell + '</td>' +
        '<td class="mini">' + esc(pr || '—') + '</td></tr>';
    });
    table += '</tbody></table>';
    body.innerHTML = why + rs + '<div class="wrap-table">' + table + '</div>' +
      '<p class="mini">Source: <code class="cmd">' + esc(opp.artifact_path_repo || '') + '</code></p>';
  }
  function renderOrchestrationPortfolioTrends() {
    const root = document.getElementById('orchPortfolioTrendsRoot');
    const intro = document.getElementById('orchTrendsIntro');
    const body = document.getElementById('orchTrendsBody');
    if (!root || !body) return;
    const o = DATA.orchestration_portfolio_priority_trends || {};
    root.style.display = 'block';
    if (o.load_error) {
      intro.textContent = 'Trends artifact issue (see diagnostics if strict).';
      body.innerHTML = '<p class="warn">' + esc(String(o.load_error)) + '</p>' +
        '<p class="mini">Path: <code class="cmd">' + esc(o.artifact_path_repo || '') + '</code></p>';
      return;
    }
    if (!o.present) {
      intro.textContent = 'No portfolio_priority_trends.json yet — produced when portfolio priorities are written (or run `argus orchestration portfolio-priority-trends`).';
      body.innerHTML = '<p class="empty">Not generated.</p>';
      return;
    }
    const stab = o.portfolio_stability != null ? String(o.portfolio_stability) : '—';
    const sc = o.portfolio_stability_score != null ? String(o.portfolio_stability_score) : '—';
    intro.textContent = 'Generated ' + esc(String(o.generated_at_utc || '—')) +
      ' · window ' + esc(String(o.window_size != null ? o.window_size : '—')) +
      ' · generations ' + esc(String(o.generations_considered != null ? o.generations_considered : '—')) +
      ' · stability: <strong>' + esc(stab) + '</strong> (score ' + esc(sc) + ')';
    let html = '';
    if (o.churn_summary) {
      html += '<p class="mini">' + esc(String(o.churn_summary)) + '</p>';
    }
    const recs = o.operator_recommendations || [];
    if (recs.length) {
      html += '<p class="mini"><strong>Guidance</strong></p><ul>' +
        recs.map(function (r) { return '<li>' + esc(String(r)) + '</li>'; }).join('') + '</ul>';
    }
    const inspect = o.top_products_to_inspect || [];
    if (inspect.length) {
      html += '<p class="mini"><strong>Top inspect</strong>: <code class="cmd">' +
        inspect.map(function (x) { return esc(String(x)); }).join('</code>, <code class="cmd">') +
        '</code></p>';
    }
    const rows = o.top_trending_products || [];
    html += '<div class="wrap-table"><table><thead><tr><th>product</th><th>latest</th><th>avg</th><th>#1×</th><th>flags</th><th>summary</th></tr></thead><tbody>';
    rows.forEach(function (r) {
      const fl = [];
      if (r.rising) fl.push('rising');
      if (r.falling) fl.push('falling');
      if (r.stable) fl.push('stable');
      html += '<tr><td>' + esc(String(r.product_id || '')) + '</td>' +
        '<td class="num">' + esc(String(r.latest_rank != null ? r.latest_rank : '—')) + '</td>' +
        '<td class="num">' + esc(String(r.average_rank != null ? r.average_rank : '—')) + '</td>' +
        '<td class="num">' + esc(String(r.times_ranked_first != null ? r.times_ranked_first : '—')) + '</td>' +
        '<td class="mini">' + esc(fl.join(', ') || '—') + '</td>' +
        '<td class="mini">' + esc(String(r.trend_summary || '—')) + '</td></tr>';
    });
    html += '</tbody></table></div>';
    html += '<p class="mini">Source: <code class="cmd">' + esc(o.artifact_path_repo || '') + '</code></p>';
    body.innerHTML = html;
  }
  function histWindow() {
    const v = document.getElementById('histWin').value;
    if (v === 'all') return 'all';
    return parseInt(v, 10);
  }
  function sliceHistory(points) {
    const w = histWindow();
    if (!points || !points.length) return [];
    if (w === 'all') return points.slice();
    return points.slice(-w);
  }
  function deltaNum(points, key) {
    const s = sliceHistory(points);
    if (s.length < 2) return null;
    const a = s[0][key], b = s[s.length - 1][key];
    if (a == null || b == null) return null;
    return Number(b) - Number(a);
  }
  function fmtDelta(d) {
    if (d == null || isNaN(d)) return '—';
    if (Math.abs(d) < 1e-9) return '0';
    const r = Math.abs(d) >= 100 ? d.toFixed(0) : (Math.abs(d) >= 10 ? d.toFixed(1) : d.toFixed(2));
    return (d > 0 ? '+' : '') + r;
  }
  function augmentRow(p) {
    const pts = p.history_points || [];
    const df = deltaNum(pts, 'active_findings_count');
    const dc = deltaNum(pts, 'monthly_cost_usd');
    const dp = deltaNum(pts, 'priority_score');
    const de = deltaNum(pts, 'escalation_count');
    const tv = p.temporal_visibility || {};
    const tb = tv.temporal_bundle || {};
    const ov = p.operator_visibility || {};
    return Object.assign({}, p, {
      delta_findings: df,
      delta_cost: dc,
      delta_priority: dp,
      delta_esc: de,
      hist_n: pts.length,
      trend_flags: (p.trend_summary && p.trend_summary.trend_flags) ? p.trend_summary.trend_flags.join(',') : '',
      temporal_overall: tv.overall != null ? tv.overall : 'unknown',
      temporal_freshness: tb.worst_freshness_bucket || '—',
      ov_uncertainty: ov.uncertainty != null ? ov.uncertainty : null,
      ov_risk: ov.risk_score != null ? ov.risk_score : null,
      ov_esc_pressure: ov.escalation_pressure != null ? ov.escalation_pressure : null,
      ov_exploratory: ov.exploratory ? 1 : 0,
      ideas_count: p.ideas_count != null ? p.ideas_count : 0,
      ideas_invent_count: p.ideas_invent_count != null ? p.ideas_invent_count : 0,
    });
  }
  function miniBarScore(v, kind) {
    if (v == null || isNaN(v)) return '<span class="ov-mini">—</span>';
    const pct = Math.round(Number(v) * 100);
    let cls = '';
    if (kind === 'unc') {
      if (pct >= 65) cls = ' bad';
      else if (pct >= 40) cls = ' warn';
    } else {
      if (pct >= 70) cls = ' bad';
      else if (pct >= 45) cls = ' warn';
    }
    return '<span class="bar-track"><span class="bar-fill' + cls + '" style="width:' + pct + '%"></span></span>' +
      '<span class="ov-mini">' + pct + '%</span>';
  }
  function exploratoryCell(isEx, risk) {
    if (!isEx) return '<span class="tag t-muted">—</span>';
    const r = risk != null ? Number(risk) : 0;
    const cls = r >= 0.5 ? 't-warn' : 't-muted';
    return '<span class="tag ' + cls + '">yes</span>';
  }
  function temporalStatusTag(overall) {
    const o = overall || 'unknown';
    let cls = 't-muted';
    if (o === 'current') cls = 't-ok';
    else if (o === 'stale') cls = 't-warn';
    else if (o === 'missing' || o === 'unknown') cls = 't-bad';
    else if (o === 'not_required') cls = 't-muted';
    return '<span class="tag ' + cls + '">' + esc(o) + '</span>';
  }
  function loadData() {
    const el = document.getElementById('payload');
    const fatal = document.getElementById('fatalPayload');
    try {
      DATA = JSON.parse(el.textContent);
    } catch (e) {
      if (fatal) {
        fatal.style.display = 'block';
        fatal.textContent = 'Embedded dashboard JSON could not be parsed (generator or template bug): ' + e;
      }
      return;
    }
    if (fatal) fatal.style.display = 'none';
    const hc = (DATA.history && DATA.history.snapshots_count) || 0;
    let metaLine =
      'Generated ' + (DATA.generated_at_utc || '?') +
      ' · valid products: ' + (DATA.inventory && DATA.inventory.valid_count) +
      ' · history snapshots: ' + hc +
      ' · repo: ' + (DATA.repo_root || '');
    const ap0 = DATA.actions_panel;
    if (ap0 && ap0.proposed) {
      metaLine += ' · proposed actions: ' + ap0.proposed.length;
    }
    const er0 = DATA.economics_resources;
    if (er0 && er0.present && er0.total_orphan_cost_usd != null) {
      metaLine += ' · economics orphan cost: $' + Number(er0.total_orphan_cost_usd).toFixed(2) + '/mo';
    }
    const llr = DATA.last_loop_run;
    if (llr && llr.present && llr.run_id) {
      metaLine += ' · last loop full: ' + esc(String(llr.run_id)) +
        (llr.ok ? ' ok' : ' FAIL');
    }
    const asM = DATA.autonomy_safety;
    if (asM && asM.autonomy_mode) {
      metaLine += ' · autonomy: ' + asM.autonomy_mode;
    }
    const ttot = DATA.temporal_findings_portfolio_total;
    if (ttot != null && ttot > 0) {
      metaLine += ' · temporal findings: ' + ttot;
    }
    const tm = DATA.temporal;
    if (tm && tm.last_signal_collection_max_utc) {
      metaLine += ' · last signal collection (max): ' + tm.last_signal_collection_max_utc;
    } else if (tm) {
      metaLine += ' · temporal: no signal collection timestamps yet';
    }
    const opp = DATA.orchestration_portfolio_priorities;
    if (opp && opp.present && opp.recommended_product_id) {
      metaLine += ' · orch priority: ' + esc(String(opp.recommended_product_id)) +
        ' → ' + esc(String(opp.recommended_next_action != null ? opp.recommended_next_action : 'none'));
    }
    const opt = DATA.orchestration_portfolio_priority_trends;
    if (opt && opt.present && opt.portfolio_stability) {
      metaLine += ' · portfolio: ' + esc(String(opt.portfolio_stability));
    }
    const idb = DATA.ideas;
    if (idb && idb.present && idb.portfolio) {
      const pf = idb.portfolio;
      const bt = pf.by_type || {};
      metaLine += ' · ideas: ' + esc(String(pf.idea_total || 0)) +
        ' (exploit ' + esc(String(bt.exploit || 0)) + ' / explore ' + esc(String(bt.explore || 0)) +
        ' / invent ' + esc(String(bt.invent || 0)) + ')';
      if (pf.rejected_duplicate_total) {
        metaLine += ' · rejected dups: ' + esc(String(pf.rejected_duplicate_total));
      }
    }
    document.getElementById('meta').textContent = metaLine;
    renderDiagnosticsStrip();
    renderOperatorAlerts();
    renderOrchestrationPortfolioPriorities();
    renderOrchestrationPortfolioTrends();
    renderLastLoopPanel();
    renderTemporalPanel();
    renderIdeasPanel();
    renderRefinementPanel();
    const states = new Set();
    const statuses = new Set();
    (DATA.products || []).forEach(p => {
      if (p.state) states.add(p.state);
      if (p.status) statuses.add(p.status);
    });
    const fs = document.getElementById('fState');
    const ft = document.getElementById('fStatus');
    [...states].sort().forEach(s => { const o = document.createElement('option'); o.value = s; o.textContent = s; fs.appendChild(o); });
    [...statuses].sort().forEach(s => { const o = document.createElement('option'); o.value = s; o.textContent = s; ft.appendChild(o); });
    render();
    renderAutonomyPanel();
    renderEconomicsPanel();
    renderActionsPanel();
  }
  function filtered() {
    const st = document.getElementById('fState').value;
    const stat = document.getElementById('fStatus').value;
    const costMax = parseFloat(document.getElementById('fCost').value);
    const sev = document.getElementById('fSev').value;
    const act = (document.getElementById('fAction').value || '').toLowerCase();
    return (DATA.products || []).filter(p => {
      if (st && p.state !== st) return false;
      if (stat && p.status !== stat) return false;
      if (!isNaN(costMax) && p.monthly_cost_usd != null && p.monthly_cost_usd > costMax) return false;
      if (sev && !passesSevFilter(p.findings_by_severity, sev)) return false;
      if (act) {
        const hay = ((p.top_intent || '') + ' ' + (p.top_recommended_action || '')).toLowerCase();
        if (!hay.includes(act)) return false;
      }
      return true;
    }).map(augmentRow);
  }
  function sortedRows(rows) {
    const key = document.getElementById('sortKey').value;
    const desc = document.getElementById('sortDir').checked;
    const mul = desc ? -1 : 1;
    const cp = [...rows];
    cp.sort((a, b) => {
      let va = a[key], vb = b[key];
      if (key === 'lifecycle_stage') { va = va || ''; vb = vb || ''; }
      if (key === 'ov_uncertainty' || key === 'ov_risk' || key === 'ov_esc_pressure') {
        if (va == null || isNaN(va)) va = -1;
        if (vb == null || isNaN(vb)) vb = -1;
      }
      if (key === 'ov_exploratory') {
        va = va ? 1 : 0;
        vb = vb ? 1 : 0;
      }
      if (key === 'ideas_count' || key === 'ideas_invent_count') {
        va = va != null ? Number(va) : 0;
        vb = vb != null ? Number(vb) : 0;
      }
      if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * mul;
      if (va == null) va = key.includes('score') || key.includes('cost') || key.includes('findings') || key.startsWith('delta') || key === 'hist_n' ? -1e9 : '';
      if (vb == null) vb = key.includes('score') || key.includes('cost') || key.includes('findings') || key.startsWith('delta') || key === 'hist_n' ? -1e9 : '';
      if (typeof va === 'number') return (va - vb) * mul;
      return String(va).localeCompare(String(vb)) * mul;
    });
    return cp;
  }
  function flagTags(p) {
    const ts = p.trend_summary;
    if (!ts || !ts.trend_flags || !ts.trend_flags.length) return '—';
    return ts.trend_flags.map(f => {
      let cls = 'tag';
      if (f.includes('improving')) cls += ' improving';
      else if (f.includes('risk') || f.includes('abandon')) cls += ' risk';
      else if (f.includes('thrash') || f.includes('stagnat') || f.includes('drift')) cls += ' warn';
      return '<span class="' + cls + '">' + esc(f) + '</span>';
    }).join(' ');
  }
  function render() {
    const tbody = document.getElementById('tbody');
    tbody.innerHTML = '';
    const rows = sortedRows(filtered());
    rows.forEach(p => {
      const tr = document.createElement('tr');
      tr.dataset.pid = p.product_id;
      tr.innerHTML =
        '<td>' + esc(p.product_id) + '</td>' +
        '<td>' + esc(p.type) + '</td>' +
        '<td>' + esc(p.state) + '</td>' +
        '<td>' + esc(p.status) + '</td>' +
        '<td class="num">' + (p.monthly_cost_usd != null ? esc(String(p.monthly_cost_usd)) : '—') + '</td>' +
        '<td>' + esc(p.last_signal_at || '—') + '</td>' +
        '<td>' + temporalStatusTag(p.temporal_overall) + '</td>' +
        '<td class="mini">' + esc(String(p.temporal_freshness || '—')) + '</td>' +
        '<td class="num">' + esc(String(p.active_findings_count)) + '</td>' +
        '<td class="num">' + esc(String(p.temporal_findings_count != null ? p.temporal_findings_count : 0)) + '</td>' +
        '<td class="num">' + esc(String(p.ideas_count != null ? p.ideas_count : 0)) + '</td>' +
        '<td class="num">' + esc(String(p.ideas_invent_count != null ? p.ideas_invent_count : 0)) + '</td>' +
        '<td class="num">' + fmtDelta(p.delta_findings) + '</td>' +
        '<td class="num">' + fmtDelta(p.delta_cost) + '</td>' +
        '<td class="num">' + fmtDelta(p.delta_priority) + '</td>' +
        '<td class="num">' + fmtDelta(p.delta_esc) + '</td>' +
        '<td class="num">' + esc(String(p.hist_n || 0)) + '</td>' +
        '<td>' + flagTags(p) + '</td>' +
        '<td>' + esc((p.top_recommended_action || '').slice(0, 56)) + '</td>' +
        '<td class="num">' + (p.priority_score != null ? esc(String(Number(p.priority_score).toFixed(2))) : '—') + '</td>' +
        '<td class="num">' + (p.top_confidence != null ? esc(String(p.top_confidence)) : '—') + '</td>' +
        '<td class="num">' + miniBarScore(p.ov_uncertainty, 'unc') + '</td>' +
        '<td class="num">' + miniBarScore(p.ov_risk, 'risk') + '</td>' +
        '<td class="num">' + miniBarScore(p.ov_esc_pressure, 'esc') + '</td>' +
        '<td>' + exploratoryCell(p.ov_exploratory, p.ov_risk) + '</td>';
      tr.addEventListener('click', () => selectRow(tr, p));
      tbody.appendChild(tr);
    });
  }
  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }
  function repoPathToHref(r) {
    if (!r || typeof r !== 'string') return '#';
    if (r.indexOf('runs/') === 0) return '../' + r.slice(5);
    return r;
  }
  function sparklineSVG(values, w, h) {
    const v = values.filter(x => x != null && !isNaN(x));
    if (v.length < 2) return '';
    const mn = Math.min(...v), mx = Math.max(...v);
    const span = mx - mn || 1;
    const pts = v.map((y, i) => {
      const x = (i / (v.length - 1)) * (w - 4) + 2;
      const yn = h - 2 - ((y - mn) / span) * (h - 4);
      return x + ',' + yn;
    });
    return '<svg class="spark" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '">' +
      '<polyline fill="none" stroke="#8ab4f8" stroke-width="1.5" points="' + pts.join(' ') + '"/></svg>';
  }
  function selectRow(tr, p) {
    document.querySelectorAll('tbody tr').forEach(r => r.classList.remove('selected'));
    tr.classList.add('selected');
    const d = document.getElementById('detail');
    const pts = sliceHistory(p.history_points || []);
    const escals = (p.escalations || []).length
      ? '<ul>' + p.escalations.map(e => '<li><span class="tag">' + esc(e.risk_level || '') + '</span> ' + esc(e.packet_id || '') + ' — ' + esc(e.title || '') + '</li>').join('') + '</ul>'
      : '<p class="empty">No escalation packets for this product.</p>';
    const links = (p.artifacts || {});
    const linkStr = Object.keys(links).map(k =>
      '<a href="' + esc(repoPathToHref(links[k])) + '">' + esc(k) + '</a>'
    ).join(' ');
    const al = DATA.artifact_links || {};
    const histLink = al.history_latest_from_dashboard
      ? '<a href="' + esc(al.history_latest_from_dashboard) + '">history latest manifest</a>'
      : '';
    const trLink = al.trends_latest_from_dashboard
      ? '<a href="' + esc(al.trends_latest_from_dashboard) + '">trends report</a>'
      : '';
    const genLink = al.decisions_generations_from_dashboard
      ? '<a href="' + esc(al.decisions_generations_from_dashboard) + '">decision generations</a>'
      : '';
    const portLink = al.portfolio_decisions_from_dashboard
      ? '<a href="' + esc(al.portfolio_decisions_from_dashboard) + '">portfolio decisions</a>'
      : '';
    const snapLinks = (DATA.history && DATA.history.snapshots_catalog || []).map(c =>
      '<a href="' + esc(c.path_from_dashboard) + '">' + esc(c.snapshot_id) + '</a>'
    ).join(' · ');
    let trendBlock = '';
    if (p.trend_summary) {
      const ts = p.trend_summary;
      trendBlock = '<h3>Trend / drift (rules-based)</h3><p class="mini">confidence: ' + esc(String(ts.confidence)) +
        ' · window: ' + esc(String(ts.window_size)) + '</p><p>' + esc(ts.recommended_interpretation || '') + '</p>' +
        (ts.drift_signals && ts.drift_signals.length
          ? '<ul>' + ts.drift_signals.map(s => '<li>' + esc(s) + '</li>').join('') + '</ul>' : '');
    } else {
      trendBlock = '<p class="empty">No trend summary (need ≥2 snapshots in runs/history). Run <code>argus history snapshot</code> and <code>argus trends analyze</code>.</p>';
    }
    let histCharts = '';
    if (pts.length >= 2) {
      const findings = pts.map(x => x.active_findings_count);
      const costs = pts.map(x => x.monthly_cost_usd);
      const prios = pts.map(x => x.priority_score);
      histCharts =
        '<h3>History sparklines (' + pts.length + ' points, ' + document.getElementById('histWin').selectedOptions[0].textContent + ')</h3>' +
        '<div class="spark-wrap">' + sparklineSVG(findings, 120, 36) + '</div> <span class="mini">findings</span> ' +
        '<div class="spark-wrap">' + sparklineSVG(costs, 120, 36) + '</div> <span class="mini">cost</span> ' +
        '<div class="spark-wrap">' + sparklineSVG(prios, 120, 36) + '</div> <span class="mini">priority</span>';
      histCharts += '<h4>Timeline</h4><table class="hist-table"><thead><tr><th>snapshot</th><th>find</th><th>$</th><th>pri</th><th>esc</th><th>stage</th><th>top action</th></tr></thead><tbody>';
      pts.forEach(row => {
        histCharts += '<tr><td>' + esc(row.snapshot_id) + '</td><td class="num">' + esc(String(row.active_findings_count)) +
          '</td><td class="num">' + (row.monthly_cost_usd != null ? esc(String(row.monthly_cost_usd)) : '—') +
          '</td><td class="num">' + (row.priority_score != null ? esc(String(row.priority_score)) : '—') +
          '</td><td class="num">' + esc(String(row.escalation_count)) +
          '</td><td>' + esc(row.lifecycle_stage || '') + '</td><td>' + esc((row.top_recommended_action || '').slice(0, 64)) + '</td></tr>';
      });
      histCharts += '</tbody></table>';
      const stages = pts.map(x => x.lifecycle_stage);
      const actions = pts.map(x => (x.top_recommended_action || '').slice(0, 80));
      let stCh = 0, acCh = 0;
      for (let i = 1; i < pts.length; i++) {
        if (stages[i] !== stages[i-1]) stCh++;
        if (actions[i] !== actions[i-1]) acCh++;
      }
      histCharts += '<p class="mini">Lifecycle stage changes: ' + stCh + ' · Top action changes: ' + acCh + '</p>';
    } else {
      histCharts = '<p class="empty">Not enough history points for charts (need ≥2 snapshots including this product).</p>';
    }
    const kill = p.kill_candidate ? ' <span class="warn">kill_candidate</span>' : '';
    const ss = p.strategy_snapshot || {};
    const pls = p.planning_snapshot || {};
    const opi = p.orchestration_planning_influence || {};
    let spBlock = '<h3>Strategy &amp; planning visibility</h3>';
    const hasSp = ss.present || pls.present || opi.present;
    if (!hasSp) {
      spBlock += '<p class="mini">No strategy snapshot, planning snapshot, or orchestration state file for this product yet.</p>';
    } else {
      if (ss.present) {
        if (ss.parse_error) {
          spBlock += '<p class="warn">Strategy snapshot: invalid JSON.</p>';
        } else if (!ss.readable) {
          spBlock += '<p class="warn">Strategy snapshot: unreadable.</p>';
        } else if (ss.schema_mismatch) {
          spBlock += '<p class="warn">Strategy snapshot: schema mismatch.</p>';
        } else {
          spBlock += '<p class="mini"><strong>Strategy</strong> · posture <code class="cmd">' + esc(String(ss.posture || '—')) + '</code>' +
            ' · raw <code class="cmd">' + esc(String(ss.posture_raw || '—')) + '</code>';
          if (ss.skepticism_applied) {
            spBlock += ' · skepticism: ' + esc(String(ss.skepticism_reason || ''));
          }
          spBlock += '</p><p class="mini"><code class="cmd">' + esc(ss.path_repo || '') + '</code></p>';
        }
      }
      if (pls.present) {
        if (pls.parse_error) {
          spBlock += '<p class="warn">Planning snapshot: invalid JSON.</p>';
        } else if (!pls.readable) {
          spBlock += '<p class="warn">Planning snapshot: unreadable.</p>';
        } else if (pls.schema_mismatch) {
          spBlock += '<p class="warn">Planning snapshot: schema mismatch.</p>';
        } else {
          const ws = (pls.priority_workstreams || []).map(function (x) { return String(x); }).join(', ');
          spBlock += '<p class="mini"><strong>Planning</strong> · mode <code class="cmd">' + esc(String(pls.planning_mode || '—')) + '</code>' +
            ' · recommended_actions: ' + esc(String(pls.recommended_actions_count != null ? pls.recommended_actions_count : '—')) +
            (ws ? ' · workstreams: ' + esc(ws.slice(0, 400)) : '') + '</p>' +
            '<p class="mini"><code class="cmd">' + esc(pls.path_repo || '') + '</code></p>';
        }
      }
      if (opi.present) {
        if (opi.parse_error) {
          spBlock += '<p class="warn">Orchestration state: invalid JSON.</p>';
        } else if (!opi.readable) {
          spBlock += '<p class="warn">Orchestration state: unreadable.</p>';
        } else {
          spBlock += '<p class="mini"><strong>Planning influence (orchestration)</strong> · mode considered: ' +
            esc(String(opi.planning_mode_considered != null ? opi.planning_mode_considered : '—')) +
            ' · priority_adjustment_applied: ' + esc(String(opi.planning_priority_adjustment_applied)) +
            (opi.next_action_planning_note ? ' · note: ' + esc(String(opi.next_action_planning_note)) : '') +
            '</p><p class="mini"><code class="cmd">' + esc(opi.path_repo || '') + '</code></p>';
        }
      }
    }
    const ideasRows = p.ideas || [];
    let ideasDetail = '';
    if (ideasRows.length) {
      ideasDetail = '<h3>Ideas (latest bundle)</h3><table class="ideas-mini"><thead><tr><th>type</th><th>source</th><th>title</th><th>novelty</th><th>diversity</th><th>EV</th><th>conf</th><th>risk/reward</th><th>selection</th></tr></thead><tbody>';
      ideasRows.forEach(function (x) {
        const hl = x.highlights || [];
        const rowCls = hl.indexOf('invent') >= 0 ? 'idea-invent' : (hl.indexOf('high_risk_reward') >= 0 ? 'idea-hrr' : '');
        ideasDetail += '<tr class="' + rowCls + '"><td>' + esc(x.type || '') + '</td><td>' + esc(x.source || '') +
          '</td><td>' + esc((x.title || '').slice(0, 120)) + '</td><td class="num">' +
          esc(String(x.novelty_score != null ? Number(x.novelty_score).toFixed(2) : '—')) +
          '</td><td class="num">' + esc(String(x.diversity_score != null ? Number(x.diversity_score).toFixed(2) : '—')) +
          '</td><td class="num">' + esc(String(x.expected_value_score != null ? Number(x.expected_value_score).toFixed(2) : '—')) +
          '</td><td class="num">' + esc(String(x.confidence_score != null ? Number(x.confidence_score).toFixed(2) : '—')) +
          '</td><td>' + esc(x.risk_reward_tier || '') + '</td><td>' + esc(x.selection_status || '') + '</td></tr>';
      });
      ideasDetail += '</tbody></table>';
      ideasRows.slice(0, 4).forEach(function (x) {
        if (x.rationale) {
          ideasDetail += '<p class="mini"><strong>' + esc((x.title || '').slice(0, 48)) + ':</strong> ' +
            esc(String(x.rationale).slice(0, 420)) + '</p>';
        }
      });
    } else {
      ideasDetail = '<h3>Ideas</h3><p class="mini">No ideas scoped to this product in the latest bundle. Run <code class="cmd">argus ideas generate ' + esc(p.product_id || '') + '</code> or check portfolio-only rows in the Ideas panel.</p>';
    }
    const tv = p.temporal_visibility || {};
    const tvBlock = tv && tv.product_id
      ? '<h4>Temporal visibility</h4>' +
        (tv.operator_note ? '<p class="mini">' + esc(tv.operator_note) + '</p>' : '') +
        '<p class="mini">Overall: ' + temporalStatusTag(tv.overall) +
        ' · requirement: ' + esc(tv.temporal_requirement || '') + '</p>' +
        (tv.signals && tv.signals.validation_issues && tv.signals.validation_issues.length
          ? '<p class="warn">Signal bundle checks: ' + esc(tv.signals.validation_issues.join('; ')) + '</p>' : '') +
        (tv.flags && tv.flags.length
          ? '<p class="warn">Flags: ' + esc(tv.flags.join(', ')) + '</p>' : '<p class="mini">No warning flags.</p>') +
        '<p class="mini">Pipeline: ' + esc((tv.pipeline && tv.pipeline.coherent) ? 'coherent' : 'issues') +
        (tv.pipeline && tv.pipeline.warnings && tv.pipeline.warnings.length
          ? ' — ' + esc(tv.pipeline.warnings.join(' · ')) : '') + '</p>' +
        '<p class="mini">Temporal bundle: ' + esc((tv.temporal_bundle && tv.temporal_bundle.present) ? 'present' : 'absent') +
        (tv.temporal_bundle && tv.temporal_bundle.worst_freshness_bucket
          ? ' · worst bucket: ' + esc(tv.temporal_bundle.worst_freshness_bucket) : '') +
        (tv.temporal_bundle && tv.temporal_bundle.malformed ? ' <span class="warn">(malformed JSON)</span>' : '') +
        '</p>'
      : '<h4>Temporal visibility</h4><p class="empty">No temporal_visibility in payload — regenerate dashboard.</p>';
    const ov = p.operator_visibility || {};
    let ovBlock = '<h3>Confidence &amp; risk</h3>';
    if (!ov || !ov.schema) {
      ovBlock += '<p class="empty">No operator_visibility block — regenerate dashboard.</p>';
    } else {
      ovBlock += '<p class="mini">' + esc(ov.uncertainty_basis || '') + '</p>' +
        '<ul class="mini"><li>confidence (top candidate): ' + (ov.confidence != null ? esc(String(ov.confidence)) : '—') +
        '</li><li>uncertainty (1−confidence when present): ' + (ov.uncertainty != null ? esc(String(ov.uncertainty)) : '—') +
        '</li><li>risk score: ' + esc(String(ov.risk_score)) +
        '</li><li>escalation pressure: ' + esc(String(ov.escalation_pressure)) +
        '</li><li>exploratory: ' + esc(String(ov.exploratory)) +
        '</li><li>evidence density: ' + esc(String(ov.evidence_density)) + '</li></ul>';
      if (ov.badges && ov.badges.length) {
        ovBlock += '<p>' + ov.badges.map(b => '<span class="tag ' +
          (b.tone === 'bad' ? 't-bad' : (b.tone === 'warn' ? 't-warn' : 't-muted')) + '">' + esc(b.label || '') + '</span>').join(' ') + '</p>';
      }
      ovBlock += '<h4>Factor breakdown</h4><pre>' + esc(JSON.stringify(ov.factor_breakdown || {}, null, 2).slice(0, 4000)) + '</pre>' +
        '<h4>Friction sources</h4>';
      if (ov.friction_sources && ov.friction_sources.length) {
        ovBlock += '<ul>' + ov.friction_sources.map(x => '<li>' + esc(x) + '</li>').join('') + '</ul>';
      } else {
        ovBlock += '<p class="empty">None flagged.</p>';
      }
      ovBlock += '<h4>Top uncertainty contributors</h4>';
      if (ov.top_uncertainty_contributors && ov.top_uncertainty_contributors.length) {
        ovBlock += '<ul>' + ov.top_uncertainty_contributors.map(x => '<li>' + esc(x) + '</li>').join('') + '</ul>';
      } else {
        ovBlock += '<p class="empty">None listed.</p>';
      }
      const rr = ov.recurrence_risk || {};
      ovBlock += '<h4>Recurrence risk (trends)</h4><p class="mini">' +
        esc(rr.present ? 'Elevated pattern in trend/drift signals' : 'Not flagged from trend rules') + '</p>';
      if (rr.notes && rr.notes.length) {
        ovBlock += '<ul class="mini">' + rr.notes.map(n => '<li>' + esc(n) + '</li>').join('') + '</ul>';
      }
    }
    d.innerHTML =
      '<h3 style="margin-top:0">' + esc(p.product_id) + kill + '</h3>' +
      spBlock + ideasDetail + tvBlock + ovBlock +
      '<div class="links">' + linkStr + ' <span class="mini">(relative to this HTML file)</span><br/>' +
      histLink + ' ' + trLink + ' ' + genLink + ' ' + portLink + '</div>' +
      (snapLinks ? '<h4>Snapshot files</h4><p class="mini">' + snapLinks + '</p>' : '') +
      trendBlock + histCharts +
      '<h4>product.yaml summary</h4><pre>' + esc((p.product_yaml_summary_lines || []).join(String.fromCharCode(10))) + '</pre>' +
      '<h4>Lifecycle scores</h4><pre>' + esc(JSON.stringify(p.lifecycle_scores || {}, null, 2)) + '</pre>' +
      '<h4>Escalations</h4>' + escals +
      (function() {
        const n = p.temporal_findings_count || 0;
        const bk = p.temporal_findings_by_kind || {};
        if (!n && !Object.keys(bk).length) return '';
        const parts = Object.keys(bk).map(k => '<span class="tag">' + esc(k) + '</span> ' + esc(String(bk[k])));
        return '<h4>Temporal findings</h4><p class="mini">Count: ' + esc(String(n)) +
          (parts.length ? ' · ' + parts.join(' ') : '') + '</p>';
      })() +
      '<h4>Recent signals (' + (p.signal_records || []).length + ' shown)</h4>' +
      '<pre>' + esc(JSON.stringify(p.signal_records || [], null, 2).slice(0, 12000)) + '</pre>' +
      '<h4>Findings (' + (p.findings || []).length + ')</h4>' +
      '<pre>' + esc(JSON.stringify(p.findings || [], null, 2).slice(0, 12000)) + '</pre>' +
      '<h4>Recommended actions (candidates)</h4>' +
      '<pre>' + esc(JSON.stringify(p.candidates || [], null, 2).slice(0, 8000)) + '</pre>';
  }
  ['fState','fStatus','fCost','fSev','fAction','sortKey','sortDir','histWin'].forEach(id => {
    document.getElementById(id).addEventListener('change', render);
    document.getElementById(id).addEventListener('input', render);
  });
  document.querySelectorAll('thead th[data-k]').forEach(th => {
    th.addEventListener('click', () => {
      document.getElementById('sortKey').value = th.dataset.k;
      render();
    });
  });
  function renderRefinementPanel() {
    const root = document.getElementById('refinementPanelBody');
    const intro = document.getElementById('refinementIntro');
    if (!root || !intro) return;
    const ref = DATA.refinement;
    if (!ref || !ref.schema) {
      intro.textContent = '';
      root.innerHTML = '<p class="empty">No refinement block in dashboard payload.</p>';
      return;
    }
    const act = ref.active_sessions || [];
    const vt = ref.verdict_totals_latest_round || {};
    const det = ref.session_details || {};
    const sch = ref.schema || '';
    intro.textContent = 'Schema: ' + esc(sch) + ' · open sessions: ' + act.length + ' · index entries: ' + (ref.index_total || 0) +
      ' · latest verdicts (all sessions): pass ' + (vt.pass || 0) + ', concern ' + (vt.concern || 0) + ', fail ' + (vt.fail || 0);
    if (!act.length) {
      root.innerHTML = '<p class="empty">No active refinement sessions. Use <code class="cmd">argus refine start</code> / <code class="cmd">argus refine run</code>.</p>';
      return;
    }
    let html = '<table class="ideas-mini"><thead><tr><th>session</th><th>type</th><th>round</th><th>status</th><th>blocking</th><th>source</th></tr></thead><tbody>';
    act.forEach(function (r) {
      const sid = r.session_id || '';
      html += '<tr><td class="mini">' + esc(sid) + '</td><td>' + esc(String(r.artifact_type || '')) + '</td><td>' +
        esc(String(r.current_round != null ? r.current_round : '')) + '</td><td>' + esc(String(r.status || '')) + '</td><td>' +
        esc(String(r.blocking_issues_count != null ? r.blocking_issues_count : '')) + '</td><td>' + esc(String(r.source_id || '')) + '</td></tr>';
      const d = det[sid];
      if (d && typeof d === 'object') {
        const rs = d.round_sets || {};
        const chain = 'drafts [' + (rs.drafts || []).join(',') + '] · reviews [' + (rs.reviews || []).join(',') + '] · syn [' +
          (rs.synthesis || []).join(',') + '] · conv [' + (rs.convergence || []).join(',') + ']';
        let health = '';
        const re = d.round_chain_errors || [];
        const rw = d.round_chain_warnings || [];
        if (re.length) health += '<div class="refine-err">chain errors: ' + esc(re.join(' · ')) + '</div>';
        if (rw.length) health += '<div class="refine-warn">chain warnings: ' + esc(rw.join(' · ')) + '</div>';
        const j = JSON.stringify(d, null, 2);
        html += '<tr><td colspan="6" style="border-bottom:1px solid #333;padding-left:0.5rem">' +
          '<details class="refine-details"><summary>Session detail — ' + esc(sid.slice(0, 28)) + '…</summary>' +
          '<p class="refine-chain">' + esc(chain) + '</p>' + health +
          '<pre>' + esc(j.slice(0, 14000)) + (j.length > 14000 ? '\\n…' : '') + '</pre></details></td></tr>';
      }
    });
    html += '</tbody></table>';
    root.innerHTML = html;
  }
  function renderIdeasPanel() {
    const root = document.getElementById('ideasPanelBody');
    const intro = document.getElementById('ideasIntro');
    if (!root || !intro) return;
    const idb = DATA.ideas;
    if (!idb) {
      intro.textContent = '';
      root.innerHTML = '<p class="warn">No ideas block — run <code class="cmd">argus dashboard</code> to regenerate.</p>';
      return;
    }
    if (!idb.present) {
      intro.textContent = '';
      root.innerHTML = '<p class="empty">No <code class="cmd">runs/ideas/latest.json</code> yet. Run <code class="cmd">argus ideas generate</code> or <code class="cmd">argus ideas generate &lt;product_id&gt;</code>.</p>';
      return;
    }
    const pf = idb.portfolio || {};
    const bt = pf.by_type || {};
    intro.textContent =
      'Bundle: ' + esc(idb.generated_at_utc || '?') +
      (idb.bundle_scope_product_id != null ? ' · scope: ' + esc(String(idb.bundle_scope_product_id)) : ' · scope: portfolio') +
      ' · diversity index: ' + esc(String(pf.diversity_index != null ? pf.diversity_index : '—')) +
      ' · high risk/reward: ' + esc(String(pf.high_risk_reward_count || 0)) +
      ' · invent: ' + esc(String(pf.invent_count || 0));
    let html = '<h3>Portfolio distribution</h3><ul class="mini">' +
      '<li>exploit: ' + esc(String(bt.exploit || 0)) + '</li>' +
      '<li>explore: ' + esc(String(bt.explore || 0)) + '</li>' +
      '<li>invent: ' + esc(String(bt.invent || 0)) + '</li>' +
      '<li>rejected duplicates: ' + esc(String(pf.rejected_duplicate_total || 0)) + '</li></ul>';
    const rej = idb.rejected_duplicates || [];
    if (rej.length) {
      html += '<h3>Rejected duplicates <span class="tag idea-dup">deduped</span></h3><ul>';
      rej.slice(0, 12).forEach((x) => {
        html += '<li class="idea-reject">' + esc(x.title || '') + ' <span class="mini">(' + esc(x.type || '') + ' · ' + esc(x.source || '') + ')</span></li>';
      });
      html += '</ul>';
    }
    const samp = idb.selected_ideas_sample || [];
    html += '<h3>Sample — inventive &amp; high risk/reward</h3><table class="ideas-mini"><thead><tr><th></th><th>title</th><th>scores</th><th>selection</th></tr></thead><tbody>';
    samp.filter(function (x) {
      const hl = x.highlights || [];
      return hl.indexOf('invent') >= 0 || hl.indexOf('high_risk_reward') >= 0;
    }).slice(0, 10).forEach(function (x) {
      const hl = x.highlights || [];
      let cls = '';
      if (hl.indexOf('invent') >= 0) cls = 'idea-invent';
      if (hl.indexOf('high_risk_reward') >= 0) cls = 'idea-hrr';
      const tags = (hl.length ? hl.map(function (h) {
        return '<span class="tag ' + (h === 'invent' ? 'idea-inv' : '') + (h === 'high_risk_reward' ? ' idea-rr' : '') + '">' + esc(h) + '</span>';
      }).join(' ') : '');
      html += '<tr class="' + cls + '"><td>' + tags + '</td><td>' + esc((x.title || '').slice(0, 120)) + '</td><td class="mini">nov ' +
        esc(String(x.novelty_score != null ? Number(x.novelty_score).toFixed(2) : '—')) +
        ' · div ' + esc(String(x.diversity_score != null ? Number(x.diversity_score).toFixed(2) : '—')) +
        ' · EV ' + esc(String(x.expected_value_score != null ? Number(x.expected_value_score).toFixed(2) : '—')) +
        '</td><td>' + esc(x.selection_status || '') + '</td></tr>';
    });
    html += '</tbody></table>';
    const po = idb.portfolio_only_ideas || [];
    if (po.length) {
      html += '<h3>Portfolio-scoped ideas (no product id)</h3><ul>';
      po.slice(0, 6).forEach(function (x) {
        html += '<li>' + esc((x.title || '').slice(0, 160)) + '</li>';
      });
      html += '</ul>';
    }
    html += '<p class="mini">Artifact: <code class="cmd">runs/ideas/latest.json</code> · <code class="cmd">argus ideas list</code></p>';
    root.innerHTML = html;
  }
  function renderTemporalPanel() {
    const root = document.getElementById('temporalPanelBody');
    const intro = document.getElementById('temporalIntro');
    if (!root || !intro) return;
    const t = DATA.temporal;
    if (!t) {
      intro.textContent = '';
      root.innerHTML = '<p class="warn">No temporal block in payload — run <code class="cmd">argus dashboard</code> to regenerate.</p>';
      return;
    }
    const sc = t.summary_counts || {};
    const gf = t.global_flags || [];
    const tol = t.tolerances || {};
    const tolHint = (tol.signal_collection_max_age_days != null && tol.future_collection_timestamp_sec != null)
      ? (' · thresholds: collection age > ' + esc(String(tol.signal_collection_max_age_days)) + 'd → stale; '
        + 'timestamp > ' + esc(String(tol.future_collection_timestamp_sec)) + 's ahead of reference → integrity')
      : '';
    intro.textContent =
      'Reference ' + esc(t.reference_time_utc || t.generated_at_utc || '?') +
      ' · last signal collection (max): ' + esc(t.last_signal_collection_max_utc || '—') +
      ' · counts — current: ' + esc(String(sc.current || 0)) +
      ', stale: ' + esc(String(sc.stale || 0)) +
      ', missing: ' + esc(String(sc.missing || 0)) +
      ', unknown: ' + esc(String(sc.unknown || 0)) +
      ', not required: ' + esc(String(sc.not_required || 0)) +
      (gf.length ? ' · global: ' + esc(gf.join(', ')) : '') +
      tolHint;
    const rf = t.recent_temporal_findings || [];
    let html = '<h3>Recent temporal-related findings</h3>';
    if (!rf.length) {
      html += '<p class="empty">None (inactivity / stale context / no recent evidence kinds).</p>';
    } else {
      html += '<ul>';
      rf.forEach(x => {
        html += '<li><span class="tag">' + esc(x.kind || '') + '</span> <strong>' + esc(x.product_id || '') + '</strong> — ' +
          esc((x.title || '').slice(0, 200)) + '</li>';
      });
      html += '</ul>';
    }
    html += '<p class="mini">Use <code class="cmd">argus doctor</code> for missing/stale artifact checks.</p>';
    root.innerHTML = html;
  }
  function renderLastLoopPanel() {
    const root = document.getElementById('lastLoopPanelBody');
    const intro = document.getElementById('lastLoopIntro');
    if (!root || !intro) return;
    const L = DATA.last_loop_run;
    if (!L || !L.present) {
      intro.textContent = '';
      root.innerHTML = '<p class="empty">No runs/loop/ harness yet. Run <span class="cmd">uv run argus loop full</span> (see docs/first-run.md).</p>';
      return;
    }
    intro.textContent = 'Dry-run execution stage: ' + (L.dry_run_execution ? 'yes (static checks only)' : 'flag off — subprocess still gated elsewhere');
    const hrefJson = L.summary_json_repo ? '../' + L.summary_json_repo.replace(/^runs\\//, '') : '#';
    const hrefTxt = L.summary_txt_repo ? '../' + L.summary_txt_repo.replace(/^runs\\//, '') : null;
    let html = '<p class="mini">run_id <strong>' + esc(String(L.run_id)) + '</strong> · stages ok: ' +
      esc(String(L.stage_ok_count)) + ' · failed: ' + esc(String(L.stage_fail_count)) +
      ' · harness ok: ' + esc(String(L.ok)) + '</p>';
    html += '<p class="mini"><a href="' + esc(hrefJson) + '">summary.json</a>';
    if (hrefTxt) {
      html += ' · <a href="' + esc(hrefTxt) + '">summary.txt</a>';
    }
    html += ' · <span class="cmd">uv run argus run summary ' + esc(String(L.run_id)) + '</span></p>';
    const tids = L.target_product_ids || [];
    if (tids.length) {
      html += '<p class="mini">Products: ' + esc(tids.slice(0, 16).join(', ')) + (tids.length > 16 ? ' …' : '') + '</p>';
    }
    root.innerHTML = html;
  }
  function renderAutonomyPanel() {
    const root = document.getElementById('autonomyPanelBody');
    const intro = document.getElementById('autonomyIntro');
    if (!root || !intro) return;
    const a = DATA.autonomy_safety;
    if (!a) {
      root.innerHTML = '<p class="empty">No autonomy_safety in payload (regenerate dashboard).</p>';
      intro.textContent = '';
      return;
    }
    const lar = a.last_autonomy_run || {};
    const gs = a.guardrail_summary || {};
    const gl = a.guardrail_limits || {};
    intro.textContent =
      'Mode: ' + (a.autonomy_mode || '—') +
      ' · tier: ' + (a.autonomy_tier != null ? a.autonomy_tier : '—') +
      ' · block_streak: ' + (gs.block_streak != null ? gs.block_streak : '—') +
      ' · pending approvals: ' + (a.pending_approvals != null ? a.pending_approvals : '—') +
      ' · open capability requests: ' + (a.pending_capability_requests || 0) +
      ' · execution runs: ' + (a.execution && a.execution.total != null ? a.execution.total : 0);
    let html = '<h3>Last autonomy run</h3>';
    if (!lar.run_id) {
      html += '<p class="empty">No runs/autonomy/*/manifest.json yet.</p>';
    } else {
      const href = lar.path_repo ? '../' + lar.path_repo.replace(/^runs\\//, '') : '#';
      html += '<p class="mini"><a href="' + esc(href) + '">' + esc(lar.run_id) + '</a> · started ' +
        esc(lar.started_at_utc || '') + ' · ok=' + esc(String(lar.ok)) + '</p>';
    }
    html += '<h3>Blocked actions (capability)</h3>';
    const blk = a.blocked_actions || [];
    if (!blk.length) {
      html += '<p class="empty">None recorded (see runs/autonomy/blocked_actions.json).</p>';
    } else {
      html += '<div class="wrap-table"><table><thead><tr><th>action_id</th><th>product</th><th>reason</th></tr></thead><tbody>';
      blk.forEach(b => {
        html += '<tr><td class="cmd">' + esc(b.action_id || '') + '</td><td>' + esc(b.product_id || '') +
          '</td><td>' + esc((b.reason || '').slice(0, 200)) + '</td></tr>';
      });
      html += '</tbody></table></div>';
    }
    html += '<h3>Policy caps (effective)</h3>';
    html += '<p class="mini">max_actions/day: ' + esc(String(gl.max_actions_per_run != null ? gl.max_actions_per_run : '—')) +
      ' · max_cost/day USD: ' + esc(String(gl.max_cost_per_day != null ? gl.max_cost_per_day : '—')) +
      ' · spawns/day: ' + esc(String(gl.max_product_spawns_per_utc_day != null ? gl.max_product_spawns_per_utc_day : '—')) +
      ' · experiments/day: ' + esc(String(gl.max_experiments_per_utc_day != null ? gl.max_experiments_per_utc_day : '—')) +
      ' · shutdowns/day: ' + esc(String(gl.max_shutdowns_per_utc_day != null ? gl.max_shutdowns_per_utc_day : '—')) +
      ' · min_confidence (auto): ' + esc(String(gl.min_confidence_autonomous != null ? gl.min_confidence_autonomous : '—')) +
      '</p>';
    html += '<h3>Execution success / failure (runs/execution)</h3>';
    const ex = a.execution || {};
    html += '<p>Success: <span class="risk-low">' + esc(String(ex.success || 0)) + '</span> · Failed: <span class="risk-high">' +
      esc(String(ex.failed || 0)) + '</span> · Other: ' + esc(String(ex.other || 0)) + '</p>';
    const ev = gs.recent_guardrail_events || [];
    html += '<h3>Recent guardrail events</h3>';
    if (!ev.length) {
      html += '<p class="empty">None (policy blocks append here).</p>';
    } else {
      html += '<ul>';
      ev.slice(-8).forEach(x => {
        html += '<li><span class="mini">' + esc(x.at_utc || '') + '</span> — ' + esc((x.reason || '').slice(0, 240)) + '</li>';
      });
      html += '</ul>';
    }
    const epk = a.escalation_packets_recent || [];
    html += '<h3>Recent escalation packets</h3>';
    if (!epk.length) {
      html += '<p class="empty">None under runs/escalations/latest/.</p>';
    } else {
      html += '<ul>';
      epk.forEach(x => {
        html += '<li><strong>' + esc(x.packet_id || '') + '</strong> · ' + esc(x.product_id || '') +
          ' · ' + esc(x.risk_level || '') + '</li>';
      });
      html += '</ul>';
    }
    const ed = a.escalation_dedupe && a.escalation_dedupe.groups ? a.escalation_dedupe.groups : [];
    html += '<h3>Escalation noise (grouped)</h3>';
    if (!ed.length) {
      html += '<p class="empty">No indexed packets or no repeats.</p>';
    } else {
      html += '<p class="mini">Same product + trigger fingerprint — higher count means repeated alerts.</p>';
      html += '<ul>';
      ed.slice(0, 12).forEach(g => {
        html += '<li><strong>' + esc(g.product_id || '') + '</strong> · count ' + esc(String(g.count || 0)) +
          ' · latest ' + esc(g.latest_packet_id || '') + '</li>';
      });
      html += '</ul>';
    }
    root.innerHTML = html;
  }
  function renderEconomicsPanel() {
    const root = document.getElementById('economicsPanelBody');
    const intro = document.getElementById('economicsIntro');
    if (!root || !intro) return;
    const er = DATA.economics_resources;
    const al = DATA.artifact_links || {};
    const link = al.economics_resources_from_dashboard
      ? '<a href="' + esc(al.economics_resources_from_dashboard) + '">resources_latest.json</a>'
      : '';
    if (!er) {
      root.innerHTML = '<p class="empty">No economics_resources in payload (regenerate dashboard).</p>';
      intro.textContent = '';
      return;
    }
    if (er.error) {
      intro.innerHTML = 'Could not read report: ' + esc(er.error) + '. ' + link;
      root.innerHTML = '';
      return;
    }
    if (!er.present) {
      intro.innerHTML =
        'No <code>runs/economics/resources_latest.json</code> yet. ' + link +
        ' Run <code class="cmd">argus economics resources</code> after config or ingest.';
      root.innerHTML = '<p class="empty">No resource linkage report.</p>';
      return;
    }
    intro.innerHTML =
      'Generated ' + esc(er.generated_at_utc || '?') + '. Mapped $' +
      esc(String(er.total_mapped_cost_usd != null ? er.total_mapped_cost_usd : '—')) +
      '/mo · orphan $' + esc(String(er.total_orphan_cost_usd != null ? er.total_orphan_cost_usd : '—')) +
      '/mo · ' + esc(String(er.resource_count || 0)) + ' resource row(s). ' + link;
    let html = '<h3>Unused / unmapped resources</h3><div class="wrap-table"><table><thead><tr>' +
      '<th>resource_id</th><th>kind</th><th class="num">$/mo</th><th>reason</th></tr></thead><tbody>';
    const op = er.orphans_preview || [];
    if (!op.length) {
      html += '<tr><td colspan="4" class="empty">None (all mapped to known products).</td></tr>';
    } else {
      op.forEach(o => {
        html += '<tr><td class="cmd">' + esc(o.resource_id || '') + '</td><td>' + esc(o.kind || '') +
          '</td><td class="num">' + esc(String(o.monthly_cost_usd != null ? o.monthly_cost_usd : '')) +
          '</td><td>' + esc(o.reason || '') + '</td></tr>';
      });
    }
    html += '</tbody></table></div>';
    html += '<h3>High-cost / low-value (heuristic)</h3>';
    const hp = er.hclv_preview || [];
    if (!hp.length) {
      html += '<p class="empty">None flagged.</p>';
    } else {
      html += '<div class="wrap-table"><table><thead><tr><th>product</th><th class="num">cost</th><th class="num">rev~</th><th>reason</th></tr></thead><tbody>';
      hp.forEach(h => {
        html += '<tr><td>' + esc(h.product_id || '') + '</td><td class="num">' +
          esc(String(h.monthly_cost != null ? h.monthly_cost : '')) +
          '</td><td class="num">' + esc(String(h.estimated_revenue != null ? h.estimated_revenue : '')) +
          '</td><td>' + esc((h.reason || '').slice(0, 160)) + '</td></tr>';
      });
      html += '</tbody></table></div>';
    }
    root.innerHTML = html;
  }
  function riskClass(level) {
    if (level === 'high') return 'risk-high';
    if (level === 'medium') return 'risk-medium';
    return 'risk-low';
  }
  function apprClass(st) {
    const m = {
      needs_approval: 'appr-needs',
      pending_record: 'appr-pending',
      approved: 'appr-approved',
      auto_eligible: 'appr-auto',
      rejected: 'appr-rejected',
    };
    return m[st] || '';
  }
  function actionsRow(r) {
    return '<tr>' +
      '<td>' + esc(r.action_id || '') + '</td>' +
      '<td>' + esc(r.product_id || '') + '</td>' +
      '<td><span class="' + riskClass(r.risk_level) + '">' + esc(r.risk_level || '—') + '</span></td>' +
      '<td><span class="' + apprClass(r.approval_status) + '">' + esc(r.approval_status || '') + '</span></td>' +
      '<td class="cmd">' + esc((r.command || '').slice(0, 200)) + '</td>' +
      '<td>' + esc((r.expected_outcome || '').slice(0, 160)) + '</td>' +
      '</tr>';
  }
  function actionsTable(title, rows) {
    if (!rows || !rows.length) {
      return '<h3>' + esc(title) + '</h3><p class="empty">None.</p>';
    }
    let h = '<h3>' + esc(title) + '</h3><div class="wrap-table"><table><thead><tr>' +
      '<th>action_id</th><th>product</th><th>risk</th><th>approval</th><th>command</th><th>expected outcome</th>' +
      '</tr></thead><tbody>';
    rows.forEach(r => { h += actionsRow(r); });
    h += '</tbody></table></div>';
    return h;
  }
  function execHistoryRow(r) {
    const st = (r.status || '');
    const cls = st === 'success' ? 'risk-low' : (st === 'failed' ? 'risk-high' : '');
    return '<tr>' +
      '<td><a href="' + esc(r.path_from_dashboard || '#') + '">' + esc(r.run_id || '') + '</a></td>' +
      '<td>' + esc(r.action_id || '') + '</td>' +
      '<td>' + esc(r.product_id || '') + '</td>' +
      '<td><span class="' + cls + '">' + esc(st) + '</span></td>' +
      '<td class="num">' + (r.exit_code != null ? esc(String(r.exit_code)) : '—') + '</td>' +
      '<td class="cmd">' + esc((r.command || '').slice(0, 120)) + '</td>' +
      '<td>' + esc((r.started_at || '').slice(0, 22)) + '</td>' +
      '</tr>';
  }
  function renderActionsPanel() {
    const root = document.getElementById('actionsPanelBody');
    const intro = document.getElementById('actionsIntro');
    const ap = DATA.actions_panel;
    if (!root) return;
    if (!ap) {
      root.innerHTML = '<p class="empty">No actions_panel in payload (regenerate dashboard).</p>';
      return;
    }
    if (ap.error) {
      intro.textContent = 'Could not build planning actions: ' + ap.error;
      root.innerHTML = '';
      return;
    }
    const al = ap.artifact_links || {};
    const planHref = al.planning_actions_from_dashboard;
    intro.innerHTML =
      'From <code>argus planning actions</code> (regenerated for this page). Planning bundle: ' +
      esc(ap.planning_bundle_generated_at_utc || '?') + '. ' +
      (planHref ? '<a href="' + esc(planHref) + '">runs/planning/actions.json</a>' : '');
    const pend = [].concat(ap.pending_approvals || []);
    const auto = [].concat(ap.auto_eligible || [], ap.approved_actions || []);
    const hist = ap.execution_history || [];
    const rej = ap.rejected_actions || [];
    let html = '';
    html += '<p class="mini">Pending = needs operator approval or an open <code>appr_</code> record. ' +
      'Auto = contract policy (safe / no gate). Approved = matching approved record in runs/approval/.</p>';
    html += actionsTable('Pending approvals', pend);
    html += actionsTable('Auto-eligible & human-approved (ready to run)', auto);
    if (rej.length) {
      html += actionsTable('Rejected (latest record)', rej);
    }
    if (hist.length) {
      html += '<h3>Execution history</h3><div class="wrap-table"><table><thead><tr>' +
        '<th>run</th><th>action_id</th><th>product</th><th>status</th><th>exit</th><th>command</th><th>started</th>' +
        '</tr></thead><tbody>';
      hist.forEach(r => { html += execHistoryRow(r); });
      html += '</tbody></table></div>';
    } else {
      html += '<h3>Execution history</h3><p class="empty">No runs under runs/execution/ yet.</p>';
    }
    root.innerHTML = html;
  }
  loadData();
  </script>
</body>
</html>
"""


def write_dashboard_html(
    repo_root: Path,
    out_path: Path | None = None,
    *,
    products_dir: Path | None = None,
    strict: bool = False,
) -> tuple[Path, dict[str, Any]]:
    """Build payload and write a self-contained HTML file.

    Returns ``(path, payload)`` so callers can inspect diagnostics (e.g. ``--strict`` exit code).
    """
    root = repo_root.resolve()
    payload = build_dashboard_payload(root, products_dir=products_dir, strict=strict)
    dest = out_path or (root / "runs" / "dashboard" / "index.html")
    dest.parent.mkdir(parents=True, exist_ok=True)
    html = dashboard_html_template()
    json_str = json.dumps(payload, ensure_ascii=False)
    json_str = json_str.replace("</", "<\\/")
    html = html.replace(
        '<script id="payload" type="application/json"></script>',
        '<script id="payload" type="application/json">' + json_str + "</script>",
        1,
    )
    dest.write_text(html, encoding="utf-8")
    return dest, payload
