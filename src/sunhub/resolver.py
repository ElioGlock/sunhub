"""Resolve referências usadas em prompts e na CLI.

    adr:14 · adr:ADR-014 · ADR-014 · ADR-flutter-0001 · adr:flutter-1 · skill:api-standards
    antipattern:god-object · doc:setup · <fonte>:<caminho/arquivo.md>
"""
from __future__ import annotations

import re

from sunhub.indexer import parse_adr_key, parse_adr_ref, slugify
from sunhub.models import KINDS, Document
from sunhub.store import Store

REF_RE = re.compile(r"^(?P<prefix>[A-Za-z0-9._-]+)\s*:\s*(?P<key>.+)$")


def _rank(docs: list[Document], priorities: dict[str, int]) -> list[Document]:
    status_rank = {"accepted": 2, None: 1}
    return sorted(docs, key=lambda d: (priorities.get(d.source, 0), status_rank.get(d.status, 0)), reverse=True)


def resolve(store: Store, ref: str, priorities: dict[str, int] | None = None,
            sources: list[str] | None = None) -> tuple[Document | None, list[Document]]:
    """Retorna (melhor_documento, outros_candidatos). `sources` limita às fontes do perfil do projeto."""
    priorities = priorities or {}
    ref = ref.strip().strip("`'\"")
    if not ref:
        return None, []
    kind, key = None, ref
    if re.match(r"^ADR", ref, re.I) and parse_adr_key(ref):
        kind, key = "adr", ref
    else:
        m = REF_RE.match(ref)
        if m:
            prefix, key = m.group("prefix"), m.group("key").strip()
            if prefix.lower() in KINDS:
                kind = prefix.lower()
            else:  # <fonte>:<caminho>
                doc = store.get(f"{prefix}:{key.lstrip('/')}")
                if doc:
                    return doc, []
                hits = [d for d in store.list(source=prefix) if d.path.endswith(key.lstrip("/"))]
                if hits:
                    return hits[0], hits[1:]
                return None, []

    candidates = _candidates(store, kind, key, sources)
    if not candidates:
        return None, []
    ranked = _rank(candidates, priorities)
    return ranked[0], ranked[1:]


def _candidates(store: Store, kind: str | None, key: str, sources: list[str] | None = None) -> list[Document]:
    kinds = [kind] if kind else list(KINDS)
    if "adr" in kinds:
        want = parse_adr_key(key)
        if want is not None:
            adrs = [(d, parse_adr_ref(d.ref_id)) for d in store.list(kind="adr", sources=sources)
                    if (d.ref_id or "").upper().startswith("ADR")]
            hits = [d for d, got in adrs if got == want]
            if not hits and want[0] is None:  # "adr:1" sem escopo: vale ADR-flutter-0001, ADR-dotnet-0001…
                hits = [d for d, got in adrs if got and got[1] == want[1]]
            if hits:
                return hits
    for k in kinds:
        hits = store.find(k, key, sources) or store.find(k, slugify(key), sources)
        if hits:
            return hits
    low = key.lower()
    loose = [d for k in kinds for d in store.list(kind=k, sources=sources)
             if low in (d.ref_id or "").lower() or low in (d.title or "").lower()]
    if loose:
        return loose
    return [d for d, _ in store.search(key, kind=kind, limit=5, sources=sources)]
