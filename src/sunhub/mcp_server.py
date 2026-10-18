"""Servidor MCP (stdio) do Sunhub. Somente leitura.

Registro: `sunhub install-mcp` (ou manual: comando `python -m sunhub mcp`).
Nada aqui pode escrever em stdout fora do protocolo.
"""
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from sunhub import config, resolver
from sunhub.models import KINDS
from sunhub.render import for_agent, line, short
from sunhub.store import Store

INSTRUCTIONS = (
    "Sunhub é a base de conhecimento local da equipe: ADRs, guidelines, antipatterns, documentação e skills "
    "sincronizados de repositórios Git. Consulte antes de decisões de arquitetura/API e sempre que o usuário citar "
    "referências como adr:14, ADR-014, skill:nome, guideline:nome. ADRs superseded/deprecated não valem. "
    "Cite referência, status e fonte@commit. Se nada cobrir o caso, diga explicitamente."
)

mcp = _Server("sunhub", instructions=INSTRUCTIONS)


def _kind(kind: Optional[str]) -> Optional[str]:
    if not kind:
        return None
    k = kind.strip().lower().rstrip("s")
    aliases = {"adr": "adr", "guideline": "guideline", "antipattern": "antipattern", "anti-pattern": "antipattern",
               "skill": "skill", "doc": "doc", "documentation": "doc", "docs": "doc"}
    if k not in aliases:
        raise ValueError(f"kind inválido '{kind}'. Use: {', '.join(KINDS)}")
    return aliases[k]


def _priorities() -> dict:
    try:
        return config.load().priorities()
    except config.ConfigError:
        return {}


@mcp.tool()
def sun_search(query: str, kind: Optional[str] = None, source: Optional[str] = None, limit: int = 8) -> str:
    """Busca textual na base de conhecimento da equipe.

    kind: adr | guideline | antipattern | skill | doc (opcional). Retorna referências para usar em sun_get.
    """
    with Store() as st:
        hits = st.search(query, _kind(kind), source, max(1, min(int(limit), 25)))
    if not hits:
        return f"Nada encontrado para '{query}'. Nenhum documento do Sunhub cobre isso; diga isso ao usuário."
    out = [f"{len(hits)} resultado(s) para '{query}':"]
    for d, snip in hits:
        status = f" status={d.status}" if d.status else ""
        sup = f" substituído_por={d.extra['superseded_by']}" if d.extra.get("superseded_by") else ""
        out.append(f"- {d.ref} | {d.title} |{status}{sup} | {d.source}/{d.path}@{short(d.commit_sha)}\n  {snip}")
    out.append("Leia o documento completo com sun_get(ref) antes de aplicá-lo.")
    return "\n".join(out)


@mcp.tool()
def sun_get(ref: str) -> str:
    """Lê um documento completo por referência.

    Exemplos: adr:14, ADR-014, skill:api-standards, guideline:rest-naming, antipattern:god-object,
    <fonte>:<caminho/arquivo.md>.
    """
    with Store() as st:
        doc, others = resolver.resolve(st, ref, _priorities())
        if not doc:
            hits = st.search(ref, limit=5)
            sug = "\n".join(f"- {d.ref}: {d.title}" for d, _ in hits)
            return f"Referência '{ref}' não encontrada." + (f" Talvez:\n{sug}" if sug else " Nenhum documento parecido.")
        text = for_agent(doc)
        if others:
            text += "\n\nOutros candidatos para essa referência:\n" + "\n".join(f"- {d.uid} ({d.title})" for d in others[:8])
        return text


@mcp.tool()
def sun_get_adr(id: str) -> str:
    """Lê um ADR pelo número/id (ex.: 14, ADR-014) com status e a cadeia de substituição.

    Se o ADR foi substituído, inclui também o ADR vigente.
    """
    key = id if id.lower().startswith("adr") else f"adr:{id}"
    with Store() as st:
        doc, others = resolver.resolve(st, key, _priorities())
        if not doc or doc.kind != "adr":
            return f"ADR '{id}' não encontrado. Use sun_search(query, kind='adr')."
        parts = [for_agent(doc)]
        seen = {doc.uid}
        current = doc
        for _ in range(5):
            nxt = current.extra.get("superseded_by")
            if not nxt:
                break
            succ, _ = resolver.resolve(st, f"adr:{nxt}", _priorities())
            if not succ or succ.uid in seen:
                parts.append(f"(o ADR substituto {nxt} não está no Sunhub)")
                break
            parts.append(f"\nADR vigente que substitui {current.ref_id}:\n" + for_agent(succ))
            seen.add(succ.uid)
            current = succ
        if others:
            parts.append("\nOutros ADRs com o mesmo número em outras fontes: " + ", ".join(d.uid for d in others))
        return "\n".join(parts)


@mcp.tool()
def sun_list(kind: Optional[str] = None, source: Optional[str] = None) -> str:
    """Lista documentos (referência, título, status). kind: adr | guideline | antipattern | skill | doc."""
    with Store() as st:
        docs = st.list(_kind(kind), source, limit=300)
    if not docs:
        return "Nenhum documento."
    return "\n".join(line(d) for d in docs)


@mcp.tool()
def sun_check_antipatterns(text: str, limit: int = 5) -> str:
    """Retorna os antipatterns da equipe relevantes para um plano, trecho de código ou descrição de mudança."""
    with Store() as st:
        hits = st.search(text, "antipattern", limit=max(1, min(int(limit), 10)))
        if not hits:
            total = st.count(kind="antipattern")
            return ("Nenhum antipattern relevante encontrado." if total
                    else "O Sunhub não tem antipatterns cadastrados.")
        parts = [f"{len(hits)} antipattern(s) possivelmente relevantes. Verifique se a mudança cai em algum:"]
        for d, _ in hits:
            body = d.body if len(d.body) < 4000 else d.body[:4000] + "\n[…]"
            d2 = d
            d2.body = body
            parts.append(for_agent(d2))
        return "\n\n".join(parts)


@mcp.tool()
def sun_status() -> str:
    """Fontes vinculadas, contagem de documentos e último sync."""
    cfg = config.load()
    with Store() as st:
        lines = []
        for s in cfg.sources:
            state = st.get_state(s.name) or {}
            counts = ", ".join(f"{n} {k}" for k, n in sorted(st.counts_by_kind(s.name).items())) or "vazia"
            err = f" ERRO: {state['last_error'].splitlines()[0]}" if state.get("last_error") else ""
            lines.append(f"- {s.name} ({s.location}) @{short(state.get('commit_sha'))}: {counts}; "
                         f"último sync {state.get('last_sync') or 'nunca'}{err}")
    return "\n".join(lines) or "Nenhuma fonte vinculada. O usuário pode rodar `sunhub add <url>`."


@mcp.resource("sunhub://{kind}/{key}")
def sun_resource(kind: str, key: str) -> str:
    """Documento do Sunhub como recurso: sunhub://adr/ADR-014, sunhub://skill/api-standards."""
    with Store() as st:
        doc, _ = resolver.resolve(st, f"{kind}:{key}", _priorities())
        return for_agent(doc) if doc else f"não encontrado: {kind}:{key}"


def _lazy_sync() -> None:
    """Se o último sync estiver velho (timer desligado/PC estava off), atualiza em segundo plano."""
    try:
        from sunhub import sync

        cfg = config.load()
        with Store() as st:
            last = st.last_sync()
            limit = timedelta(minutes=2 * max(5, cfg.settings.sync_interval_minutes))
            if cfg.sources and (last is None or datetime.now(timezone.utc) - last > limit):
                sync.sync_all(cfg, st, blocking=False)
    except Exception:  # noqa: BLE001 - nunca derrubar o servidor por causa do sync
        pass


def run() -> None:
    threading.Thread(target=_lazy_sync, daemon=True).start()
    mcp.run()
