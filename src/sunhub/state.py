"""Manifesto: tudo o que o Sunhub criou fora do hub, para poder desfazer."""
from __future__ import annotations

import json

from sunhub import paths

KEYS = ("links", "mcp", "blocks", "files")


def load() -> dict:
    try:
        data = json.loads(paths.manifest_file().read_text("utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    for key in KEYS:
        data.setdefault(key, [])
    return data


def save(data: dict) -> None:
    paths.ensure()
    f = paths.manifest_file()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    tmp.replace(f)


def remember(key: str, entry: dict) -> None:
    data = load()
    if entry not in data[key]:
        data[key].append(entry)
    save(data)


def forget(key: str, entry: dict) -> None:
    data = load()
    data[key] = [e for e in data[key] if e != entry]
    save(data)
