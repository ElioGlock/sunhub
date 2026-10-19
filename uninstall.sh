#!/usr/bin/env bash
# Remove do computador tudo o que o Sunhub criou: links de skills, MCP, timer, atalho, comando,
# blocos no ~/.profile, a pasta ~/sunhub e o ambiente .venv deste clone.
#   ./uninstall.sh              # remove tudo (pede confirmação)
#   ./uninstall.sh --keep-data  # mantém ~/sunhub (configuração, clones e índice)
#   ./uninstall.sh --yes        # sem confirmação
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
if [ -x "$root/.venv/bin/sunhub" ]; then "$root/.venv/bin/sunhub" uninstall "$@"
elif command -v sunhub >/dev/null; then sunhub uninstall "$@"
else echo "comando sunhub não encontrado; nada a desfazer no sistema"; fi
if command -v uv >/dev/null && uv tool list 2>/dev/null | grep -q '^sunhub '; then uv tool uninstall sunhub; fi
rm -rf "$root/.venv"
echo "✓ Sunhub removido. Sobrou só esta pasta com o código: $root (apague se quiser)"
