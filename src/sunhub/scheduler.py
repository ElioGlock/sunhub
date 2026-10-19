"""Atualização automática no sistema atual: systemd --user (Linux) ou Agendador de Tarefas (Windows)."""
from __future__ import annotations

from sunhub import osutil
from sunhub.errors import TimerError

if osutil.IS_WINDOWS:
    from sunhub import winsched as _impl
else:
    from sunhub import systemd as _impl

__all__ = ["TimerError", "NAME", "enable", "disable", "status", "schedule", "write_units", "available"]

NAME = getattr(_impl, "NAME", "systemd do usuário")
enable = _impl.enable
disable = _impl.disable
status = _impl.status
schedule = _impl.schedule
write_units = _impl.write_units
available = _impl.available
