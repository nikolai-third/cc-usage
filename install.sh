#!/usr/bin/env bash
# cc-usage installer.
#
#   curl -fsSL https://raw.githubusercontent.com/nikolai-third/cc-usage/main/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/nikolai-third/cc-usage/main/install.sh | bash -s -- --uninstall
#
# Options (install): --no-statusline  --no-hook  --no-claude-md
set -euo pipefail

REPO="nikolai-third/cc-usage"
REF="${CC_USAGE_REF:-main}"

case "$(uname -s)" in
  Darwin|Linux) ;;
  *) echo "cc-usage supports macOS and Linux only." >&2; exit 1 ;;
esac

for tool in curl tar; do
  command -v "$tool" >/dev/null || { echo "cc-usage needs $tool." >&2; exit 1; }
done

PYTHON="$(command -v python3 || true)"
if [ -z "$PYTHON" ] || ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null; then
  echo "cc-usage needs Python 3.8+ (python3 on PATH)." >&2
  exit 1
fi

# Run from a local checkout if there is one, otherwise download the sources.
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "$(dirname "${BASH_SOURCE[0]}")/src/configure.py" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
if [ -z "$SCRIPT_DIR" ]; then
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  curl -fsSL "https://codeload.github.com/$REPO/tar.gz/$REF" | tar -xz -C "$TMP" --strip-components=1
  SCRIPT_DIR="$TMP"
fi

if [ "${1:-}" = "--uninstall" ]; then
  "$PYTHON" "$SCRIPT_DIR/src/configure.py" uninstall; exit
fi
"$PYTHON" "$SCRIPT_DIR/src/configure.py" install "$@"
