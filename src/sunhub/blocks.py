"""Blocos marcados em arquivos do usuário (~/.profile, AGENTS.md...). Editar só dentro deles."""
from __future__ import annotations

import re
from pathlib import Path

from sunhub import state

SHELL = ("# >>> sunhub >>>", "# <<< sunhub <<<")
MARKDOWN = ("<!-- >>> sunhub >>> -->", "<!-- <<< sunhub <<< -->")


def _pattern(markers: tuple[str, str]) -> re.Pattern:
    return re.compile(rf"\n?{re.escape(markers[0])}.*?{re.escape(markers[1])}\n?", re.S)


def upsert(file: Path, body: str, markers: tuple[str, str] = SHELL, track: bool = True) -> None:
    file = Path(file).expanduser()
    text = file.read_text("utf-8") if file.exists() else ""
    block = f"{markers[0]}\n{body.strip()}\n{markers[1]}\n"
    pat = _pattern(markers)
    if pat.search(text):
        text = pat.sub(lambda m: block if m.start() == 0 else "\n" + block, text, count=1)
    else:
        text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + block
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, "utf-8")
    if track:
        state.remember("blocks", {"file": str(file), "markers": list(markers)})


def remove(file: Path, markers: tuple[str, str] = SHELL) -> bool:
    file = Path(file).expanduser()
    if not file.exists():
        return False
    text = file.read_text("utf-8")
    new = _pattern(markers).sub("\n", text)
    if new == text:
        return False
    file.write_text(new.strip("\n") + ("\n" if new.strip() else ""), "utf-8")
    state.forget("blocks", {"file": str(file), "markers": list(markers)})
    return True


def has(file: Path, markers: tuple[str, str] = SHELL) -> bool:
    file = Path(file).expanduser()
    return file.exists() and markers[0] in file.read_text("utf-8")
