# Analytics integrations (snapshot-first)

Argus ingests **local files** under each product’s metrics tree (typically `products/<id>/metrics/snapshots/`). Nothing calls vendor APIs by default; “live” wiring is your ETL or scheduled export that drops JSON/CSV here.

## Adapters and filename hints

| Area | Snapshot filename contains | Adapter id | Notes |
| --- | --- | --- | --- |
| Product analytics | `posthog` | `posthog_snapshot` | Pageviews / funnels; export from PostHog or proxy. |
| Web analytics | `google_analytics` or `ga4` | `google_analytics_snapshot` | GA4-style JSON/CSV exports. |
| Mobile / app | `mobile` or `app_metrics` | `mobile_app_snapshot` | Store or in-app metrics dumps. |
| Content / channels | `content_platform`, `channel_metrics`, `youtube`, `substack` | `content_platform_snapshot` | Subscribers, views, platform field optional. |
| Revenue | `stripe` | `stripe_revenue_snapshot` | MRR / subscription snapshots when present. |
| Infra cost | `aws_cost` or `aws-cost` | `aws_cost_snapshot` | Cost lines for economics + resource synthesis. |

**First substring match wins** (see `argus/signals/snapshots/registry.py`). Name files distinctly so the right parser runs.

## Product binding

Snapshots resolve `product_id` via path under `products/<id>/…` and optional `product_id` in JSON (see `argus/signals/snapshots/binding.py`). Keeping one product per tree avoids cross-wiring.

## Live integration (out of band)

1. **PostHog / GA / Stripe / YouTube / Substack**: use each vendor’s export API or dashboard export on a schedule; write files into `metrics/snapshots/` with stable naming.
2. **Secrets and API keys** never belong in Argus config for ingestion — use your runner’s environment and write **files** the repo can read, or use capability requests if you later add authenticated collectors.
3. **Tests** use fixture JSON under `tests/fixtures/` or ephemeral temp files; no network in CI.

## Capability requests

Automated pulls, webhooks, or account-level setup stay behind **capability requests** (`runs/capabilities/requests/`) until explicitly approved — see `docs/capabilities.md`.
