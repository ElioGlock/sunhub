"""Diferenças entre sistemas operacionais num lugar só (Linux, Windows; macOS onde é igual ao Linux)."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """subprocess.run sem abrir janela de console no Windows (o app e o agendador rodam sem console)."""
    if IS_WINDOWS:
        kw["creationflags"] = kw.get("creationflags", 0) | NO_WINDOW
    return subprocess.run(cmd, **kw)


# ------------------------------------------------------------------ pastas do sistema

def appdata() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")


def localappdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def vscode_user_dir(variant: str = "Code") -> Path:
    if IS_WINDOWS:
        return appdata() / variant / "User"
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / variant / "User"
    return Path.home() / ".config" / variant / "User"


# ------------------------------------------------------------------ links de pasta (symlink / junction)

def link_target(p: Path) -> str | None:
    """Destino de um symlink ou junction; None se `p` não é link."""
    try:
        target = os.readlink(p)
    except (OSError, ValueError):
        return None
    for prefix in ("\\\\?\\", "\\??\\"):
        if target.startswith(prefix):
            target = target[len(prefix):]
    return target


def is_link(p: Path) -> bool:
    return link_target(p) is not None


def same_path(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def make_dir_link(link: Path, target: Path) -> None:
    """Symlink de pasta. No Windows sem modo desenvolvedor, usa junction (não exige administrador)."""
    if IS_WINDOWS:
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError:
            import _winapi  # noqa: PLC0415 - só existe no Windows

            _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def remove_link(p: Path) -> None:
    """Remove só o link (nunca o conteúdo do destino)."""
    if IS_WINDOWS:
        os.rmdir(p)  # symlink de pasta e junction saem com rmdir, sem tocar no destino
    else:
        p.unlink()


# ------------------------------------------------------------------ trava entre processos

class LockBusy(RuntimeError):
    pass


@contextmanager
def file_lock(path: Path, blocking: bool = True) -> Iterator[None]:
    f = open(path, "a+")
    try:
        if IS_WINDOWS:
            import msvcrt  # noqa: PLC0415

            while True:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if not blocking:
                        raise LockBusy("ocupado") from None
                    time.sleep(0.5)
        else:
            import fcntl  # noqa: PLC0415

            try:
                fcntl.flock(f, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError:
                raise LockBusy("ocupado") from None
        yield
    finally:
        try:
            if IS_WINDOWS:
                import msvcrt  # noqa: PLC0415

                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl  # noqa: PLC0415

                fcntl.flock(f, fcntl.LOCK_UN)
        except OSError:
            pass
        f.close()


# ------------------------------------------------------------------ PATH do usuário (Windows)

def windows_user_path_add(folder: Path) -> bool:
    """Acrescenta `folder` ao PATH do usuário (registro HKCU\\Environment). Retorna True se mudou."""
    import winreg  # noqa: PLC0415

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
        try:
            value, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            value, kind = "", winreg.REG_EXPAND_SZ
        parts = [p for p in value.split(";") if p]
        if any(same_path(os.path.expandvars(p), folder) for p in parts):
            return False
        winreg.SetValueEx(key, "Path", 0, kind, ";".join([*parts, str(folder)]))
    _broadcast_env_change()
    return True


def windows_user_path_remove(folder: Path) -> bool:
    import winreg  # noqa: PLC0415

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
        try:
            value, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return False
        parts = [p for p in value.split(";") if p]
        keep = [p for p in parts if not same_path(os.path.expandvars(p), folder)]
        if keep == parts:
            return False
        winreg.SetValueEx(key, "Path", 0, kind, ";".join(keep))
    _broadcast_env_change()
    return True


def _broadcast_env_change() -> None:
    """Avisa o Explorer: terminais novos já enxergam o PATH atualizado."""
    try:
        import ctypes  # noqa: PLC0415

        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x1A, 0, "Environment", 0x2, 5000, None)
    except Exception:  # noqa: BLE001
        pass
