"""Servidor MCP (stdio) do Sunhub.

Leitura: busca, documentos, ADRs, antipatterns, novidades. Escrita só em `sun_propose_adr`, que
fica desligada até o usuário permitir. Respeita o perfil do projeto (.sunhub.toml) e registra o
que o agente consulta (para a tela Atividade).
Nada aqui pode escrever em stdout fora do protocolo.
"""
import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

try:  # mcp 2.x
    from mcp.server.mcpserver import Context
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import Context
    from mcp.server.fastmcp import FastMCP as _Server

from sunhub import config, notify, profile, resolver
from sunhub import search as search_mod
from sunhub.models import KINDS
from sunhub.render import for_agent, line, short
from sunhub.store import Store

INSTRUCTIONS = (
    "Sunhub é a base de conhecimento local da equipe: ADRs, guidelines, antipatterns, documentação e skills "
    "sincronizados de repositórios Git. Consulte antes de decisões de arquitetura/API e sempre que o usuário citar "
    "referências como adr:14, ADR-014, skill:nome, guideline:nome. ADRs superseded/deprecated não valem. "
    "Cite referência, status e fonte@commit. Se nada cobrir o caso, diga explicitamente e, se fizer sentido, "
    "ofereça propor um ADR com sun_propose_adr. Passe `project` (a pasta do projeto atual) quando souber."
)

mcp = _Server("sunhub", instructions=INSTRUCTIONS)

_roots_cache: dict[int, Optional[str]] = {}


def _kind(kind: Optional[str]) -> Optional[str]:
    if not kind:
        return None
    k = kind.strip().lower()
    aliases = {"adr": "adr", "adrs": "adr", "guideline": "guideline", "guidelines": "guideline",
               "antipattern": "antipattern", "antipatterns": "antipattern", "anti-pattern": "antipattern",
               "skill": "skill", "skills": "skill", "doc": "doc", "docs": "doc", "documentation": "doc"}
    if k not in aliases:
        raise ValueError(f"kind inválido '{kind}'. Use: {', '.join(KINDS)}")
    return aliases[k]


def _cfg() -> config.Config:
    try:
        return config.load()
    except config.ConfigError:
        return config.Config()


async def _root_from_client(ctx: Optional[Context]) -> Optional[str]:
    """Pasta do projeto informada pelo editor (MCP roots), quando o cliente suporta."""
    if ctx is None:
        return None
    try:
        session = ctx.session
    except Exception:  # noqa: BLE001
        return None
    key = id(session)
    if key in _roots_cache:
        return _roots_cache[key]
    caps = None
    for getter in (lambda: ctx.client_capabilities, lambda: session.client_params.capabilities):
        try:
            caps = getter()
            if caps is not None:
                break
        except Exception:  # noqa: BLE001 - API varia entre versões do SDK
            continue
    if caps is not None and getattr(caps, "roots", None) is None:
        _roots_cache[key] = None  # cliente não informa a pasta do projeto
        return None
    root = None
    try:
        result = await asyncio.wait_for(session.list_roots(), timeout=2)
        for r in result.roots:
            uri = str(r.uri)
            if uri.startswith("file://"):
                root = unquote(urlparse(uri).path)
                break
    except Exception:  # noqa: BLE001 - cliente sem suporte a roots
        root = None
    _roots_cache[key] = root
    return root


async def _scope(ctx: Optional[Context], project: Optional[str]) -> tuple[Optional[list[str]], Optional[Path]]:
    cfg = _cfg()
    start = project or None
    if not start:
        start = await _root_from_client(ctx)
    prof, allowed = profile.for_cwd(cfg, start)
    return allowed, (prof.path if prof else None)


def _log(tool: str, query: Optional[str] = None, doc=None, hits: Optional[int] = None) -> None:
    with Store() as st:
        st.log_usage(tool, query, doc.uid if doc else None, doc.source if doc else None, hits)


@mcp.tool()
async def sun_search(query: str, kind: Optional[str] = None, source: Optional[str] = None, limit: int = 8,
                     project: Optional[str] = None, ctx: Context = None) -> str:
    """Busca na base de conhecimento da equipe (por texto e, se ligado, por significado).

    kind: adr | guideline | antipattern | skill | doc (opcional). project: pasta do projeto atual (opcional;
    aplica o .sunhub.toml do projeto). Retorna referências para usar em sun_get.
    """
    sources, _ = await _scope(ctx, project)
    with Store() as st:
        hits = search_mod.search(st, query, _kind(kind), source, max(1, min(int(limit), 25)), sources=sources)
    _log("sun_search", query, hits=len(hits))
    if not hits:
        return (f"Nada encontrado para '{query}'. Nenhum documento do Sunhub cobre isso: diga isso ao usuário "
                "e, se for uma decisão de arquitetura, ofereça registrar um ADR (sun_propose_adr).")
    out = [f"{len(hits)} resultado(s) para '{query}':"]
    for d, snip in hits:
        status = f" status={d.status}" if d.status else ""
        sup = f" substituído_por={d.extra['superseded_by']}" if d.extra.get("superseded_by") else ""
        out.append(f"- {d.ref} | {d.title} |{status}{sup} | {d.source}/{d.path}@{short(d.commit_sha)}\n  {snip}")
    out.append("Leia o documento completo com sun_get(ref) antes de aplicá-lo.")
    return "\n".join(out)


@mcp.tool()
async def sun_get(ref: str, project: Optional[str] = None, ctx: Context = None) -> str:
    """Lê um documento completo por referência.

    Exemplos: adr:14, ADR-014, skill:api-standards, guideline:rest-naming, antipattern:god-object,
    <fonte>:<caminho/arquivo.md>.
    """
    sources, _ = await _scope(ctx, project)
    with Store() as st:
        doc, others = resolver.resolve(st, ref, _cfg().priorities(), sources)
        if not doc:
            hits = st.search(ref, limit=5, sources=sources)
            _log("sun_get", ref, hits=0)
            sug = "\n".join(f"- {d.ref}: {d.title}" for d, _ in hits)
            return f"Referência '{ref}' não encontrada." + (f" Talvez:\n{sug}" if sug else " Nenhum documento parecido.")
    _log("sun_get", ref, doc, 1)
    text = for_agent(doc)
    if others:
        text += "\n\nOutros candidatos para essa referência:\n" + "\n".join(f"- {d.uid} ({d.title})" for d in others[:8])
    return text


@mcp.tool()
async def sun_get_adr(id: str, project: Optional[str] = None, ctx: Context = None) -> str:
    """Lê um ADR pelo número/id (ex.: 14, ADR-014, ADR-flutter-0001, flutter-1) com status e a cadeia de substituição.

    Se o ADR foi substituído, inclui também o ADR vigente.
    """
    sources, _ = await _scope(ctx, project)
    key = id if id.lower().startswith("adr") else f"adr:{id}"
    pr = _cfg().priorities()
    with Store() as st:
        doc, others = resolver.resolve(st, key, pr, sources)
        if not doc or doc.kind != "adr":
            _log("sun_get_adr", id, hits=0)
            return f"ADR '{id}' não encontrado. Use sun_search(query, kind='adr')."
        parts = [for_agent(doc)]
        seen = {doc.uid}
        current = doc
        for _ in range(5):
            nxt = current.extra.get("superseded_by")
            if not nxt:
                break
            succ, _ = resolver.resolve(st, f"adr:{nxt}", pr, sources)
            if not succ or succ.uid in seen:
                parts.append(f"(o ADR substituto {nxt} não está no Sunhub)")
                break
            parts.append(f"\nADR vigente que substitui {current.ref_id}:\n" + for_agent(succ))
            seen.add(succ.uid)
            current = succ
    _log("sun_get_adr", id, doc, 1)
    if others:
        parts.append("\nOutros ADRs com o mesmo número em outras fontes: " + ", ".join(d.uid for d in others))
    return "\n".join(parts)


@mcp.tool()
async def sun_list(kind: Optional[str] = None, source: Optional[str] = None, project: Optional[str] = None,
                   ctx: Context = None) -> str:
    """Lista documentos (referência, título, status). kind: adr | guideline | antipattern | skill | doc."""
    sources, _ = await _scope(ctx, project)
    with Store() as st:
        docs = st.list(_kind(kind), source, limit=300, sources=sources)
    _log("sun_list", kind or "", hits=len(docs))
    return "\n".join(line(d) for d in docs) if docs else "Nenhum documento."


@mcp.tool()
async def sun_check_antipatterns(text: str, limit: int = 5, project: Optional[str] = None,
                                 ctx: Context = None) -> str:
    """Retorna os antipatterns da equipe relevantes para um plano, trecho de código ou descrição de mudança."""
    sources, _ = await _scope(ctx, project)
    with Store() as st:
        hits = search_mod.search(st, text, "antipattern", None, max(1, min(int(limit), 10)), sources=sources)
        total = st.count(kind="antipattern", sources=sources)
    _log("sun_check_antipatterns", text[:200], hits=len(hits))
    if not hits:
        return "Nenhum antipattern relevante encontrado." if total else "O Sunhub não tem antipatterns cadastrados."
    parts = [f"{len(hits)} antipattern(s) possivelmente relevantes. Verifique se a mudança cai em algum:"]
    for d, _ in hits:
        if len(d.body) > 4000:
            d.body = d.body[:4000] + "\n[…]"
        parts.append(for_agent(d))
    return "\n\n".join(parts)


@mcp.tool()
async def sun_changes(days: int = 7, project: Optional[str] = None, ctx: Context = None) -> str:
    """Novidades da base de conhecimento nos últimos `days` dias: ADRs/skills/guidelines novos, removidos,
    alterados e mudanças de status (ex.: um ADR que passou a superseded)."""
    sources, _ = await _scope(ctx, project)
    since = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    with Store() as st:
        rows = st.changes(since, sources=sources, limit=200)
    _log("sun_changes", str(days), hits=len(rows))
    if not rows:
        return f"Nenhuma novidade nos últimos {days} dia(s)."
    out = [f"{len(rows)} novidade(s) nos últimos {days} dia(s):"]
    for c in rows:
        when = datetime.fromisoformat(c["ts"]).astimezone().strftime("%d/%m %H:%M")
        out.append(f"- {when} [{c['source']}] {notify.describe(c)}")
    return "\n".join(out)


@mcp.tool()
async def sun_propose_adr(title: str, context: str, decision: str, consequences: str, alternatives: str = "",
                          source: Optional[str] = None) -> str:
    """Propõe um novo ADR (quando nenhum existente cobre a decisão). Cria o arquivo no formato do repositório,
    numa branch nova, e abre um PR para revisão humana. Só funciona se o usuário permitiu no Sunhub.
    Antes de chamar, confirme com o usuário o título e o conteúdo."""
    cfg = _cfg()
    if not cfg.settings.allow_proposals:
        return ("Propor ADRs está desligado. O usuário pode permitir no app (Integrações > Recursos) ou com "
                "`sunhub config allow_proposals true`. Enquanto isso, mostre o rascunho do ADR na conversa.")
    from sunhub import propose

    def work():
        with Store() as st:
            return propose.propose(cfg, st, title, context, decision, consequences, alternatives, source)

    try:
        p = await asyncio.to_thread(work)
    except Exception as e:  # noqa: BLE001
        return f"Não consegui propor o ADR: {e}"
    with Store() as st:
        st.log_usage("sun_propose_adr", title, None, p.source, 1)
    return p.message + ("\nRevise e aprove o PR para que o ADR passe a valer." if p.pushed else "")


@mcp.tool()
async def sun_status(project: Optional[str] = None, ctx: Context = None) -> str:
    """Fontes vinculadas, contagem de documentos, último sync e perfil do projeto em uso."""
    sources, prof_path = await _scope(ctx, project)
    cfg = _cfg()
    with Store() as st:
        lines = []
        for s in cfg.sources:
            if sources is not None and s.name not in sources:
                continue
            state = st.get_state(s.name) or {}
            counts = ", ".join(f"{n} {k}" for k, n in sorted(st.counts_by_kind(s.name).items())) or "vazia"
            err = f" ERRO: {state['last_error'].splitlines()[0]}" if state.get("last_error") else ""
            lines.append(f"- {s.name} ({s.location}) @{short(state.get('commit_sha'))}: {counts}; "
                         f"último sync {state.get('last_sync') or 'nunca'}{err}")
    if prof_path:
        lines.insert(0, f"Perfil do projeto: {prof_path} ({len(sources or [])} fonte(s))")
    return "\n".join(lines) or "Nenhuma fonte vinculada. O usuário pode rodar `sunhub add <url>`."


@mcp.resource("sunhub://{kind}/{key}")
def sun_resource(kind: str, key: str) -> str:
    """Documento do Sunhub como recurso: sunhub://adr/ADR-014, sunhub://skill/api-standards."""
    with Store() as st:
        doc, _ = resolver.resolve(st, f"{kind}:{key}", _cfg().priorities())
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
                sync.sync_all(cfg, st, blocking=False, notify_user=True)
    except Exception:  # noqa: BLE001 - nunca derrubar o servidor por causa do sync
        pass


def run() -> None:
    threading.Thread(target=_lazy_sync, daemon=True).start()
    mcp.run()
