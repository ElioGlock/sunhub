"""Instalação no sistema do usuário e o caminho de volta (uninstall)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

from sunhub import blocks, clients, config, linker, osutil, paths, runtime, scheduler, state
from sunhub.store import Store

DESKTOP_ID = "sunhub"

PROFILE_FILES = (".profile", ".bashrc", ".zshrc")
PROJECT_BLOCK = """## Sunhub (base de conhecimento da equipe)

- ADRs, guidelines, antipatterns e skills da equipe estão no Sunhub: servidor MCP `sunhub` e skill `sunhub`.
- Antes de decisões de arquitetura ou de API, consulte-os; cite a referência (ex.: adr:14) e o status.
- ADRs substituídos não valem. Não invente padrões internos: se nada cobrir o caso, diga.
- Sem MCP: `sunhub search "<texto>"` e `sunhub get <ref>` no terminal."""


def shell_env() -> list[str]:
    """Exporta SUNHUB_HOME e garante ~/.local/bin no PATH, em blocos marcados."""
    body = (f'export SUNHUB_HOME="{paths.home()}"\n'
            'case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac')
    touched = []
    for name in PROFILE_FILES:
        f = Path.home() / name
        if name == ".profile" or f.exists():
            blocks.upsert(f, body)
            touched.append(str(f))
    return touched


def bin_dir() -> Path:
    """Pasta do executável `sunhub` deste ambiente (venv do app ou uv tool)."""
    return Path(sys.executable).parent


def _track_file(path: Path) -> None:
    state.remember("files", {"path": str(path)})


def link_cli() -> str | None:
    """Garante `sunhub` em ~/.local/bin. Não mexe em um `sunhub` que não seja nosso."""
    link = Path.home() / ".local" / "bin" / "sunhub"
    link.parent.mkdir(parents=True, exist_ok=True)
    img = runtime.appimage()
    ours = any(e["path"] == str(link) for e in state.load()["files"])
    if img:
        if link.exists() and not ours and not link.is_symlink():
            return None
        script = f'#!/bin/sh\nexec "{img}" "$@"\n'
        if link.is_symlink():
            link.unlink()
        if not (link.exists() and link.read_text("utf-8") == script):
            link.write_text(script, "utf-8")
            link.chmod(0o755)
        _track_file(link)
        return str(link)
    target = runtime.cli_entry()
    if target is None:
        return None
    if link.is_symlink() and os.readlink(link) == str(target):
        _track_file(link)
        return str(link)
    if link.exists() and not link.is_symlink() and not ours:
        return None
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(target)
    _track_file(link)
    return str(link)


def win_bin_dir() -> Path:
    return osutil.localappdata() / "Sunhub" / "bin"


def link_cli_windows() -> str:
    """`sunhub` no terminal do Windows: um sunhub.cmd numa pasta nossa, adicionada ao PATH do usuário."""
    d = win_bin_dir()
    d.mkdir(parents=True, exist_ok=True)
    shim = d / "sunhub.cmd"
    content = "@echo off\r\n" + subprocess.list2cmdline(runtime.self_command()) + " %*\r\n"
    if not shim.exists() or shim.read_text("utf-8") != content:
        shim.write_text(content, "utf-8", newline="")
    _track_file(shim)
    if osutil.windows_user_path_add(d):
        state.remember("winpath", {"path": str(d)})
    return str(shim)


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def install_launcher_windows() -> str:
    """Atalho no Menu Iniciar (sem janela de console)."""
    programs = osutil.appdata() / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    programs.mkdir(parents=True, exist_ok=True)
    lnk = programs / "Sunhub.lnk"
    ico = paths.state_dir() / "sunhub.ico"
    ico.write_bytes(resources.files("sunhub.desktop").joinpath("sunhub.ico").read_bytes())
    cmd = runtime.self_command(background=True)
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(lnk)});"
          f"$s.TargetPath={_ps_quote(cmd[0])};"
          f"$s.Arguments={_ps_quote(subprocess.list2cmdline([*cmd[1:], 'app']))};"
          f"$s.WorkingDirectory={_ps_quote(Path.home())};"
          f"$s.IconLocation={_ps_quote(str(ico) + ',0')};"
          "$s.Description='Skills, ADRs e guidelines para agentes de IA';$s.Save()")
    osutil.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
               timeout=60)
    for f in (lnk, ico):
        _track_file(f)
    return str(lnk)


def install_launcher() -> str:
    """Atalho no menu de aplicativos (.desktop) + ícone."""
    apps = Path.home() / ".local" / "share" / "applications"
    icons = Path.home() / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps"
    apps.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)
    icon = icons / f"{DESKTOP_ID}.svg"
    icon.write_bytes(resources.files("sunhub.desktop").joinpath("sunhub.svg").read_bytes())
    entry = apps / f"{DESKTOP_ID}.desktop"
    env = "" if paths.is_default_home() else f"env SUNHUB_HOME={paths.home()} "
    entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Sunhub\n"
        "GenericName=Hub de conhecimento para agentes\n"
        "Comment=Skills, ADRs, guidelines e antipatterns sincronizados para Copilot, Claude Code e outros\n"
        f"Exec={env}{runtime.shell_command('app')}\n"
        f"Icon={DESKTOP_ID}\n"
        "Terminal=false\n"
        "Categories=Development;Utility;\n"
        "Keywords=skills;adr;mcp;copilot;agents;\n"
        f"StartupWMClass={DESKTOP_ID}\n",
        "utf-8",
    )
    entry.chmod(0o755)
    for f in (entry, icon):
        _track_file(f)
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(apps)], capture_output=True)
    return str(entry)


def refresh_integrations() -> list[str]:
    """Reaponta MCP e timer para o Python atual (ex.: depois de mover/reinstalar o app)."""
    done = []
    for entry in list(state.load()["mcp"]):
        try:
            clients.install(clients.get(entry["client"]))
            done.append(entry["client"])
        except clients.ClientError:
            pass
    if scheduler.status() not in ("not-installed",):
        try:
            interval = config.load().settings.sync_interval_minutes
        except config.ConfigError:
            interval = config.Config().settings.sync_interval_minutes
        if scheduler.write_units(interval):  # caminho do Sunhub mudou: reativa
            try:
                scheduler.enable(interval)
                done.append("timer")
            except scheduler.TimerError:
                pass
    return done


def setup(launcher: bool = True) -> dict:
    paths.ensure()
    if not paths.config_file().exists():
        config.save(config.Config())
    cfg = config.load()
    with Store() as store:
        linker.write_router_skill(cfg, store)
        if cfg.sources:
            linker.relink(cfg, store)  # religa skills para o local atual das fontes
    if osutil.IS_WINDOWS:
        profiles, cli = [], link_cli_windows()
        entry = install_launcher_windows() if launcher else None
    else:
        profiles, cli = shell_env(), link_cli()
        entry = install_launcher() if launcher and not osutil.IS_MAC else None
    return {"home": str(paths.home()), "profiles": profiles, "cli": cli, "launcher": entry,
            "refreshed": refresh_integrations()}


def init_project(project: Path, copilot: bool = True) -> list[str]:
    project = Path(project).resolve()
    written = []
    agents = project / "AGENTS.md"
    blocks.upsert(agents, PROJECT_BLOCK, blocks.MARKDOWN, track=False)
    written.append(str(agents))
    if copilot:
        f = project / ".github" / "copilot-instructions.md"
        blocks.upsert(f, PROJECT_BLOCK, blocks.MARKDOWN, track=False)
        written.append(str(f))
    return written


def uninstall(purge: bool = False) -> list[str]:
    done = []
    n = linker.unlink_all()
    done.append(f"{n} skills despublicadas")
    for entry in list(state.load()["mcp"]):
        try:
            done.append(clients.uninstall(clients.get(entry["client"])))
        except clients.ClientError as e:
            done.append(str(e))
    if scheduler.status() != "not-installed":
        done.append(scheduler.disable())
    for entry in list(state.load()["files"]):
        f = Path(entry["path"])
        if f.is_symlink() or f.is_file():
            f.unlink()
            done.append(f"removido {f}")
        state.forget("files", entry)
    for entry in list(state.load()["winpath"]):
        try:
            if osutil.windows_user_path_remove(Path(entry["path"])):
                done.append(f"{entry['path']} removido do PATH")
        except OSError:
            pass
        state.forget("winpath", entry)
    for entry in list(state.load()["blocks"]):
        if blocks.remove(Path(entry["file"]), tuple(entry["markers"])):
            done.append(f"bloco removido de {entry['file']}")
    if purge and paths.home().exists():
        osutil.rmtree(paths.home())
        done.append(f"{paths.home()} apagado")
    return done
