#!/usr/bin/env bash
# Launch `argus portfolio run-service` from the repo root (systemd-friendly).
# Same `uv` / PATH behavior as `tools/run_dashboard.sh`.
#
# Usage:
#   ./tools/run_portfolio_runner.sh --interval-seconds 3600 --max-runs 100000
#   UV=/full/path/to/uv ./tools/run_portfolio_runner.sh ...
set -euo pipefail
if ! command -v uv >/dev/null 2>&1; then
  export PATH="${HOME}/.local/bin:/usr/local/bin:${PATH:-/usr/bin:/bin}"
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ARGUS_REPO_ROOT="${ARGUS_REPO_ROOT:-$ROOT}"
cd "$ROOT"
UV_EXE="${UV:-uv}"
exec "$UV_EXE" run argus portfolio run-service "$@"
