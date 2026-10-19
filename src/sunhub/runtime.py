"""Como chamar o próprio Sunhub (venv do app, uv tool ou AppImage)."""
from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path


def appimage() -> str | None:
    """Caminho do .AppImage quando o Sunhub roda empacotado (o AppImage define APPIMAGE)."""
    p = os.environ.get("APPIMAGE")
    return p if p and Path(p).is_file() else None


def self_command() -> list[str]:
    """Comando estável para rodar `sunhub <args>` (MCP, timer, atalho)."""
    img = appimage()
    if img:
        return [img]
    return [sys.executable, "-m", "sunhub"]


def shell_command(*args: str) -> str:
    return " ".join(shlex.quote(a) for a in [*self_command(), *args])


def cli_entry() -> Path | None:
    """Executável `sunhub` deste ambiente, quando existe (venv/uv tool)."""
    exe = Path(sys.executable).parent / "sunhub"
    return exe if exe.exists() else None
