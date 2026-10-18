"""Registro do servidor MCP do Sunhub nos clientes (VS Code/Copilot, Claude Code, Cursor...).

Formatos e caminhos mudam rápido; cada cliente é uma entrada pequena aqui.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from sunhub import paths, state

SERVER_NAME = "sunhub"


class ClientError(RuntimeError):
    pass


@dataclass
class Client:
    key: str
    label: str
    kind: str  # "json" | "claude-cli"
    rel_file: str | None = None
    root_key: str = "mcpServers"
    extra: dict = field(default_factory=dict)
    detect_dirs: tuple[str, ...] = ()
    detect_cmds: tuple[str, ...] = ()

    @property
    def file(self) -> Path | None:
        return Path.home() / self.rel_file if self.rel_file else None

    @property
    def location(self) -> str:
        return f"~/{self.rel_file}" if self.rel_file else "claude mcp add --scope user"

    def detected(self) -> bool:
        return any((Path.home() / d).exists() for d in self.detect_dirs) or any(shutil.which(c) for c in self.detect_cmds)


CLIENTS = [
    Client("vscode", "VS Code (GitHub Copilot)", "json", ".config/Code/User/mcp.json", "servers", {"type": "stdio"},
           (".config/Code",), ("code",)),
    Client("vscode-insiders", "VS Code Insiders", "json", ".config/Code - Insiders/User/mcp.json", "servers",
           {"type": "stdio"}, (".config/Code - Insiders",), ("code-insiders",)),
    Client("copilot-cli", "GitHub Copilot CLI", "json", ".copilot/mcp-config.json", "mcpServers",
           {"type": "local", "tools": ["*"]}, (), ("copilot",)),
    Client("claude", "Claude Code", "claude-cli", None, detect_dirs=(".claude",), detect_cmds=("claude",)),
    Client("cursor", "Cursor", "json", ".cursor/mcp.json", "mcpServers", {}, (".cursor",), ("cursor",)),
    Client("gemini", "Gemini CLI", "json", ".gemini/settings.json", "mcpServers", {}, (".gemini",), ("gemini",)),
]


def get(key: str) -> Client:
    for c in CLIENTS:
        if c.key == key:
            return c
    raise ClientError(f"cliente desconhecido '{key}'. Opções: {', '.join(c.key for c in CLIENTS)}")


def server_spec() -> dict:
    spec: dict = {"command": sys.executable, "args": ["-m", "sunhub", "mcp"]}
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


def _read_json(f: Path) -> dict:
    if not f.exists() or not f.read_text("utf-8").strip():
        return {}
    text = f.read_text("utf-8")
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
        data = json.loads((Path.home() / ".claude.json").read_text("utf-8"))
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
        servers[SERVER_NAME] = {**c.extra, **spec}
        data[c.root_key] = servers
        _write_json(c.file, data)
        state.remember("mcp", {"client": c.key})
        return f"{c.label}: servidor MCP registrado em {c.location}"
    if not shutil.which("claude"):
        raise ClientError("comando `claude` não encontrado no PATH")
    subprocess.run(["claude", "mcp", "remove", "--scope", "user", SERVER_NAME], capture_output=True, text=True)
    cmd = ["claude", "mcp", "add", "--scope", "user"]
    for k, v in (spec.get("env") or {}).items():
        cmd += ["-e", f"{k}={v}"]
    cmd += [SERVER_NAME, "--", spec["command"], *spec["args"]]
    p = subprocess.run(cmd, capture_output=True, text=True)
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
        subprocess.run(["claude", "mcp", "remove", "--scope", "user", SERVER_NAME], capture_output=True, text=True)
    state.forget("mcp", {"client": c.key})
    return f"{c.label}: servidor MCP removido"


def env_info() -> dict:
    return {"python": sys.executable, "home": str(paths.home()), "path_has_sunhub": bool(shutil.which("sunhub")),
            "display": bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))}
