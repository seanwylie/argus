#!/usr/bin/env bash
# Fast-fail checks for docs/proof-run.md (repository root = parent of this script's directory).
set -euo pipefail

: "${PRODUCT_ID:?set PRODUCT_ID}"

for cmd in jq uv; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "proof-run preflight: missing required command: ${cmd}" >&2
    exit 1
  fi
done

if [[ "${ARGUS_CONTEXT_PACKETS:-0}" != "1" ]]; then
  echo "proof-run preflight: export ARGUS_CONTEXT_PACKETS=1 (required for proof context packets)" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f "products/${PRODUCT_ID}/product.yaml" ]]; then
  echo "proof-run preflight: missing products/${PRODUCT_ID}/product.yaml" >&2
  exit 1
fi

if ! uv run argus products show "$PRODUCT_ID" >/dev/null 2>&1; then
  echo "proof-run preflight: argus products show failed for ${PRODUCT_ID}" >&2
  exit 1
fi

echo "proof-run preflight: ok (PRODUCT_ID=${PRODUCT_ID} repo=${ROOT})"
