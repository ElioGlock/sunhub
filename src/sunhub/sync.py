"""Clona/atualiza as fontes, reindexa, registra novidades e religa skills."""
from __future__ import annotations

import shutil
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Iterator

from sunhub import config, git, indexer, linker, notify, osutil, paths
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
    changes: list[dict] = field(default_factory=list)


@contextmanager
def sync_lock(blocking: bool = True) -> Iterator[None]:
    paths.ensure()
    try:
        with osutil.file_lock(paths.state_dir() / "sync.lock", blocking):
            yield
    except osutil.LockBusy:
        raise SyncBusy("outro sync já está em andamento") from None


# ------------------------------------------------------------------ variações de branch

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


def _base_branch(root: Path, base: Source) -> str | None:
    return base.branch or git.default_branch(root) or git.default_branch(base.checkout_dir())


def _overlay_docs(src: Source, root: Path, base: Source, commit: str | None):
    """Documentos que a branch adicionou ou mudou desde que saiu da branch base."""
    base_branch = _base_branch(root, base)
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


# ------------------------------------------------------------------ exports .zip

def _extract_archive(src: Source, root: Path, store: Store) -> tuple[str, str, bool]:
    z = Path(src.archive).expanduser()
    if not z.is_file():
        raise SyncError(f"arquivo não encontrado: {z}")
    st = z.stat()
    marker = f"zip:{int(st.st_mtime)}:{st.st_size}"
    prev = (store.get_state(src.name) or {}).get("commit_sha")
    if prev == marker and root.is_dir():
        return marker, "em dia", False
    tmp = root.with_name(root.name + ".extraindo")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    base = tmp.resolve()
    try:
        with zipfile.ZipFile(z) as zf:
            for member in zf.infolist():
                target = (tmp / member.filename).resolve()
                is_link = (member.external_attr >> 16) & 0o170000 == 0o120000
                if is_link or target == base or base not in target.parents:
                    continue  # ignora symlinks e caminhos que escapam da pasta (zip slip)
                zf.extract(member, tmp)
    except zipfile.BadZipFile as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise SyncError(f"{z.name} não é um .zip válido") from e
    shutil.rmtree(root, ignore_errors=True)
    tmp.rename(root)
    return marker, "extraído", True


# ------------------------------------------------------------------ novidades

def _diff(source: str, before: dict, after: dict, commit: str | None) -> list[dict]:
    short = (commit or "")[:12] or None
    if not before:
        return [{"source": source, "change": "source_added", "detail": f"{len(after)} documentos", "commit_sha": short}]
    rows = []
    for uid, a in after.items():
        b = before.get(uid)
        base = {"source": source, "uid": uid, "ref": a["ref"], "title": a["title"], "kind": a["kind"],
                "commit_sha": short}
        if b is None:
            rows.append({**base, "change": "added"})
        elif (b["status"] or None) != (a["status"] or None):
            rows.append({**base, "change": "status", "detail": f"{b['status'] or '—'} → {a['status'] or '—'}"})
        elif b["hash"] != a["hash"]:
            rows.append({**base, "change": "modified"})
    for uid, b in before.items():
        if uid not in after:
            rows.append({"source": source, "uid": uid, "ref": b["ref"], "title": b["title"], "kind": b["kind"],
                         "change": "removed", "commit_sha": short})
    return rows


# ------------------------------------------------------------------ sync

def sync_source(src: Source, store: Store, full: bool = False, base: Source | None = None) -> SyncResult:
    root = src.checkout_dir()
    before = store.snapshot(src.name)
    try:
        commit = None
        old = new = None
        fetched = True
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
                also = [_base_branch(root, base)] if base else []
                old, new, fetched = git.update(root, src.branch, src.ref, also=also)
                detail = "atualizado" if old != new else ("em dia" if fetched else "em dia (nada a baixar)")
            commit = new
            changed = old is None or old != new
        elif src.archive:
            commit, detail, changed = _extract_archive(src, root, store)
        else:
            if not root.is_dir():
                raise SyncError(f"pasta local não encontrada: {root}")
            detail, changed = "pasta local reindexada", True

        if base and (changed or fetched or full or not before):
            docs = _overlay_docs(src, root, base, commit)
            if docs is not None:
                store.replace_source(src.name, docs)
                rows = _diff(src.name, before, store.snapshot(src.name), commit)
                store.add_changes(rows)
                store.set_state(src.name, commit, None)
                return SyncResult(src.name, True, changed, store.count(src.name), commit, None,
                                  f"{detail}; só diferenças em relação a {base.name}", rows)
        elif base:
            store.set_state(src.name, commit, None)
            return SyncResult(src.name, True, False, store.count(src.name), commit, None,
                              f"{detail}; só diferenças em relação a {base.name}")

        incremental = bool(src.is_git and old and new and old != new and not full and before)
        touched: set[str] = set()
        if incremental:
            touched = {p for _, a, b in git.changed_files(root, old, new) for p in (a, b) if p}
            if any(p == indexer.OVERRIDE_FILE or p.endswith(indexer.SKILL_FILE) for p in touched):
                incremental = False
        reindexed = True
        if incremental:
            store.delete_uids([f"{src.name}:{p}" for p in touched])
            store.upsert(indexer.index_paths(src.name, root, sorted(touched), commit))
            detail += f" ({len(touched)} arquivo(s))"
        elif changed or full or not before:
            store.replace_source(src.name, indexer.index_source(src.name, root, commit))
        else:
            reindexed = False
        rows = _diff(src.name, before, store.snapshot(src.name), commit) if reindexed else []
        store.add_changes(rows)
        store.set_state(src.name, commit, None)
        return SyncResult(src.name, True, changed, store.count(src.name), commit, None, detail, rows)
    except (git.GitError, SyncError, OSError) as e:
        store.set_state(src.name, None, str(e))
        return SyncResult(src.name, False, False, store.count(src.name), None, str(e), "falhou")


def sync_all(cfg: Config, store: Store, names: list[str] | None = None, full: bool = False,
             relink: bool = True, blocking: bool = True,
             notify_user: bool = False) -> tuple[list[SyncResult], "linker.LinkReport | None"]:
    if names:
        for n in names:
            cfg.require(n)
    with sync_lock(blocking):
        chosen = [s for s in cfg.enabled_sources if not names or s.name in names]
        bases = {s.name: overlay_base(cfg, s) for s in chosen}
        chosen.sort(key=lambda s: bases[s.name] is not None)  # bases antes das variações
        results = [sync_source(s, store, full, bases[s.name]) for s in chosen]
        report = linker.relink(cfg, store) if relink else None
        if cfg.settings.semantic:
            _update_vectors(store)
        _log(results)
    if notify_user and cfg.settings.notify:
        rows = [c for r in results for c in r.changes if c["change"] != "source_added"]
        body = notify.summarize(rows)
        if body:
            notify.send("Sunhub: novidades nas suas fontes", body)
    return results, report


def _update_vectors(store: Store) -> None:
    try:
        from sunhub import semantic

        emb = semantic.get_embedder()
        if emb:
            semantic.update_index(store, emb)
    except Exception as e:  # noqa: BLE001 - busca por significado nunca derruba o sync
        _log_line(f"busca por significado: não atualizou os vetores ({e})")


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
    if not src.local:
        root = src.checkout_dir().resolve()
        base = paths.sources_dir().resolve()
        if root.parent == base and root.exists():  # só apaga o que o Sunhub criou
            shutil.rmtree(root)
    if dependents:  # variações que dependiam desta fonte passam a ter o conteúdo completo
        sync_all(cfg, store, names=dependents, full=True)
    else:
        linker.relink(cfg, store)
    return src


def _log_line(text: str) -> None:
    try:
        with open(paths.log_file(), "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {text}\n")
    except OSError:
        pass


def _log(results: list[SyncResult]) -> None:
    for r in results:
        line = f"{r.name}: {'ok' if r.ok else 'ERRO'} {r.detail} docs={r.docs}"
        if r.changes:
            line += f" novidades={len(r.changes)}"
        if r.error:
            line += " " + r.error.replace("\n", " | ")
        _log_line(line)
