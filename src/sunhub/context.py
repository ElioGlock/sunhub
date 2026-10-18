"""Pacote de contexto para colar em ferramentas sem MCP."""
from __future__ import annotations

from sunhub import resolver
from sunhub.render import header
from sunhub.store import Store


def build(store: Store, task: str = "", refs: list[str] | None = None, priorities: dict | None = None,
          per_kind: int = 3) -> tuple[str, list[str]]:
    parts = ["# Contexto do Sunhub", ""]
    if task:
        parts += [f"Tarefa: {task}", ""]
    missing: list[str] = []
    seen: set[str] = set()
    for ref in refs or []:
        doc, _ = resolver.resolve(store, ref, priorities)
        if not doc:
            missing.append(ref)
            continue
        seen.add(doc.uid)
        parts += [f"## {doc.title}", f"_{header(doc)}_", "", doc.body.strip(), ""]
    if task:
        for kind, label in (("adr", "ADRs relacionados"), ("guideline", "Guidelines relacionadas"),
                            ("antipattern", "Antipatterns a evitar")):
            hits = [(d, s) for d, s in store.search(task, kind=kind, limit=per_kind + len(seen)) if d.uid not in seen]
            if hits:
                parts.append(f"## {label}")
                for d, s in hits[:per_kind]:
                    st = f" [{d.status}]" if d.status else ""
                    parts.append(f"- **{d.ref}** {d.title}{st} ({d.source}/{d.path}): {s}")
                parts.append("")
    parts.append("Regras: siga ADRs aceitos (ignore os substituídos), cite as referências usadas e diga quando nada cobrir o caso.")
    return "\n".join(parts).strip() + "\n", missing
