#!/usr/bin/env bash
# Ripple launcher. Double-click in Finder, or run from a terminal.
#
#   ./launch.command                 start the app and open a browser
#   ./launch.command --test [args]   run the test suite instead, passing any
#                                    further arguments to pytest
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

# Stop any instance already running, before anything else. A leftover server
# from an earlier session keeps serving stale code indefinitely; replacing it
# is the only way to be sure the code on disk is the code being served. The
# match is on the server's own command line, not on a port, so a process
# started by hand on another port is found too. SIGTERM first, a short grace
# period, then SIGKILL for stragglers.
running="$(pgrep -f "uvicorn ripple\.web\.app" 2>/dev/null || true)"
if [ -n "$running" ]; then
  echo "Stopping the running Ripple instance..."
  for pid in $running; do
    kill "$pid" 2>/dev/null || true
  done
  for _ in $(seq 1 20); do
    pgrep -f "uvicorn ripple\.web\.app" >/dev/null 2>&1 || break
    sleep 0.25
  done
  for pid in $(pgrep -f "uvicorn ripple\.web\.app" 2>/dev/null || true); do
    kill -9 "$pid" 2>/dev/null || true
  done
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 was not found on PATH. Install Python 3.11 or newer from" >&2
  echo "https://www.python.org/downloads/ and run this script again." >&2
  exit 1
fi

# The project needs 3.11+; saying so here beats a syntax error later.
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Ripple needs Python 3.11 or newer; python3 here is $(python3 -V 2>&1)." >&2
  echo "Install a newer Python from https://www.python.org/downloads/ and" >&2
  echo "run this script again." >&2
  exit 1
fi

# An existing venv is validated by running its interpreter, not by the
# directory existing: a half-created or broken venv would otherwise be
# trusted forever.
if ! "$PYTHON" -c 'import sys' >/dev/null 2>&1; then
  echo "Creating the virtual environment..."
  rm -rf "$VENV"
  python3 -m venv "$VENV"
  "$PYTHON" -m ensurepip --upgrade >/dev/null 2>&1 || true
fi

# Keep sync clients away from compiled extensions and the credential file.
# Dropbox churn invalidates .so code signatures on macOS, and the secrets
# file must not replicate off this machine. Setting the attribute is
# idempotent, so it runs on every launch rather than only on creation.
if command -v xattr >/dev/null 2>&1; then
  for synced_path in "$VENV" "$REPO_ROOT/.venv" "$REPO_ROOT/data"; do
    if [ -e "$synced_path" ]; then
      xattr -w com.dropbox.ignored 1 "$synced_path" 2>/dev/null || true
    fi
  done
fi

echo "Installing dependencies..."
"$PYTHON" -m pip install --quiet --upgrade pip
"$PYTHON" -m pip install --quiet -e ".[dev]"

if [ "${1:-}" = "--test" ]; then
  shift
  echo "Running the test suite..."
  echo
  exec "$PYTHON" -m pytest "$@"
fi

# Find a free port, starting at the default and trying up to twenty above it.
# The kill above normally clears any same-app holder; one found here anyway
# (a race, a kill that did not take) is killed and its port taken, never
# reused, so a stale instance cannot keep serving unnoticed.
PORT=""
for offset in $(seq 0 20); do
  candidate=$((BASE_PORT + offset))
  holders="$(lsof -ti ":$candidate" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -z "$holders" ]; then
    PORT="$candidate"
    break
  fi
  same_app=""
  for pid in $holders; do
    if ps -p "$pid" -o command= 2>/dev/null | grep -q "ripple\.web\.app"; then
      same_app="yes"
      kill "$pid" 2>/dev/null || true
    fi
  done
  if [ -n "$same_app" ]; then
    sleep 1
    for pid in $(lsof -ti ":$candidate" -sTCP:LISTEN 2>/dev/null || true); do
      kill -9 "$pid" 2>/dev/null || true
    done
    PORT="$candidate"
    break
  fi
done

if [ -z "$PORT" ]; then
  echo "No free port between $BASE_PORT and $((BASE_PORT + 20))." >&2
  echo "Close whatever holds those ports, or set RIPPLE_PORT to another" >&2
  echo "starting port, then run this script again." >&2
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

# --reload restarts the server when a Python file changes, so an edit shows
# up on the next page load. Templates and static assets need no restart at
# all: templates re-read on render, and every response is sent no-store.
exec "$PYTHON" -m uvicorn ripple.web.app:app --host 127.0.0.1 --port "$PORT" \
  --log-level info --reload --reload-dir "$REPO_ROOT/ripple"
