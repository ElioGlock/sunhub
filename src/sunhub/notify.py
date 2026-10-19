"""Resumo das novidades de um sync e notificação do sistema (notify-send)."""
from __future__ import annotations

import shutil
import subprocess

LABEL = {"skill": ("skill", "a"), "adr": ("ADR", "o"), "guideline": ("guideline", "a"),
         "antipattern": ("antipattern", "o"), "doc": ("documento", "o")}


def describe(c: dict) -> str:
    """Uma linha legível para uma novidade."""
    ref = c.get("ref") or c.get("uid") or ""
    kind = c.get("kind") or ""
    if kind and ref.startswith(kind + ":"):
        ref = ref[len(kind) + 1:]
    title = c.get("title") or ""
    what, g = LABEL.get(kind, ("documento", "o"))
    tail = f" — {title}" if title and title != ref else ""
    change = c["change"]
    if change == "source_added":
        return f"fonte {c['source']} vinculada ({c.get('detail')})"
    if change == "added":
        return f"{what} nov{g}: {ref}{tail}"
    if change == "removed":
        return f"{what} removid{g}: {ref}{tail}"
    if change == "status":
        return f"{ref} mudou de status: {c.get('detail')}"
    return f"{ref} atualizad{g}{tail}"


def summarize(changes: list[dict], limit: int = 5) -> str | None:
    """Texto curto com o que importa (novos, removidos, mudanças de status), sem listar tudo."""
    important = [c for c in changes if c["change"] in ("added", "removed", "status", "source_added")]
    modified = [c for c in changes if c["change"] == "modified"]
    if not important and not modified:
        return None
    lines = [describe(c) for c in important[:limit]]
    if len(important) > limit:
        lines.append(f"… e mais {len(important) - limit} novidade(s)")
    if modified:
        lines.append(f"{len(modified)} documento(s) atualizado(s)")
    return "\n".join(lines)


def send(title: str, body: str) -> bool:
    if not shutil.which("notify-send"):
        return False
    try:
        subprocess.run(["notify-send", "--app-name=Sunhub", "--icon=sunhub", title, body],
                       capture_output=True, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
