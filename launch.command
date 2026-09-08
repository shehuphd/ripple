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

VENV="$REPO_ROOT/.venv"
PYTHON="$VENV/bin/python"
BASE_PORT="${RIPPLE_PORT:-8420}"

# Two ways to run: a desktop and a host. A host (Replit, Render, Fly) names
# the one port it proxies in PORT and reaches the app across the container
# boundary, so that run takes the port as given, binds every interface, and
# wants neither a browser nor the reloader. A desktop sets neither variable
# and keeps the behaviour it always had: loopback, a scanned port, a browser,
# and reload on edit. RIPPLE_HOST overrides the bind address on its own.
SERVED_PORT="${PORT:-}"
if [ -n "$SERVED_PORT" ]; then
  HOST="${RIPPLE_HOST:-0.0.0.0}"
else
  HOST="${RIPPLE_HOST:-127.0.0.1}"
fi
case "$HOST" in
  127.0.0.1|localhost|::1) HOST_IS_LOOPBACK="yes" ;;
  *) HOST_IS_LOOPBACK="" ;;
esac

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

# The project needs Python 3.11 or newer. A candidate is judged by running
# it, not by its name existing: a version manager's shim can be present and
# still refuse to execute.
meets_floor() {
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    >/dev/null 2>&1
}

# The venv, when it exists and meets the floor, is the only interpreter this
# script needs; the search below runs only to create or rebuild it. Plain
# `python3` is tried after the version-named binaries, because a version
# manager's pin can hold it below 3.11 while newer interpreters are present
# and working elsewhere (a pyenv install, a python.org framework build), and
# a launcher that trusts PATH's python3 alone refuses machines that can run
# the app.
if ! meets_floor "$PYTHON"; then
  SYSTEM_PYTHON=""
  for candidate in \
    python3.13 python3.12 python3.11 python3 \
    /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
    /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3 \
    /usr/local/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/*/bin/python3; do
    if meets_floor "$candidate"; then
      SYSTEM_PYTHON="$candidate"
      break
    fi
  done
  if [ -z "$SYSTEM_PYTHON" ]; then
    echo "No Python 3.11 or newer was found on this machine." >&2
    echo "python3 here answers as: $(python3 -V 2>&1 || true)" >&2
    echo "Install a newer Python from https://www.python.org/downloads/ and" >&2
    echo "run this script again." >&2
    exit 1
  fi
  echo "Creating the virtual environment with $SYSTEM_PYTHON..."
  rm -rf "$VENV"
  "$SYSTEM_PYTHON" -m venv "$VENV"
  "$PYTHON" -m ensurepip --upgrade >/dev/null 2>&1 || true
fi

# Keep sync clients away from compiled extensions and the credential file.
# Dropbox churn invalidates .so code signatures on macOS, and the secrets
# file must not replicate off this machine. Setting the attribute is
# idempotent, so it runs on every launch rather than only on creation.
if command -v xattr >/dev/null 2>&1; then
  for synced_path in "$VENV" "$REPO_ROOT/data"; do
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

PORT=""
if [ -n "$SERVED_PORT" ]; then
  # A host proxies exactly one port, so scanning past it would publish a
  # server nothing routes to. Take it as given, and let the bind fail loudly
  # if something already holds it.
  PORT="$SERVED_PORT"
else
  # Find a free port, starting at the default and trying up to twenty above
  # it. The kill above normally clears any same-app holder; one found here
  # anyway (a race, a kill that did not take) is killed and its port taken,
  # never reused, so a stale instance cannot keep serving unnoticed.
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
if [ -n "$HOST_IS_LOOPBACK" ]; then
  echo "Starting Ripple on $URL"
else
  echo "Starting Ripple on $HOST:$PORT"
fi
echo "Press Control-C to stop."
echo

# Open the browser once the server answers, rather than immediately, so the
# first page load is not a connection error. A run bound past loopback is
# being served to someone else's browser, so there is none to open here.
if [ -n "$HOST_IS_LOOPBACK" ]; then
  (
    for _ in $(seq 1 40); do
      if curl -fsS -o /dev/null "$URL" 2>/dev/null; then
        open "$URL" 2>/dev/null || true
        exit 0
      fi
      sleep 0.25
    done
  ) &
fi

# --reload restarts the server when a Python file changes, so an edit shows
# up on the next page load. Templates and static assets need no restart at
# all: templates re-read on render, and every response is sent no-store.
# A host runs code that no one is editing, and the reloader's file watching
# costs it memory for nothing, so that run goes without.
if [ -n "$SERVED_PORT" ]; then
  exec "$PYTHON" -m uvicorn ripple.web.app:app --host "$HOST" --port "$PORT" \
    --log-level info
fi
exec "$PYTHON" -m uvicorn ripple.web.app:app --host "$HOST" --port "$PORT" \
  --log-level info --reload --reload-dir "$REPO_ROOT/ripple"
