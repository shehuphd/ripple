#!/usr/bin/env bash
# Ripple launcher. Double-click in Finder, or run from a terminal.
#
# Path-safe: resolves its own directory through symlinks and quotes every
# expansion, so it works from a path containing spaces, and does not depend on
# the caller's working directory.

set -euo pipefail

# Resolve this script's real directory, following symlinks.
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

echo "Ripple  ·  $REPO_ROOT"
echo

if [ ! -x "$PYTHON" ]; then
  echo "Creating the virtual environment..."
  if command -v python3 >/dev/null 2>&1; then
    python3 -m venv "$VENV"
  else
    echo "python3 was not found on PATH. Install Python 3.11 or newer." >&2
    exit 1
  fi
fi

echo "Installing dependencies..."
"$PYTHON" -m pip install --quiet --upgrade pip
"$PYTHON" -m pip install --quiet -e ".[dev]"

echo "Running the test suite..."
echo
"$PYTHON" -m pytest

echo
echo "Optional tools:"
for binary in tesseract pdftoppm; do
  if command -v "$binary" >/dev/null 2>&1; then
    echo "  $binary: present"
  else
    echo "  $binary: absent (scanned PDFs will be rejected rather than OCR'd)"
  fi
done

echo
echo "Re-render the demo corpus:"
echo "  \"$PYTHON\" tools/render_screenplay.py"

# Keep the window open when launched by double-click from Finder.
if [ -t 1 ] && [ "${TERM_PROGRAM:-}" = "Apple_Terminal" ]; then
  echo
  read -r -p "Press Return to close." _
fi
