"""Configuração do usuário: ~/sunhub/config.toml."""
from __future__ import annotations

import re
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import tomli_w

from sunhub import paths as _paths
from sunhub.models import KINDS

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
RESERVED_NAMES = {"sunhub", *KINDS}
LINK_TARGETS = ("copilot", "claude", "agents")
LINK_MODES = ("symlink", "copy")
URL_RE = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|[\w.-]+@[\w.-]+:)", re.I)

HEADER = (
    "# Configuração do Sunhub. Pode editar à mão ou pela GUI (sunhub gui).\n"
    "# Depois de editar, rode: sunhub sync\n\n"
)


class ConfigError(ValueError):
    pass


@dataclass
class Source:
    name: str
    url: str | None = None
    local: str | None = None
    branch: str | None = None
    ref: str | None = None
    priority: int = 0
    enabled: bool = True
    paths: list[str] = field(default_factory=list)  # sparse checkout opcional

    @property
    def is_git(self) -> bool:
        return bool(self.url)

    @property
    def location(self) -> str:
        return self.url or self.local or ""

    def checkout_dir(self) -> Path:
        if self.local:
            return Path(self.local).expanduser()
        return _paths.sources_dir() / self.name

    def to_dict(self) -> dict:
        d: dict = {"name": self.name}
        for key in ("url", "local", "branch", "ref"):
            value = getattr(self, key)
            if value:
                d[key] = value
        if self.priority:
            d["priority"] = self.priority
        if not self.enabled:
            d["enabled"] = False
        if self.paths:
            d["paths"] = list(self.paths)
        return d


@dataclass
class Settings:
    sync_interval_minutes: int = 30
    link_targets: list[str] = field(default_factory=lambda: list(LINK_TARGETS))
    link_mode: str = "symlink"
    gui_port: int = 8765


@dataclass
class Config:
    settings: Settings = field(default_factory=Settings)
    sources: list[Source] = field(default_factory=list)

    def get(self, name: str) -> Source | None:
        return next((s for s in self.sources if s.name == name), None)

    def require(self, name: str) -> Source:
        src = self.get(name)
        if src is None:
            raise ConfigError(f"a fonte '{name}' não existe")
        return src

    def add(self, src: Source) -> Source:
        validate_source(src)
        if self.get(src.name):
            raise ConfigError(f"já existe uma fonte chamada '{src.name}'")
        for s in self.sources:
            same_url = src.url and norm_url(s.url) == norm_url(src.url) and (s.branch or None) == (src.branch or None)
            same_dir = src.local and s.local == src.local
            if same_url or same_dir:
                raise ConfigError(f"isso já está vinculado como '{s.name}'")
        self.sources.append(src)
        return src

    def remove(self, name: str) -> Source:
        src = self.require(name)
        self.sources.remove(src)
        return src

    def priorities(self) -> dict[str, int]:
        return {s.name: s.priority for s in self.sources}

    @property
    def enabled_sources(self) -> list[Source]:
        return [s for s in self.sources if s.enabled]


def norm_url(url: str | None) -> str:
    return re.sub(r"(\.git)?/*$", "", (url or "").strip().lower())


def validate_source(src: Source) -> None:
    if not NAME_RE.match(src.name):
        raise ConfigError(f"nome inválido '{src.name}': use minúsculas, números, '.', '_' ou '-'")
    if src.name in RESERVED_NAMES:
        raise ConfigError(f"o nome '{src.name}' é reservado")
    if bool(src.url) == bool(src.local):
        raise ConfigError("informe uma URL Git ou uma pasta local (apenas um dos dois)")
    for p in src.paths:
        if p.startswith("/") or ".." in Path(p).parts:
            raise ConfigError(f"caminho de sparse checkout inválido: {p}")


def name_from_location(location: str) -> str:
    tail = re.split(r"[/:\\]", location.rstrip("/"))[-1]
    tail = re.sub(r"\.git$", "", tail).lower()
    tail = re.sub(r"[^a-z0-9._-]+", "-", tail).strip("-._")
    if not tail or tail in RESERVED_NAMES:
        tail = f"{tail or 'fonte'}-repo"
    return tail[:64]


def unique_name(cfg: Config, base: str) -> str:
    name, i = base, 2
    while cfg.get(name):
        name = f"{base}-{i}"
        i += 1
    return name


def source_from_location(
    cfg: Config,
    location: str,
    name: str | None = None,
    branch: str | None = None,
    ref: str | None = None,
    priority: int = 0,
    sparse: list[str] | None = None,
) -> Source:
    """URL Git → fonte clonada pelo Sunhub; pasta existente → fonte local (sem clone)."""
    location = location.strip()
    if not location:
        raise ConfigError("informe a URL do repositório")
    local = Path(location).expanduser()
    if not URL_RE.match(location) and local.is_dir():
        resolved = str(local.resolve())
        return Source(name=name or unique_name(cfg, name_from_location(resolved)), local=resolved, priority=priority)
    return Source(
        name=name or unique_name(cfg, name_from_location(location)),
        url=location,
        branch=branch or None,
        ref=ref or None,
        priority=priority,
        paths=list(sparse or []),
    )


def load() -> Config:
    f = _paths.config_file()
    if not f.exists():
        return Config()
    try:
        data = tomllib.loads(f.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"não consegui ler {f}: {e}") from e
    setting_keys = {x.name for x in fields(Settings)}
    source_keys = {x.name for x in fields(Source)}
    settings = Settings(**{k: v for k, v in (data.get("settings") or {}).items() if k in setting_keys})
    sources = [
        Source(**{k: v for k, v in s.items() if k in source_keys})
        for s in data.get("sources") or []
        if isinstance(s, dict) and s.get("name")
    ]
    return Config(settings=settings, sources=sources)


def save(cfg: Config) -> None:
    _paths.ensure()
    f = _paths.config_file()
    data = {"settings": asdict(cfg.settings), "sources": [s.to_dict() for s in cfg.sources]}
    tmp = f.with_suffix(".tmp")
    tmp.write_text(HEADER + tomli_w.dumps(data), "utf-8")
    tmp.replace(f)
