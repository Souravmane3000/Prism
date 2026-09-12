#!/usr/bin/env bash
# Idempotent repository bootstrap for the Prism Cloud Agent environment.
# Prepares the Python backend (editable install into a venv) and the
# Next.js frontend (npm ci). Safe to run repeatedly.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${PRISM_VENV:-$HOME/.venv}"

echo "[install] Repo root: $REPO_ROOT"

# The default image ships Python 3.12 but not the venv module; add it once.
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  echo "[install] Installing python3-venv"
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3-venv
fi

# ── Backend: editable install into a persistent virtualenv ────────────────────
if [ ! -x "$VENV/bin/python" ]; then
  echo "[install] Creating virtualenv at $VENV"
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --upgrade pip
pip install -e "$REPO_ROOT[dev]"

# ── Frontend: install locked dependencies ─────────────────────────────────────
cd "$REPO_ROOT/frontend"
npm ci

echo "[install] Done. Activate the backend venv with: source $VENV/bin/activate"
