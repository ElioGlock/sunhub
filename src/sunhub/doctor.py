"""Diagnóstico: o que está ok, o que falta, o que quebrou."""
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sunhub import clients, git, linker, paths, state, systemd
from sunhub.config import Config
from sunhub.store import Store


@dataclass
class Check:
    level: str  # ok | warn | error
    text: str

    @property
    def sign(self) -> str:
        return {"ok": "✓", "warn": "!", "error": "✗"}[self.level]


def run(cfg: Config, store: Store) -> list[Check]:
    out: list[Check] = []
    add = lambda level, text: out.append(Check(level, text))  # noqa: E731

    add("ok" if sys.version_info >= (3, 10) else "error", f"Python {sys.version.split()[0]}")
    v = git.version()
    add("ok" if v else "error", f"git {v}" if v else "git não encontrado (sudo apt install git)")
    add("ok" if paths.home().is_dir() else "error", f"hub em {paths.home()}")
    add("ok" if shutil.which("sunhub") else "warn",
        "comando `sunhub` no PATH" if shutil.which("sunhub") else "`sunhub` fora do PATH (abra um novo terminal ou rode `source app --install`)")

    if not cfg.sources:
        add("warn", "nenhuma fonte vinculada (sunhub add <url> ou sunhub gui)")
    for s in cfg.sources:
        st = store.get_state(s.name) or {}
        n = store.count(s.name)
        if not s.enabled:
            add("warn", f"{s.name}: desativada")
        elif st.get("last_error"):
            add("error", f"{s.name}: {st['last_error'].splitlines()[0]}")
        elif not s.checkout_dir().exists():
            add("warn", f"{s.name}: ainda não clonada (sunhub sync)")
        else:
            age = ""
            if st.get("last_sync"):
                mins = int((datetime.now(timezone.utc) - datetime.fromisoformat(st["last_sync"])).total_seconds() // 60)
                age = f", sync há {mins} min"
            add("ok", f"{s.name}: {n} documentos{age}")

    links = state.load()["links"]
    broken = [e["path"] for e in links if not Path(e["path"]).exists()]
    targets = ", ".join(f"~/{linker.TARGET_DIRS[t]}" for t in cfg.settings.link_targets if t in linker.TARGET_DIRS)
    names = {Path(e["path"]).name for e in links}
    add("error" if broken else "ok", f"{len(names)} skills publicadas em {targets or 'nenhuma pasta'}"
        + (f"; {len(broken)} links quebrados (rode sunhub link)" if broken else ""))

    for c in clients.CLIENTS:
        if c.detected() or clients.installed(c):
            ok = clients.installed(c)
            add("ok" if ok else "warn", f"{c.label}: MCP {'registrado' if ok else 'não registrado (sunhub install-mcp ' + c.key + ')'}")

    ts = systemd.status()
    add("ok" if ts == "active" else "warn", {
        "active": f"sync automático ativo (a cada {cfg.settings.sync_interval_minutes} min)",
        "inactive": "timer instalado mas inativo (sunhub timer enable)",
        "not-installed": "sync automático desligado (sunhub timer enable)",
        "unavailable": "systemd --user indisponível; use `sunhub sync` manualmente",
    }[ts])
    return out
