"""Instalação no sistema do usuário e o caminho de volta (uninstall)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

from sunhub import blocks, clients, config, linker, paths, runtime, state, systemd
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
    if systemd.status() not in ("not-installed",):
        try:
            interval = config.load().settings.sync_interval_minutes
        except config.ConfigError:
            interval = config.Config().settings.sync_interval_minutes
        if systemd.write_units(interval):  # caminho do Sunhub mudou: reativa
            try:
                systemd.enable(interval)
                done.append("timer")
            except systemd.TimerError:
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
    info = {"home": str(paths.home()), "profiles": shell_env(), "cli": link_cli(),
            "launcher": install_launcher() if launcher else None, "refreshed": refresh_integrations()}
    return info


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
    if systemd.status() != "not-installed":
        done.append(systemd.disable())
    for entry in list(state.load()["files"]):
        f = Path(entry["path"])
        if f.is_symlink() or f.is_file():
            f.unlink()
            done.append(f"removido {f}")
        state.forget("files", entry)
    for entry in list(state.load()["blocks"]):
        if blocks.remove(Path(entry["file"]), tuple(entry["markers"])):
            done.append(f"bloco removido de {entry['file']}")
    if purge and paths.home().exists():
        shutil.rmtree(paths.home())
        done.append(f"{paths.home()} apagado")
    return done
