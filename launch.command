#!/usr/bin/env bash
# Ripple launcher. Double-click in Finder, or run from a terminal.
#
#   ./launch.command          start the app and open a browser
#   ./launch.command --test   run the test suite instead
#
# Path-safe: resolves its own directory through symlinks and quotes every
# expansion, so it works from a path containing spaces and does not depend on
# the caller's working directory.

set -euo pipefail

script_source="${BASH_SOURCE[0]}"
while [ -L "$script_source" ]; do
  link_target="$(readlink "$script_source")"
  case "$link_target" in
    /*) script_source="$link_target" ;;
    *)  script_source="$(cd -P "$(dirname "$script_source")" && pwd)/$link_target" ;;
  esac
done
REPO_ROOT="$(cd -P "$(dirname "$script_source")" && pwd)"
cd "$REPO_ROOT"

VENV="$REPO_ROOT/tools/.venv"
PYTHON="$VENV/bin/python"
BASE_PORT="${RIPPLE_PORT:-8420}"

echo "Ripple  ·  $REPO_ROOT"
echo

if [ ! -x "$PYTHON" ]; then
  echo "Creating the virtual environment..."
  command -v python3 >/dev/null 2>&1 || {
    echo "python3 was not found on PATH. Install Python 3.11 or newer." >&2
    exit 1
  }
  python3 -m venv "$VENV"
fi

echo "Installing dependencies..."
"$PYTHON" -m pip install --quiet --upgrade pip
"$PYTHON" -m pip install --quiet -e ".[dev]"

if [ "${1:-}" = "--test" ]; then
  echo "Running the test suite..."
  echo
  exec "$PYTHON" -m pytest
fi

# Find a free port, starting at the default and trying up to twenty above it.
# An instance of Ripple already listening is reused rather than duplicated.
PORT=""
for offset in $(seq 0 20); do
  candidate=$((BASE_PORT + offset))
  holder="$(lsof -ti ":$candidate" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -z "$holder" ]; then
    PORT="$candidate"
    break
  fi
  if ps -p "$holder" -o command= 2>/dev/null | grep -q "ripple.web.app"; then
    echo "Ripple is already running on port $candidate. Opening it."
    open "http://127.0.0.1:$candidate" 2>/dev/null || true
    exit 0
  fi
done

if [ -z "$PORT" ]; then
  echo "No free port between $BASE_PORT and $((BASE_PORT + 20))." >&2
  exit 1
fi

echo
echo "Optional tools:"
for binary in tesseract pdftoppm; do
  if command -v "$binary" >/dev/null 2>&1; then
    echo "  $binary: present"
  else
    echo "  $binary: absent (scanned PDFs will be rejected rather than OCR'd)"
  fi
done

URL="http://127.0.0.1:$PORT"
echo
echo "Starting Ripple on $URL"
echo "Press Control-C to stop."
echo

# Open the browser once the server answers, rather than immediately, so the
# first page load is not a connection error.
(
  for _ in $(seq 1 40); do
    if curl -fsS -o /dev/null "$URL" 2>/dev/null; then
      open "$URL" 2>/dev/null || true
      exit 0
    fi
    sleep 0.25
  done
) &

exec "$PYTHON" -m uvicorn ripple.web.app:app --host 127.0.0.1 --port "$PORT" --log-level info
