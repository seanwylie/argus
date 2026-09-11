# Business snapshot fixtures

Example **local-only** JSON/CSV files for `argus signals ingest-snapshots`.

## Layout

```
fixtures/business_snapshots/<product_id>/*.json
```

Argus ingests these when:

- The product exists in inventory, and
- You have **not** placed a file with the **same basename** under `products/<id>/metrics/snapshots/` (product dir wins).

## Binding

Prefer explicit `"product_id"` in each JSON file. Otherwise the parent folder name (`<product_id>`) is used.

## Filename → adapter

See `argus signals snapshot-types`. Examples:

- `*posthog*` — PostHog-style pageviews trend
- `*google_analytics*` or `*ga4*` — GA sessions / conversion
- `*aws_cost*` — AWS-style monthly cost
- `*stripe*` — Stripe MRR trend
- `*mobile*` or `*app_metrics*` — DAU / retention
