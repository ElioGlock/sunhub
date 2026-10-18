"""Resolve referências usadas em prompts e na CLI.

    adr:14 · adr:ADR-014 · ADR-014 · skill:api-standards · guideline:rest-naming
    antipattern:god-object · doc:setup · <fonte>:<caminho/arquivo.md>
"""
from __future__ import annotations

import re

from sunhub.indexer import slugify
from sunhub.models import KINDS, Document
from sunhub.store import Store

REF_RE = re.compile(r"^(?P<prefix>[A-Za-z0-9._-]+)\s*:\s*(?P<key>.+)$")
BARE_ADR_RE = re.compile(r"^ADR[-_ ]?(\d+)$", re.I)


def _adr_number(text: str | None) -> int | None:
    m = re.search(r"(\d+)", text or "")
    return int(m.group(1)) if m else None


def _rank(docs: list[Document], priorities: dict[str, int]) -> list[Document]:
    status_rank = {"accepted": 2, None: 1}
    return sorted(docs, key=lambda d: (priorities.get(d.source, 0), status_rank.get(d.status, 0)), reverse=True)


def resolve(store: Store, ref: str, priorities: dict[str, int] | None = None) -> tuple[Document | None, list[Document]]:
    """Retorna (melhor_documento, outros_candidatos)."""
    priorities = priorities or {}
    ref = ref.strip().strip("`'\"")
    if not ref:
        return None, []
    kind, key = None, ref
    m = BARE_ADR_RE.match(ref)
    if m:
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

    candidates = _candidates(store, kind, key)
    if not candidates:
        return None, []
    ranked = _rank(candidates, priorities)
    return ranked[0], ranked[1:]


def _candidates(store: Store, kind: str | None, key: str) -> list[Document]:
    kinds = [kind] if kind else list(KINDS)
    if "adr" in kinds:
        num = _adr_number(key) if re.fullmatch(r"(?:ADR[-_ ]?)?\d+", key, re.I) else None
        if num is not None:
            hits = [d for d in store.list(kind="adr") if _adr_number(d.ref_id) == num and (d.ref_id or "").upper().startswith("ADR")]
            if hits:
                return hits
    for k in kinds:
        hits = store.find(k, key) or store.find(k, slugify(key))
        if hits:
            return hits
    # aproximado: id ou título contém a chave
    low = key.lower()
    loose = [d for k in kinds for d in store.list(kind=k)
             if low in (d.ref_id or "").lower() or low in (d.title or "").lower()]
    if loose:
        return loose
    return [d for d, _ in store.search(key, kind=kind, limit=5)]
