#!/usr/bin/env bash
# Sunhub: prepara (na primeira vez) e abre o app.
#
#   source app            # ou: ./app
#   source app --install  # só instala/atualiza, sem abrir
#
# Funciona em bash e zsh. Tudo fica no seu $HOME; não precisa de sudo.

_sunhub_app() {
  local root; root="$(cd "$(dirname "$_sunhub_src")" && pwd)"
  local venv="$root/.venv" py="$root/.venv/bin/python" marker="$root/.venv/.sunhub-installed"
  local say='\033[1;33m→\033[0m' good='\033[1;32m✓\033[0m' bad='\033[1;31m✗\033[0m'

  if [ "$(uname -s)" != "Linux" ]; then printf "$bad %s\n" "Por enquanto o Sunhub roda só no Linux."; return 1; fi
  command -v git >/dev/null 2>&1 || { printf "$bad %s\n" "git não encontrado. Instale: sudo apt install git"; return 1; }
  command -v python3 >/dev/null 2>&1 || { printf "$bad %s\n" "python3 não encontrado. Instale: sudo apt install python3"; return 1; }
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || { printf "$bad %s\n" "Python 3.10+ é necessário ($(python3 --version))."; return 1; }
  export PATH="$HOME/.local/bin:$PATH"

  # Versões antigas instalavam via `uv tool`; o app agora usa o .venv deste clone.
  if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -q '^sunhub '; then
    printf "$say %s\n" "migrando a instalação antiga (uv tool) para o app"
    uv tool uninstall sunhub >/dev/null 2>&1 || true
  fi

  # venv quebrado (Python do sistema mudou) ou pasta renomeada/movida: recria o ambiente.
  # Os caminhos antigos ficam gravados no .venv; o `sunhub setup` depois reaponta atalho, MCP e timer.
  if [ -d "$venv" ]; then
    if ! "$py" -c 'import sys' >/dev/null 2>&1 \
       || { [ -f "$marker" ] && [ "$(cat "$marker" 2>/dev/null)" != "$root" ]; } \
       || { [ ! -f "$marker" ] && ! "$py" -c 'import sunhub' >/dev/null 2>&1; }; then
      printf "$say %s\n" "a pasta do app mudou de lugar; recriando o ambiente"
      rm -rf "$venv"
    fi
  fi

  if [ ! -x "$py" ] || [ ! -f "$marker" ] || [ "$root/pyproject.toml" -nt "$marker" ] \
     || ! "$py" -c 'import sunhub, PySide6' >/dev/null 2>&1; then
    printf "$say %s\n" "preparando o Sunhub (só na primeira vez; baixa ~100 MB)…"
    if command -v uv >/dev/null 2>&1; then
      [ -x "$py" ] || uv venv -q --python python3 "$venv" || return 1
      uv pip install -q --python "$py" -e "${root}[app]" || return 1
    elif python3 -m venv "$venv" >/dev/null 2>&1 && [ -x "$venv/bin/pip" ]; then
      "$venv/bin/pip" install -q --upgrade pip >/dev/null 2>&1 || true
      "$venv/bin/pip" install -q -e "${root}[app]" || return 1
    else
      rm -rf "$venv"
      printf "$say %s\n" "python3-venv indisponível; instalando o uv em ~/.local/bin"
      command -v curl >/dev/null 2>&1 || { printf "$bad %s\n" "curl não encontrado. Instale: sudo apt install curl"; return 1; }
      curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null || return 1
      uv venv -q --python python3 "$venv" && uv pip install -q --python "$py" -e "${root}[app]" || return 1
    fi
    printf '%s' "$root" >"$marker"
    "$venv/bin/sunhub" setup --quiet || return 1
    printf "$good %s\n" "instalado: comando 'sunhub' no terminal e 'Sunhub' no menu de aplicativos"
  fi

  case "${1:-}" in --install|-i) return 0 ;; esac

  if ! "$py" -c 'from PySide6.QtWidgets import QApplication' >/dev/null 2>&1; then
    printf "$bad %s\n" "faltam bibliotecas gráficas do sistema. No Ubuntu/Debian:"
    echo "    sudo apt install libegl1 libgl1 libxkbcommon-x11-0 libxcb-cursor0"
    return 1
  fi
  if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    printf "$bad %s\n" "nenhuma tela gráfica nesta sessão. O CLI continua disponível: sunhub --help"
    return 1
  fi

  mkdir -p "$HOME/.cache"
  local log="$HOME/.cache/sunhub-app.log" ready="$HOME/.cache/sunhub-app.ready" pidf="$HOME/.cache/sunhub-app.pid"
  rm -f "$ready" "$pidf"
  ( SUNHUB_READY_FILE="$ready" nohup "$venv/bin/sunhub" app >"$log" 2>&1 & echo $! >"$pidf" )
  local pid i=0; pid="$(cat "$pidf" 2>/dev/null)"
  while [ $i -lt 100 ]; do                       # espera a janela abrir (até ~20 s)
    [ -f "$ready" ] && break
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.2; i=$((i + 1))
  done
  if [ -f "$ready" ]; then
    printf "$good %s\n" "Sunhub aberto. Depois é só abrir pelo menu de aplicativos (ou: sunhub app)."
    return 0
  fi
  if kill -0 "$pid" 2>/dev/null; then
    printf "$say %s\n" "o Sunhub está demorando para abrir; log em $log"
    return 0
  fi
  printf "$bad %s\n" "a janela não abriu. Últimas linhas do log ($log):"
  tail -n 6 "$log" | sed 's/^/    /'
  if grep -q 'xcb' "$log" 2>/dev/null; then
    echo "  → No Ubuntu/Debian: sudo apt install libxcb-cursor0 libxkbcommon-x11-0"
  fi
  return 1
}

# caminho deste arquivo (fora da função: no zsh, $0 dentro de função é o nome da função)
_sunhub_src="${BASH_SOURCE[0]:-$0}"
if [ -n "${ZSH_VERSION:-}" ]; then
  case "$ZSH_EVAL_CONTEXT" in *:file*) _sunhub_sourced=1 ;; *) _sunhub_sourced=0 ;; esac
else
  [ "${BASH_SOURCE[0]:-$0}" != "$0" ] && _sunhub_sourced=1 || _sunhub_sourced=0
fi
_sunhub_app "$@"
_sunhub_rc=$?
unset -f _sunhub_app
unset _sunhub_src
if [ "$_sunhub_sourced" = 1 ]; then unset _sunhub_sourced; return $_sunhub_rc 2>/dev/null; fi
exit $_sunhub_rc
