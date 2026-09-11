#!/usr/bin/env bash
# Launch the Argus operator console (Streamlit) from the repo root.
# Usage:
#   ./tools/run_dashboard.sh
#   ARGUS_DASHBOARD_PORT=8501 ./tools/run_dashboard.sh
#   ARGUS_REPO_ROOT=/path/to/argus ./tools/run_dashboard.sh
set -euo pipefail
# Non-interactive environments (e.g. systemd --user) often omit ~/.local/bin; `uv` is usually there.
if ! command -v uv >/dev/null 2>&1; then
  export PATH="${HOME}/.local/bin:/usr/local/bin:${PATH:-/usr/bin:/bin}"
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ARGUS_REPO_ROOT="${ARGUS_REPO_ROOT:-$ROOT}"
cd "$ROOT"
PORT="${ARGUS_DASHBOARD_PORT:-8501}"
UV_EXE="${UV:-uv}"
exec "$UV_EXE" run --group dashboard streamlit run argus/dashboard/app.py \
  --server.port "$PORT" \
  --server.headless true \
  --browser.gatherUsageStats false \
  "$@"
