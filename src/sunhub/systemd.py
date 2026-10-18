"""Atualização automática com systemd --user (sem daemon próprio, sem sudo)."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from sunhub import paths

UNIT = "sunhub-sync"


class TimerError(RuntimeError):
    pass


def unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def available() -> bool:
    return shutil.which("systemctl") is not None


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def write_units(interval_minutes: int) -> tuple[Path, Path]:
    d = unit_dir()
    d.mkdir(parents=True, exist_ok=True)
    service = d / f"{UNIT}.service"
    timer = d / f"{UNIT}.timer"
    service.write_text(
        "[Unit]\n"
        "Description=Sunhub: sincroniza os repositórios de conhecimento\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"Environment=SUNHUB_HOME={paths.home()}\n"
        f"ExecStart={sys.executable} -m sunhub sync --quiet\n",
        "utf-8",
    )
    timer.write_text(
        "[Unit]\n"
        "Description=Sunhub: sync periódico\n\n"
        "[Timer]\n"
        "OnBootSec=2min\n"
        f"OnUnitActiveSec={max(5, int(interval_minutes))}min\n"
        "RandomizedDelaySec=60\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n",
        "utf-8",
    )
    return service, timer


def enable(interval_minutes: int) -> str:
    write_units(interval_minutes)
    if not available():
        raise TimerError("systemctl não encontrado; as units foram escritas, mas não ativadas")
    for args in (("daemon-reload",), ("enable", "--now", f"{UNIT}.timer")):
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
