"""Perfil por projeto: `.sunhub.toml` na raiz do projeto escolhe quais fontes valem ali.

    # .sunhub.toml
    sources = ["eng-guidelines", "architecture-adrs"]   # só estas (aceita curingas: "time-a-*")
    exclude = ["rascunhos"]                              # opcional

Sem o arquivo, todas as fontes valem. O servidor MCP descobre o projeto pela pasta que o
editor informa (MCP roots), por SUNHUB_PROJECT ou pela pasta atual.
"""
from __future__ import annotations

import fnmatch
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from sunhub.config import Config

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

PROFILE_FILE = ".sunhub.toml"


@dataclass
class Profile:
    path: Path
    sources: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)

    @property
    def project(self) -> Path:
        return self.path.parent


def find(start: Path | str | None) -> Profile | None:
    if not start:
        return None
    try:
        p = Path(start).expanduser().resolve()
    except OSError:
        return None
    if p.is_file():
        p = p.parent
    home = Path.home().resolve()
    for d in [p, *p.parents]:
        f = d / PROFILE_FILE
        if f.is_file():
            try:
                data = tomllib.loads(f.read_text("utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                return None
            return Profile(f, [str(s) for s in data.get("sources") or []], [str(s) for s in data.get("exclude") or []])
        if d == home:
            break
    return None


def allowed(cfg: Config, profile: Profile | None) -> list[str] | None:
    """Nomes das fontes que valem; None = todas."""
    if profile is None:
        return None
    names = [s.name for s in cfg.sources]
    chosen = [n for n in names if any(fnmatch.fnmatch(n, pat) for pat in profile.sources)] if profile.sources else names
    return [n for n in chosen if not any(fnmatch.fnmatch(n, pat) for pat in profile.exclude)]


def for_cwd(cfg: Config, start: Path | str | None = None) -> tuple[Profile | None, list[str] | None]:
    start = start or os.environ.get("SUNHUB_PROJECT") or os.getcwd()
    prof = find(start)
    return prof, allowed(cfg, prof)


def write(project: Path, sources: list[str], exclude: list[str] | None = None) -> Path:
    f = Path(project).resolve() / PROFILE_FILE
    lines = ["# Fontes do Sunhub que valem neste projeto (nomes de `sunhub list`; aceita curingas).",
             f"sources = [{', '.join(repr(s).replace(chr(39), chr(34)) for s in sources)}]"]
    if exclude:
        lines.append(f"exclude = [{', '.join(repr(s).replace(chr(39), chr(34)) for s in exclude)}]")
    f.write_text("\n".join(lines) + "\n", "utf-8")
    return f
