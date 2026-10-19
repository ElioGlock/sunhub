"""Busca única usada por app, CLI e MCP: texto (FTS5) + significado (opcional), fundidos por RRF."""
from __future__ import annotations

from sunhub import config
from sunhub.models import Document
from sunhub.store import Store

RRF_K = 60


def search(store: Store, query: str, kind: str | None = None, source: str | None = None,
           limit: int = 10, sources: list[str] | None = None) -> list[tuple[Document, str]]:
    if source:
        sources = [source] if sources is None else [s for s in sources if s == source]
    text_hits = store.search(query, kind, None, limit * 2, sources=sources)
    sem_hits: list[tuple[str, float]] = []
    try:
        if config.load().settings.semantic:
            from sunhub import semantic

            emb = semantic.get_embedder()
            if emb:
                sem_hits = [(u, s) for u, s in semantic.search(store, emb, query, kind, sources, limit * 2) if s > 0.25]
    except Exception:  # noqa: BLE001 - sem modelo/erro: fica só a busca por texto
        sem_hits = []
    if not sem_hits:
        return text_hits[:limit]
    scores: dict[str, float] = {}
    snippets: dict[str, str] = {}
    docs: dict[str, Document] = {}
    for rank, (d, snip) in enumerate(text_hits):
        scores[d.uid] = scores.get(d.uid, 0) + 1 / (RRF_K + rank)
        snippets[d.uid], docs[d.uid] = snip, d
    for rank, (uid, _sim) in enumerate(sem_hits):
        scores[uid] = scores.get(uid, 0) + 1 / (RRF_K + rank)
    out = []
    for uid in sorted(scores, key=scores.get, reverse=True)[:limit]:
        d = docs.get(uid) or store.get(uid)
        if d:
            out.append((d, snippets.get(uid) or (d.description or " ".join(d.body.split())[:160])))
    return out
