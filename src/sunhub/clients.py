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


@dataclass
class Client:
    key: str
    label: str
    kind: str  # "json" | "claude-cli"
    file_fn: object = None  # () -> Path do arquivo de configuração (varia por sistema)
    root_key: str = "mcpServers"
    extra: dict = field(default_factory=dict)
    detect_fn: object = None  # () -> list[Path] de pastas que indicam a ferramenta instalada
    detect_cmds: tuple[str, ...] = ()

    @property
    def file(self) -> Path | None:
        return self.file_fn() if self.file_fn else None

    @property
    def location(self) -> str:
        if not self.file_fn:
            return "claude mcp add --scope user"
        f, home = str(self.file), str(Path.home())
        return "~" + f[len(home):] if f.startswith(home) else f

    def detected(self) -> bool:
        dirs = self.detect_fn() if self.detect_fn else []
        return any(d.exists() for d in dirs) or any(shutil.which(c) for c in self.detect_cmds)


H = Path.home
CLIENTS = [
    Client("vscode", "VS Code (GitHub Copilot)", "json", lambda: osutil.vscode_user_dir("Code") / "mcp.json", "servers",
           {"type": "stdio"}, lambda: [osutil.vscode_user_dir("Code").parent], ("code",)),
    Client("vscode-insiders", "VS Code Insiders", "json", lambda: osutil.vscode_user_dir("Code - Insiders") / "mcp.json",
           "servers", {"type": "stdio"}, lambda: [osutil.vscode_user_dir("Code - Insiders").parent], ("code-insiders",)),
    Client("copilot-cli", "GitHub Copilot CLI", "json", lambda: H() / ".copilot" / "mcp-config.json", "mcpServers",
           {"type": "local", "tools": ["*"]}, None, ("copilot",)),
    Client("claude", "Claude Code", "claude-cli", None, detect_fn=lambda: [H() / ".claude"], detect_cmds=("claude",)),
    Client("cursor", "Cursor", "json", lambda: H() / ".cursor" / "mcp.json", "mcpServers", {},
           lambda: [H() / ".cursor"], ("cursor",)),
    Client("gemini", "Gemini CLI", "json", lambda: H() / ".gemini" / "settings.json", "mcpServers", {},
           lambda: [H() / ".gemini"], ("gemini",)),
]


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
