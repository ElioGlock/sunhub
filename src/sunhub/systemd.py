"""Atualização automática com systemd --user (sem daemon próprio, sem sudo)."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from sunhub import paths, runtime

UNIT = "sunhub-sync"


from sunhub.errors import TimerError  # noqa: E402

NAME = "systemd do usuário"


def unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def available() -> bool:
    return shutil.which("systemctl") is not None


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def write_units(interval_minutes: int) -> bool:
    """Escreve as units; retorna True se algo mudou."""
    d = unit_dir()
    d.mkdir(parents=True, exist_ok=True)
    service = d / f"{UNIT}.service"
    timer = d / f"{UNIT}.timer"
    before = (service.read_text("utf-8") if service.exists() else "", timer.read_text("utf-8") if timer.exists() else "")
    service.write_text(
        "[Unit]\n"
        "Description=Sunhub: sincroniza os repositórios de conhecimento\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"Environment=SUNHUB_HOME={paths.home()}\n"
        f"ExecStart={runtime.shell_command('sync', '--quiet', background=True)}\n",
        "utf-8",
    )
    timer.write_text(
        "[Unit]\n"
        "Description=Sunhub: sync periódico\n\n"
        "[Timer]\n"
        "OnBootSec=2min\n"
        f"OnUnitActiveSec={max(1, int(interval_minutes))}min\n"
        "RandomizedDelaySec=60\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n",
        "utf-8",
    )
    return before != (service.read_text("utf-8"), timer.read_text("utf-8"))


def enable(interval_minutes: int) -> str:
    write_units(interval_minutes)
    if not available():
        raise TimerError("systemctl não encontrado; as units foram escritas, mas não ativadas")
    for args in (("daemon-reload",), ("enable", "--now", f"{UNIT}.timer"), ("restart", f"{UNIT}.timer")):
        p = _systemctl(*args)
        if p.returncode != 0:
            err = (p.stderr or p.stdout).strip() or "systemctl falhou"
            if "bus" in err.lower():
                err += ("\n→ o systemd --user não está acessível nesta sessão (comum via SSH/containers). "
                        "Rode `sunhub timer enable` num terminal da sua sessão gráfica, ou use `sunhub sync`.")
            raise TimerError(err)
    return f"sync automático a cada {interval_minutes} min"


def disable() -> str:
    if available():
        _systemctl("disable", "--now", f"{UNIT}.timer")
    for suffix in ("timer", "service"):
        (unit_dir() / f"{UNIT}.{suffix}").unlink(missing_ok=True)
    if available():
        _systemctl("daemon-reload")
    return "sync automático desativado"


def status() -> str:
    """'active' | 'inactive' | 'not-installed' | 'unavailable'"""
    if not (unit_dir() / f"{UNIT}.timer").exists():
        return "not-installed"
    if not available():
        return "unavailable"
    p = _systemctl("is-active", f"{UNIT}.timer")
    return "active" if p.stdout.strip() == "active" else "inactive"


_TS = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def _show(prop: str) -> str:
    p = _systemctl("show", f"{UNIT}.timer", "-p", prop, "--value")
    return p.stdout.strip() if p.returncode == 0 else ""


def _local(text: str) -> datetime | None:
    m = _TS.search(text or "")
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S") if m else None


def schedule(interval_minutes: int, last_sync: datetime | None = None) -> dict:
    """{'status', 'last', 'next'} com horários locais (sem fuso).

    O timer é relativo à última execução (OnUnitActiveSec), então o systemd nem sempre informa um
    horário absoluto: nesse caso, próxima = última execução + intervalo.
    """
    info: dict = {"status": status(), "last": None, "next": None}
    if info["status"] != "active":
        return info
    last = _local(_show("LastTriggerUSec"))
    if last is None and last_sync is not None:
        last = last_sync.astimezone().replace(tzinfo=None)
    nxt = _local(_show("NextElapseUSecRealtime"))
    if nxt is None and last is not None:
        nxt = last + timedelta(minutes=interval_minutes)
    info.update(last=last, next=nxt)
    return info
