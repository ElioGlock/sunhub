"""Registro do servidor MCP do Sunhub nos clientes (VS Code/Copilot, Claude Code, Cursor...).

Formatos e caminhos mudam rápido; cada cliente é uma entrada pequena aqui.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from sunhub import osutil, paths, runtime, state

SERVER_NAME = "sunhub"


class ClientError(RuntimeError):
    pass


def _tilde(p: Path) -> str:
    s, h = str(p), str(Path.home())
    return "~" + s[len(h):] if s.startswith(h) else s


@dataclass
class Client:
    """Uma ferramenta que pode usar o servidor MCP do Sunhub.

    Detecção conservadora: só conta como instalada com um sinal que a PRÓPRIA ferramenta cria
    (o comando no PATH, um arquivo dela ou conteúdo dela na pasta). O que o Sunhub cria nessas pastas
    (`skills/`, o arquivo de MCP) é ignorado, para o Sunhub não "detectar" a si mesmo.
    """

    key: str
    label: str
    kind: str  # "json" | "claude-cli"
    file_fn: object = None  # () -> Path do arquivo de configuração MCP (varia por sistema)
    root_key: str = "mcpServers"
    extra: dict = field(default_factory=dict)
    dirs_fn: object = None  # () -> list[Path]: pastas da ferramenta
    cmds: tuple[str, ...] = ()  # comandos da ferramenta no PATH
    files_fn: object = None  # () -> list[Path]: arquivos que só a ferramenta cria
    ignore: tuple[str, ...] = ()  # nomes dentro das pastas que o Sunhub pode ter criado

    @property
    def file(self) -> Path | None:
        return self.file_fn() if self.file_fn else None

    @property
    def location(self) -> str:
        return _tilde(self.file) if self.file_fn else "claude mcp add --scope user"

    def detection(self) -> str | None:
        """Motivo pelo qual a ferramenta parece instalada, ou None."""
        for c in self.cmds:
            found = shutil.which(c)
            if found:
                return f"comando `{c}` encontrado em {_tilde(Path(found))}"
        for f in self.files_fn() if self.files_fn else []:
            if f.is_file():
                return f"arquivo {_tilde(f)} encontrado"
        for d in self.dirs_fn() if self.dirs_fn else []:
            if d.is_dir():
                try:
                    own = sorted(e.name for e in d.iterdir() if e.name not in self.ignore)
                except OSError:
                    continue
                if own:
                    more = "…" if len(own) > 3 else ""
                    return f"pasta {_tilde(d)} com {', '.join(own[:3])}{more}"
        return None

    def detected(self) -> bool:
        return self.detection() is not None


H = Path.home
CLIENTS = [
    Client("vscode", "VS Code (GitHub Copilot)", "json", lambda: osutil.vscode_user_dir("Code") / "mcp.json", "servers",
           {"type": "stdio"}, dirs_fn=lambda: [osutil.vscode_user_dir("Code").parent], cmds=("code",),
           ignore=("User",)),
    Client("vscode-insiders", "VS Code Insiders", "json", lambda: osutil.vscode_user_dir("Code - Insiders") / "mcp.json",
           "servers", {"type": "stdio"}, dirs_fn=lambda: [osutil.vscode_user_dir("Code - Insiders").parent],
           cmds=("code-insiders",), ignore=("User",)),
    Client("copilot-cli", "GitHub Copilot CLI", "json", lambda: H() / ".copilot" / "mcp-config.json", "mcpServers",
           {"type": "local", "tools": ["*"]}, dirs_fn=lambda: [H() / ".copilot"], cmds=("copilot",),
           ignore=("skills", "mcp-config.json")),
    Client("claude", "Claude Code", "claude-cli", None, dirs_fn=lambda: [H() / ".claude"], cmds=("claude",),
           files_fn=lambda: [H() / ".claude.json"], ignore=("skills",)),
    Client("cursor", "Cursor", "json", lambda: H() / ".cursor" / "mcp.json", "mcpServers", {},
           dirs_fn=lambda: [H() / ".cursor"], cmds=("cursor",), ignore=("mcp.json", "skills")),
    Client("gemini", "Gemini CLI", "json", lambda: H() / ".gemini" / "settings.json", "mcpServers", {},
           dirs_fn=lambda: [H() / ".gemini"], cmds=("gemini",), ignore=("settings.json", "skills")),
]


def default_link_targets() -> list[str]:
    """Pastas de skills a marcar numa instalação nova: Copilot sempre; Claude Code só se estiver instalado;
    a pasta genérica ~/.agents só se já existir com conteúdo de outra ferramenta."""
    targets = ["copilot"]
    if get("claude").detected():
        targets.append("claude")
    agents = H() / ".agents"
    try:
        if agents.is_dir() and any(e.name != "skills" for e in agents.iterdir()):
            targets.append("agents")
    except OSError:
        pass
    return targets


def get(key: str) -> Client:
    for c in CLIENTS:
        if c.key == key:
            return c
    raise ClientError(f"cliente desconhecido '{key}'. Opções: {', '.join(c.key for c in CLIENTS)}")


def server_spec() -> dict:
    cmd = runtime.self_command()
    spec: dict = {"command": cmd[0], "args": [*cmd[1:], "mcp"]}
    if not paths.is_default_home():
        spec["env"] = {"SUNHUB_HOME": str(paths.home())}
    return spec


def _strip_jsonc(text: str) -> str:
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            out.append(ch)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(ch)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _read_text(f: Path) -> str:
    raw = f.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_json(f: Path) -> dict:
    if not f.exists():
        return {}
    text = _read_text(f)
    if not text.strip():
        return {}
    for candidate in (text, _strip_jsonc(text)):
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except ValueError:
            continue
    raise ClientError(f"não consegui ler {f} (JSON inválido). Adicione à mão:\n{snippet()}")


def _write_json(f: Path, data: dict) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    if f.exists():
        shutil.copy2(f, f.with_name(f"{f.name}.sunhub-bak-{time.strftime('%Y%m%d%H%M%S')}"))
    tmp = f.with_name(f.name + ".sunhub-tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8")
    tmp.replace(f)


def snippet() -> str:
    return json.dumps({"mcpServers": {SERVER_NAME: server_spec()}}, indent=2)


def installed(c: Client) -> bool:
    if c.kind == "json":
        try:
            return SERVER_NAME in (_read_json(c.file).get(c.root_key) or {})
        except ClientError:
            return False
    try:
        data = json.loads(_read_text(Path.home() / ".claude.json"))
        return SERVER_NAME in (data.get("mcpServers") or {})
    except (OSError, ValueError):
        return False


def install(c: Client) -> str:
    spec = server_spec()
    if c.kind == "json":
        data = _read_json(c.file)
        servers = data.get(c.root_key)
        if not isinstance(servers, dict):
            servers = {}
        entry = {**c.extra, **spec}
        if servers.get(SERVER_NAME) == entry:  # já está igual: não reescreve (nem cria backup)
            state.remember("mcp", {"client": c.key})
            return f"{c.label}: servidor MCP já registrado em {c.location}"
        servers[SERVER_NAME] = entry
        data[c.root_key] = servers
        _write_json(c.file, data)
        state.remember("mcp", {"client": c.key})
        return f"{c.label}: servidor MCP registrado em {c.location}"
    if not shutil.which("claude"):
        raise ClientError("comando `claude` não encontrado no PATH")
    claude = shutil.which("claude")
    osutil.run([claude, "mcp", "remove", "--scope", "user", SERVER_NAME], capture_output=True, text=True)
    cmd = [claude, "mcp", "add", "--scope", "user"]
    for k, v in (spec.get("env") or {}).items():
        cmd += ["-e", f"{k}={v}"]
    cmd += [SERVER_NAME, "--", spec["command"], *spec["args"]]
    p = osutil.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise ClientError((p.stderr or p.stdout).strip() or "claude mcp add falhou")
    state.remember("mcp", {"client": c.key})
    return f"{c.label}: servidor MCP registrado (escopo user)"


def uninstall(c: Client) -> str:
    if c.kind == "json":
        f = c.file
        if f.exists():
            data = _read_json(f)
            servers = data.get(c.root_key) or {}
            if SERVER_NAME in servers:
                servers.pop(SERVER_NAME)
                data[c.root_key] = servers
                _write_json(f, data)
    elif shutil.which("claude"):
        osutil.run([shutil.which("claude"), "mcp", "remove", "--scope", "user", SERVER_NAME], capture_output=True,
                   text=True)
    state.forget("mcp", {"client": c.key})
    return f"{c.label}: servidor MCP removido"


def env_info() -> dict:
    return {"python": sys.executable, "home": str(paths.home()), "path_has_sunhub": bool(shutil.which("sunhub")),
            "display": bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))}
