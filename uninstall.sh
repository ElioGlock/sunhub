#!/usr/bin/env bash
# Desfaz tudo o que o Sunhub configurou (links de skills, MCP, timer, atalho, comando).
# Use --purge para apagar também o hub (~/sunhub com clones e índice).
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
if [ -x "$root/.venv/bin/sunhub" ]; then "$root/.venv/bin/sunhub" uninstall --yes "$@"
elif command -v sunhub >/dev/null; then sunhub uninstall --yes "$@"; fi
if command -v uv >/dev/null && uv tool list 2>/dev/null | grep -q '^sunhub '; then uv tool uninstall sunhub; fi
rm -f "$root/.venv/.sunhub-installed"
echo "✓ Sunhub removido do sistema. Para apagar o app, apague esta pasta: $root"
