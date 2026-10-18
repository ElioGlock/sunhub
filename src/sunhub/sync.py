"""Clona/atualiza as fontes, reindexa e religa skills."""
from __future__ import annotations

import fcntl
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
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


def overlay_base(cfg: Config, src: Source) -> Source | None:
    """Outra fonte, cadastrada antes, do MESMO repositório em outra branch.

    Nesse caso `src` é uma variação: o Sunhub baixa só os objetos que faltam e indexa só o que a
    branch tem de diferente (ex.: uma skill nova), sem duplicar o resto.
    """
    if not src.is_git:
        return None
    key = config.norm_url(src.url)
    for s in cfg.sources:
        if s is src:
            return None
        if s.is_git and s.enabled and config.norm_url(s.url) == key and (s.branch or None) != (src.branch or None):
            return s
    return None


def _overlay_docs(src: Source, root, base: Source, commit: str | None):
    """Documentos que a branch adicionou ou mudou desde que saiu da branch base."""
    base_branch = base.branch or git.default_branch(root) or git.default_branch(base.checkout_dir())
    if not base_branch:
        return None
    try:
        mb = git.merge_base(root, "HEAD", f"origin/{base_branch}")
        changes = git.changed_files(root, mb, "HEAD")
    except git.GitError:
        return None
    paths_ = {(b or a) for status, a, b in changes if status != "D"}
    for p in list(paths_):  # arquivo de dentro de uma skill → publica a skill inteira
        parent = PurePosixPath(p).parent
        while str(parent) not in ("", "."):
            if (root / parent / indexer.SKILL_FILE).is_file():
                paths_.add(str(parent / indexer.SKILL_FILE))
                break
            parent = parent.parent
    return indexer.index_paths(src.name, root, sorted(paths_), commit)


def sync_source(src: Source, store: Store, full: bool = False, base: Source | None = None) -> SyncResult:
    root = src.checkout_dir()
    try:
        commit = None
        old = new = None
        if src.is_git:
            if not (root / ".git").exists():
                if root.exists() and any(root.iterdir()):
                    raise SyncError(f"{root} já existe e não é um clone do Sunhub")
                git.clone(src.url, root, src.branch, src.paths, reference=base.checkout_dir() if base else None)
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

        if base:
            docs = _overlay_docs(src, root, base, commit)
            if docs is not None:
                store.replace_source(src.name, docs)
                store.set_state(src.name, commit, None)
                return SyncResult(src.name, True, old is None or old != new, store.count(src.name), commit, None,
                                  f"{detail}; só diferenças em relação a {base.name}")

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
        chosen = [s for s in cfg.enabled_sources if not names or s.name in names]
        bases = {s.name: overlay_base(cfg, s) for s in chosen}
        chosen.sort(key=lambda s: bases[s.name] is not None)  # bases antes das variações
        results = [sync_source(s, store, full, bases[s.name]) for s in chosen]
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
    dependents = [s.name for s in cfg.sources if (b := overlay_base(cfg, s)) is not None and b.name == name]
    src = cfg.remove(name)
    config.save(cfg)
    store.delete_source(name)
    if src.is_git:
        root = src.checkout_dir().resolve()
        base = paths.sources_dir().resolve()
        if root.parent == base and root.exists():  # só apaga clones gerenciados
            shutil.rmtree(root)
    if dependents:  # variações que dependiam desta fonte passam a ter o conteúdo completo
        sync_all(cfg, store, names=dependents, full=True)
    else:
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
