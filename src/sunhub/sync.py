"""Clona/atualiza as fontes, reindexa e religa skills."""
from __future__ import annotations

import fcntl
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator

from sunhub import config, git, indexer, linker, paths
from sunhub.config import Config, Source
from sunhub.store import Store


class SyncBusy(RuntimeError):
    pass


class SyncError(RuntimeError):
    pass


@dataclass
class SyncResult:
    name: str
    ok: bool
    changed: bool = False
    docs: int = 0
    commit: str | None = None
    error: str | None = None
    detail: str = ""


@contextmanager
def sync_lock(blocking: bool = True) -> Iterator[None]:
    paths.ensure()
    f = open(paths.state_dir() / "sync.lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
    except BlockingIOError:
        f.close()
        raise SyncBusy("outro sync já está em andamento")
    try:
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def sync_source(src: Source, store: Store, full: bool = False) -> SyncResult:
    root = src.checkout_dir()
    try:
        commit = None
        old = new = None
        if src.is_git:
            if not (root / ".git").exists():
                if root.exists() and any(root.iterdir()):
                    raise SyncError(f"{root} já existe e não é um clone do Sunhub")
                git.clone(src.url, root, src.branch, src.paths)
                if src.ref:
                    git.checkout(root, src.ref)
                new = git.head(root)
                detail = "clonado"
            else:
                old, new = git.update(root, src.branch, src.ref)
                detail = "atualizado" if old != new else "em dia"
            commit = new
        else:
            if not root.is_dir():
                raise SyncError(f"pasta local não encontrada: {root}")
            detail = "pasta local reindexada"

        changed = old is None or old != new
        incremental = bool(src.is_git and old and new and old != new and not full and store.count(src.name) > 0)
        if incremental:
            changes = git.changed_files(root, old, new)
            touched = {p for _, a, b in changes for p in (a, b) if p}
            if any(p == indexer.OVERRIDE_FILE or p.endswith(indexer.SKILL_FILE) for p in touched):
                incremental = False
        if incremental:
            store.delete_uids([f"{src.name}:{p}" for p in touched])
            store.upsert(indexer.index_paths(src.name, root, sorted(touched), commit))
            detail += f" ({len(touched)} arquivo(s))"
        elif changed or full or store.count(src.name) == 0:
            store.replace_source(src.name, indexer.index_source(src.name, root, commit))
        store.set_state(src.name, commit, None)
        return SyncResult(src.name, True, changed, store.count(src.name), commit, None, detail)
    except (git.GitError, SyncError, OSError) as e:
        store.set_state(src.name, None, str(e))
        return SyncResult(src.name, False, False, store.count(src.name), None, str(e), "falhou")


def sync_all(cfg: Config, store: Store, names: list[str] | None = None, full: bool = False,
             relink: bool = True, blocking: bool = True) -> tuple[list[SyncResult], "linker.LinkReport | None"]:
    if names:
        for n in names:
            cfg.require(n)
    with sync_lock(blocking):
        results = [sync_source(s, store, full) for s in cfg.enabled_sources if not names or s.name in names]
        report = linker.relink(cfg, store) if relink else None
        _log(results)
    return results, report


def add_source(cfg: Config, store: Store, src: Source, sync: bool = True) -> SyncResult | None:
    cfg.add(src)
    config.save(cfg)
    if not sync:
        return None
    results, _ = sync_all(cfg, store, names=[src.name])
    return results[0]


def remove_source(cfg: Config, store: Store, name: str) -> Source:
    src = cfg.remove(name)
    config.save(cfg)
    store.delete_source(name)
    if src.is_git:
        root = src.checkout_dir().resolve()
        base = paths.sources_dir().resolve()
        if root.parent == base and root.exists():  # só apaga clones gerenciados
            shutil.rmtree(root)
    linker.relink(cfg, store)
    return src


def _log(results: list[SyncResult]) -> None:
    try:
        with open(paths.log_file(), "a", encoding="utf-8") as f:
            stamp = datetime.now().isoformat(timespec="seconds")
            for r in results:
                line = f"{stamp} {r.name}: {'ok' if r.ok else 'ERRO'} {r.detail} docs={r.docs}"
                if r.error:
                    line += " " + r.error.replace("\n", " | ")
                f.write(line + "\n")
    except OSError:
        pass
