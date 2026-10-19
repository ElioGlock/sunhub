"""Busca por significado: embeddings locais (sem serviço externo).

Opcional. Usa o fastembed (ONNX) com um modelo multilíngue pequeno, baixado uma vez para
~/sunhub/.state/models. Ligue com `sunhub semantic on` ou em Integrações no app.
"""
from __future__ import annotations

import hashlib
import importlib.util
import math
import os
import re
import shutil
import subprocess
import sys
from array import array

from sunhub import paths
from sunhub.models import Document
from sunhub.store import Store

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"  # 384 dims, ~220 MB, entende português
BATCH = 32


class Embedder:
    name = "base"

    def embed(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover - interface
        raise NotImplementedError


class FastEmbedder(Embedder):
    def __init__(self, model: str = MODEL):
        from fastembed import TextEmbedding

        cache = paths.state_dir() / "models"
        cache.mkdir(parents=True, exist_ok=True)
        self.name = model
        self._model = TextEmbedding(model_name=model, cache_dir=str(cache))

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self._model.embed(texts, batch_size=BATCH)]


class HashEmbedder(Embedder):
    """Embedding por palavras com hashing: sem dependências. Usado nos testes (SUNHUB_EMBEDDER=hash)."""

    name = "hash-256"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * 256
            for w in re.findall(r"\w+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 256] += 1.0
            out.append(v)
        return out


def installed() -> bool:
    return importlib.util.find_spec("fastembed") is not None


def install() -> str:
    """Instala o fastembed no ambiente atual do Sunhub."""
    if installed():
        return "fastembed já instalado"
    if shutil.which("uv"):
        cmd = ["uv", "pip", "install", "--python", sys.executable, "fastembed>=0.4"]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "fastembed>=0.4"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip().splitlines()[-1] if (p.stderr or p.stdout) else "falhou")
    importlib.invalidate_caches()
    return "fastembed instalado"


_cached: Embedder | None = None


def get_embedder() -> Embedder | None:
    global _cached
    forced = os.environ.get("SUNHUB_EMBEDDER")
    if forced == "hash":
        return HashEmbedder()
    if _cached is None and installed():
        _cached = FastEmbedder()
    return _cached


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def doc_text(d: Document) -> str:
    return f"{d.title}\n{d.description or ''}\n{d.body[:2000]}"


def update_index(store: Store, emb: Embedder) -> int:
    """Calcula vetores só para documentos novos ou alterados. Retorna quantos foram (re)calculados."""
    have = store.vector_hashes(emb.name)
    todo = []
    for d in store.list():
        h = hashlib.sha1(doc_text(d).encode("utf-8", "replace")).hexdigest()
        if have.get(d.uid) != h:
            todo.append((d, h))
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        vecs = emb.embed([doc_text(d) for d, _ in chunk])
        store.put_vectors(emb.name, [(d.uid, h, array("f", _normalize(v)).tobytes()) for (d, h), v in zip(chunk, vecs)])
    store.prune_vectors(emb.name)
    return len(todo)


def search(store: Store, emb: Embedder, query: str, kind: str | None = None, sources: list[str] | None = None,
           limit: int = 10) -> list[tuple[str, float]]:
    """[(uid, similaridade)] em ordem decrescente."""
    rows = store.vectors(emb.name, kind, sources)
    if not rows or not query.strip():
        return []
    q = _normalize(emb.embed([query])[0])
    scored = []
    for uid, blob in rows:
        v = array("f")
        v.frombytes(blob)
        scored.append((uid, sum(a * b for a, b in zip(q, v))))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]
