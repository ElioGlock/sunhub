"""Caminhos do Sunhub.

Tudo é resolvido na hora da chamada, para respeitar SUNHUB_HOME e HOME
definidos depois do import (testes, systemd, GUI).
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DIRNAME = "sunhub"


def home() -> Path:
    env = os.environ.get("SUNHUB_HOME")
    return Path(env).expanduser() if env else Path.home() / DEFAULT_DIRNAME


def is_default_home() -> bool:
    return home() == Path.home() / DEFAULT_DIRNAME


def config_file() -> Path:
    return home() / "config.toml"


def sources_dir() -> Path:
    return home() / "sources"


def index_db() -> Path:
    return home() / "index.db"


def state_dir() -> Path:
    return home() / ".state"


def manifest_file() -> Path:
    return state_dir() / "manifest.json"


def log_file() -> Path:
    return state_dir() / "sync.log"


def router_skill_dir() -> Path:
    return state_dir() / "skills" / "sunhub"


def ensure() -> Path:
    for d in (home(), sources_dir(), state_dir()):
        d.mkdir(parents=True, exist_ok=True)
    return home()
