# Local portfolio dashboard

Argus can generate a **single static HTML file** that summarizes the product ecosystem from **local artifacts only** (no network, no auth, no hosted service).

The embedded JSON payload uses schema **`argus.dashboard.v3`**: it includes current state, **ideas** from `runs/ideas/latest.json` (`argus.dashboard_ideas.v1`), plus **optional history** from `runs/history/snapshots/` and **trend summaries** from deterministic rules (`argus/trends`).

## Operator signals (visibility)

The payload surfaces **confidence / uncertainty / risk** when `decision_context` exists on decision bundles, **temporal** rollups when `runs/temporal/` is present, **ideas** from `runs/ideas/`, **autonomy tier** and policy hints where embedded, **blocked** execution posture from autonomy metadata, and **escalation** rows. Warnings in the generator prefer **structured gaps** (missing artifacts) over silent defaults—regenerate after `portfolio refresh` / `confidence assess` for full fidelity.

## Prerequisites

Generate data first so the dashboard has something to show:

1. `argus portfolio refresh` (or at least `argus findings generate` and `argus decisions generate` per product).
2. Optional: `argus signals collect` / `argus signals ingest-snapshots` for signal timestamps.
3. Optional: `argus escalation generate <product>` if you want escalation rows.
4. **Optional (history & trends):** `argus history snapshot` on a cadence, then `argus trends analyze` so `runs/trends/latest.json` exists when you want trend text.

## Launch

From the repository root:

```bash
uv run argus dashboard
```

This writes **`runs/dashboard/index.html`** (gitignored by default) and prints the path.

Open in a browser:

```bash
uv run argus dashboard --open
```

Or open the file manually (`file://` URL).

### Options

| Flag | Meaning |
|------|---------|
| `--out PATH` | Write HTML somewhere else |
| `--open` | Open the default browser on the generated file |
| `--products-dir DIR` | Same as other commands: alternate `products/` root |

## Using the UI

- **Overview table**: valid inventory products with cost, last signal time, finding counts, **ideas** / **invent** counts (from latest idea bundle), top recommended action, priority, confidence.
- **Ideas panel**: portfolio type distribution (exploit / explore / invent), **rejected duplicate** titles from bundle metadata, sample highlights for **invent** and **high risk/reward** rows, **portfolio-only** ideas (no `product_id`), diversity index.
- **Product detail**: table of ideas for that product with novelty, **diversity** (avg of novelty + adjacency), EV, confidence, risk/reward tier, **selection** (`selected` vs `rejected_duplicate` in the bundle), and rationale excerpts.
- **Trend columns** (when history exists): Δ findings, Δ cost, Δ priority, Δ escalation count, **#hist** (snapshots in which this product appears), and **trend / drift** tags from rules-based analysis.
- **History window**: choose **latest 3**, **latest 5**, or **all** snapshots; deltas and sparklines use the selected window.
- **Filters**: state, status, max monthly cost, minimum findings severity band, substring match on action/intent.
- **Sort**: dropdown or click a column header (including Δ columns).
- **Temporal** — overview meta and per-row **temporal** / **time context** columns; detail includes **temporal visibility** (signals vs findings vs decisions coherence, worst freshness bucket from `runs/temporal/latest/` when present). A **Temporal** panel lists recent findings whose kinds are time/recency-related. Regenerate after `argus signals collect` so sidecars stay aligned.
- **Detail** (row click):
  - `product.yaml` summary, lifecycle scores, escalations, truncated signals/findings/candidates JSON.
  - **Trend / drift** block (interpretation + drift signals when ≥2 history points).
  - **Sparklines** (inline SVG) for findings, cost, and priority over the selected window.
  - **Timeline table** per snapshot: counts, stage, top action; counts of lifecycle and top-action changes.
  - **Relative links** (from `runs/dashboard/index.html`) to history manifest, per-snapshot JSON, trends report, portfolio decisions, and decision generations directory.

Re-run `argus dashboard` after refreshing pipelines or capturing new history snapshots.

## Payload layout (v2)

| Key | Purpose |
|-----|--------|
| `history.snapshots_catalog` | Oldest-first list of snapshot ids + paths |
| `history.snapshots_count` | Number of stored snapshots |
| `history.window_presets` | `[3, 5, "all"]` for the UI |
| `artifact_links` | Repo-relative and dashboard-relative paths to manifests and reports |
| Per product `history_points` | Time series from stored snapshots |
| Per product `trend_summary` | Output of `analyze_product` when window ≥ 2 |

## Limitations

- Large JSON blobs are **truncated** in the detail panel for browser performance.
- Links are **relative** to the generated HTML file; they work when the repo tree is intact under `runs/`.
- With **no** `runs/history/` data, trend columns show zeros/— and trend detail explains how to capture history.

See also: [signals-and-adapters.md](signals-and-adapters.md), [decisions-and-lifecycle.md](decisions-and-lifecycle.md), [temporal-intelligence.md](temporal-intelligence.md), [history.md](history.md), [trends.md](trends.md).
