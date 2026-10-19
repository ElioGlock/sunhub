"""Resumo das novidades de um sync e notificação do sistema (notify-send)."""
from __future__ import annotations

import shutil
import subprocess

from sunhub import osutil

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


POWERSHELL_AUMID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"


def _send_windows(title: str, body: str) -> bool:
    def q(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"

    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
        "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        f"$t = $x.GetElementsByTagName('text'); $t.Item(0).AppendChild($x.CreateTextNode({q(title)})) | Out-Null;"
        f"$t.Item(1).AppendChild($x.CreateTextNode({q(body)})) | Out-Null;"
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier({q(POWERSHELL_AUMID)})"
        ".Show([Windows.UI.Notifications.ToastNotification]::new($x))"
    )
    try:
        p = osutil.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, timeout=15)
        return p.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def send(title: str, body: str) -> bool:
    if osutil.IS_WINDOWS:
        return _send_windows(title, body)
    if not shutil.which("notify-send"):
        return False
    try:
        subprocess.run(["notify-send", "--app-name=Sunhub", "--icon=sunhub", title, body],
                       capture_output=True, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
