#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
PLATFORM_MARKER_FILE="$FRONTEND_DIR/.platform-install"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="${PYTHON_BIN:-python}"
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "[ERROR] Python 3 was not found. Install Python 3.10+ and retry."
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "[ERROR] Node.js was not found. Install Node.js 18+ and retry."
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "[ERROR] npm was not found. Install npm and retry."
  exit 1
fi

PLATFORM_MARKER="linux-$(uname -m)-node-$(node -v)-npm-$(npm -v)"

install_frontend_deps() {
  cd "$FRONTEND_DIR"

  local expected_rollup_pkg=""
  case "$(uname -m)" in
    x86_64) expected_rollup_pkg="@rollup/rollup-linux-x64-gnu" ;;
    aarch64|arm64) expected_rollup_pkg="@rollup/rollup-linux-arm64-gnu" ;;
  esac

  local previous_marker=""
  if [[ -f "$PLATFORM_MARKER_FILE" ]]; then
    previous_marker="$(cat "$PLATFORM_MARKER_FILE" 2>/dev/null || true)"
  fi

  if [[ "$previous_marker" != "$PLATFORM_MARKER" ]] && [[ -d node_modules ]]; then
    echo "Detected frontend dependencies from a different platform/runtime."
    echo "Removing frontend/node_modules for a clean Linux install..."
    rm -rf node_modules
  fi

  npm install --include=optional

  if [[ -n "$expected_rollup_pkg" ]] && [[ ! -d "node_modules/${expected_rollup_pkg}" ]]; then
    echo "Missing Linux Rollup native package after install. Retrying with a clean frontend reinstall..."
    rm -rf node_modules package-lock.json
    npm install --include=optional
  fi

  printf '%s\n' "$PLATFORM_MARKER" > "$PLATFORM_MARKER_FILE"
}

echo "============================================"
echo "  AI Browser Agent - Linux Setup & Run"
echo "============================================"
echo

echo "[1/4] Installing Python dependencies..."
cd "$BACKEND_DIR"
"$PYTHON_BIN" -m pip install -r requirements.txt

echo
echo "[2/4] Installing Playwright browsers..."
"$PYTHON_BIN" -m playwright install chromium

echo
echo "[3/4] Installing frontend dependencies..."
install_frontend_deps

echo
echo "[4/4] Starting services..."
cd "$BACKEND_DIR"
"$PYTHON_BIN" agent.py &
BACKEND_PID=$!

cleanup() {
  kill "${BACKEND_PID:-}" "${FRONTEND_PID:-}" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

sleep 3

cd "$FRONTEND_DIR"
npm run dev &
FRONTEND_PID=$!

sleep 4

if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://localhost:3000" >/dev/null 2>&1 || true
fi

echo
echo "Backend PID : $BACKEND_PID"
echo "Frontend PID: $FRONTEND_PID"
echo "Open http://localhost:3000 if it did not open automatically."
echo

wait "$BACKEND_PID" "$FRONTEND_PID"
