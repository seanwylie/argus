# Cursor audit scan JSON contract

Argus validates Cursor-produced audit scan payloads on **`argus audit ingest-agent`**. The machine schema id is **`argus.audit_cursor_scan.v1`**.

## Batch file

Top-level object:

| Field | Type | Required |
|-------|------|----------|
| `schema` | string | Yes, must be `argus.audit_cursor_scan_batch.v1` |
| `angles` | object | Yes; keys are angle ids (`product_gap`, `cost`, …); values are `argus.audit_cursor_scan.v1` objects |

Legacy: `argus.audit_agent_batch.v1` is still accepted and normalized into the same contract.

## Single-angle file

For one angle, the ingest file may be **only** a `argus.audit_cursor_scan.v1` object (no `angles` wrapper). Use `argus audit ingest-agent --product-id <id> --angle <angle_id> --file <path>`. Prompts for a single angle are emitted with `argus audit prompt --product-id <id> --angle <angle_id>` (see `argus/audit/agent_prompt.py`: `build_cursor_scan_prompt_for_angle`).

## Per-angle object (`argus.audit_cursor_scan.v1`)

| Field | Type | Required | Notes |
|-------|------|----------|--------|
| `schema` | string | Yes | Must be `argus.audit_cursor_scan.v1` |
| `angle_id` | string | Yes | Must equal the key in `angles` |
| `summary_lines` | string[] | Yes | ≥1 non-empty line |
| `findings` | object[] | Yes | Each: `title`, `detail`, `severity` (`info`\|`warn`\|`fail`), `evidence_refs` (string[]) |
| `risks` | object[] or string[] | Yes | Objects: `statement`, `evidence_refs`. Or non-empty strings (normalized to objects on ingest). |
| `enhancements` | object[] | Yes | Same shape as `findings` |
| `confidence` | number | Yes | `0`–`1` inclusive |
| `repo_evidence_refs` | string[] | Yes | Repo-relative paths (may be empty) |
| `limitations` | string[] | Yes | May be empty |
| `provenance` | string | Yes | Must be exactly `cursor_codebase_scan` |
| `notes` | string[] | No | |

Validation errors reference the field path (e.g. `angle cost: cursor_scan.angle_id mismatch`).

## Storage in `bundle.json`

The validated object is stored under **`bundle.angles.<id>.cursor_scan`**. Deterministic runner output remains on the same angle object; **`sources`** indicates which layers are present.
