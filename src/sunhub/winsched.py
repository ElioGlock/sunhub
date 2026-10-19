"""Atualização automática no Windows: Agendador de Tarefas (sem administrador, sem janela)."""
from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from xml.sax.saxutils import escape

from sunhub import osutil, paths, runtime
from sunhub.errors import TimerError

TASK = r"\Sunhub\Sync"
NAME = "Agendador de Tarefas do Windows"


def available() -> bool:
    return shutil.which("schtasks") is not None


def _xml_file():
    return paths.state_dir() / "sunhub-sync.xml"


def _definition(interval_minutes: int) -> str:
    cmd = runtime.self_command(background=True)
    import os  # noqa: PLC0415

    user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".strip("\\")
    start = (datetime.now() + timedelta(minutes=1)).replace(microsecond=0).isoformat()
    args = " ".join(f'"{a}"' if " " in a else a for a in [*cmd[1:], "sync", "--quiet"])
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Sunhub: sincroniza os repositórios de conhecimento</Description></RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition><Interval>PT{max(1, int(interval_minutes))}M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
    <LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId><Delay>PT2M</Delay></LogonTrigger>
  </Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <Hidden>true</Hidden>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(cmd[0])}</Command><Arguments>{escape(args)}</Arguments></Exec></Actions>
</Task>
"""


def write_units(interval_minutes: int) -> bool:
    """Grava a definição da tarefa; True se o comando ou o intervalo mudou (ignora a data de início)."""
    paths.ensure()
    f = _xml_file()
    new = _definition(interval_minutes)

    def key(text: str) -> str:
        import re  # noqa: PLC0415

        return re.sub(r"<StartBoundary>.*?</StartBoundary>", "", text)

    old = f.read_text("utf-16") if f.exists() else ""
    f.write_text(new, "utf-16")
    return key(old) != key(new)


def _schtasks(*args: str):
    return osutil.run(["schtasks", *args], capture_output=True, text=True)


def enable(interval_minutes: int) -> str:
    write_units(interval_minutes)
    if not available():
        raise TimerError("schtasks não encontrado")
    p = _schtasks("/Create", "/TN", TASK, "/XML", str(_xml_file()), "/F")
    if p.returncode != 0:
        raise TimerError((p.stderr or p.stdout).strip() or "schtasks falhou")
    return f"sync automático a cada {interval_minutes} min"


def disable() -> str:
    if available():
        _schtasks("/Delete", "/TN", TASK, "/F")
    _xml_file().unlink(missing_ok=True)
    return "sync automático desativado"


def status() -> str:
    """'active' | 'inactive' | 'not-installed' | 'unavailable'"""
    if not available():
        return "unavailable"
    p = _schtasks("/Query", "/TN", TASK, "/FO", "LIST")
    if p.returncode != 0:
        return "not-installed"
    text = p.stdout.lower()
    return "inactive" if ("disabled" in text or "desabilitad" in text) else "active"


def schedule(interval_minutes: int, last_sync=None) -> dict:
    info: dict = {"status": status(), "last": None, "next": None}
    if info["status"] != "active":
        return info
    if last_sync is not None:
        last = last_sync.astimezone().replace(tzinfo=None)
        info.update(last=last, next=last + timedelta(minutes=interval_minutes))
    return info
