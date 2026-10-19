"""CLI `sunhub`."""
from pathlib import Path
from typing import List, NoReturn, Optional

import typer

from sunhub import __version__, clients, config, linker, paths, profile, resolver, scheduler
from sunhub import lint as lint_mod
from sunhub import notify as notify_mod
from sunhub import search as search_mod
from sunhub import context as context_mod
from sunhub import doctor as doctor_mod
from sunhub import setup as setup_mod
from sunhub import sync as sync_mod
from sunhub.config import LINK_MODES, LINK_TARGETS, ConfigError
from sunhub.models import KINDS
from sunhub.render import header, line, short
from sunhub.store import Store

app = typer.Typer(
    name="sunhub",
    help="Sunhub: skills, ADRs, guidelines e antipatterns de vários repositórios Git, sempre atualizados, "
         "para Copilot, Claude Code, Cursor, terminal e editores.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
timer_app = typer.Typer(help="Sync automático (systemd no Linux, Agendador de Tarefas no Windows).", no_args_is_help=True)
app.add_typer(timer_app, name="timer")


def ok(msg: str) -> None:
    typer.secho(f"✓ {msg}", fg=typer.colors.GREEN)


def warn(msg: str) -> None:
    typer.secho(f"! {msg}", fg=typer.colors.YELLOW, err=True)


def fail(msg: str) -> NoReturn:
    typer.secho(f"✗ {msg}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def load_cfg() -> config.Config:
    try:
        return config.load()
    except ConfigError as e:
        fail(str(e))


def _scope(cfg: config.Config, everything: bool) -> Optional[List[str]]:
    """Fontes do perfil do projeto (.sunhub.toml na pasta atual ou acima); None = todas."""
    if everything:
        return None
    prof, allowed = profile.for_cwd(cfg)
    if prof:
        typer.secho(f"perfil do projeto: {prof.path} ({len(allowed or [])} fonte(s); use --all para ignorar)",
                    dim=True, err=True)
    return allowed


ALL_OPT = typer.Option(False, "--all", "-a", help="Ignora o .sunhub.toml do projeto e usa todas as fontes.")


def _kind(kind: Optional[str]) -> Optional[str]:
    if kind and kind not in KINDS:
        fail(f"kind inválido '{kind}'. Use: {', '.join(KINDS)}")
    return kind


def print_results(results, report=None) -> bool:
    all_ok = True
    for r in results:
        if r.ok:
            ok(f"{r.name}: {r.detail} · {r.docs} documentos · {short(r.commit)}")
        else:
            all_ok = False
            typer.secho(f"✗ {r.name}: {r.error}", fg=typer.colors.RED, err=True)
    if report:
        if report.created:
            ok(f"{len(report.created)} skill(s) publicada(s)")
        if report.removed:
            ok(f"{len(report.removed)} skill(s) despublicada(s)")
        for c in report.conflicts:
            warn(f"já existe algo seu em {c}; não mexi")
    return all_ok


def _version(value: bool) -> None:
    if value:
        typer.echo(f"sunhub {__version__}")
        raise typer.Exit()


def _set_home(value: Optional[str]) -> Optional[str]:
    if value:
        import os

        os.environ["SUNHUB_HOME"] = value
    return value


@app.callback()
def _root(
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True, help="Mostra a versão."),
    home: Optional[str] = typer.Option(None, "--home", callback=_set_home, is_eager=True,
                                       help="Pasta do hub (padrão: ~/sunhub ou SUNHUB_HOME).", hidden=True),
):
    pass


# ------------------------------------------------------------------ fontes

@app.command()
def setup(
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    no_launcher: bool = typer.Option(False, "--no-launcher", help="Não criar atalho no menu de aplicativos."),
):
    """Prepara o sistema: hub (~/sunhub), SUNHUB_HOME, comando no PATH e atalho no menu. Rodado pelo `source app`."""
    info = setup_mod.setup(launcher=not no_launcher)
    if quiet:
        return
    ok(f"hub em {info['home']}")
    for f in info["profiles"]:
        ok(f"SUNHUB_HOME exportado em {f}")
    if info["cli"]:
        ok(f"comando em {info['cli']}")
    if info["launcher"]:
        ok(f"atalho no menu: {info['launcher']}")
    typer.echo("\nPróximo passo: `sunhub app` ou `sunhub add <url-do-repo>`.")


@app.command()
def add(
    location: str = typer.Argument(..., help="URL Git (https/ssh), pasta local ou export .zip (Confluence, Notion…)."),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Nome da fonte (padrão: nome do repo)."),
    branch: Optional[str] = typer.Option(None, "--branch", "-b"),
    ref: Optional[str] = typer.Option(None, "--ref", help="Fixar numa tag/commit."),
    priority: int = typer.Option(0, "--priority", "-p", help="Maior vence quando há referência repetida."),
    path: Optional[List[str]] = typer.Option(None, "--path", help="Sparse checkout: só estas pastas (repetível)."),
    no_sync: bool = typer.Option(False, "--no-sync", help="Só cadastra, sem clonar agora."),
):
    """Vincula um repositório (ou pasta). O Sunhub clona, indexa e publica as skills."""
    cfg = load_cfg()
    try:
        src = config.source_from_location(cfg, location, name, branch, ref, priority, path)
        with Store() as st:
            if no_sync:
                sync_mod.add_source(cfg, st, src, sync=False)
                ok(f"{src.name} cadastrada (rode `sunhub sync`)")
                return
            typer.echo({"git": f"→ clonando {src.location} …", "archive": f"→ extraindo {src.archive} …"}
                       .get(src.kind, f"→ indexando {src.local} …"))
            r = sync_mod.add_source(cfg, st, src)
    except (ConfigError, sync_mod.SyncBusy) as e:
        fail(str(e))
    if not print_results([r]):
        warn(f"a fonte ficou cadastrada; corrija o acesso e rode `sunhub sync {src.name}` ou `sunhub remove {src.name}`")
        raise typer.Exit(1)


@app.command()
def remove(name: str = typer.Argument(..., help="Nome da fonte.")):
    """Desvincula uma fonte (apaga o clone gerenciado e despublica suas skills)."""
    cfg = load_cfg()
    try:
        with Store() as st:
            src = sync_mod.remove_source(cfg, st, name)
    except ConfigError as e:
        fail(str(e))
    ok(f"{src.name} removida")


@app.command("list")
def list_cmd():
    """Fontes vinculadas e situação."""
    cfg = load_cfg()
    if not cfg.sources:
        typer.echo("Nenhuma fonte. Use `sunhub add <url>` ou `sunhub gui`.")
        return
    with Store() as st:
        for s in cfg.sources:
            state = st.get_state(s.name) or {}
            counts = " · ".join(f"{n} {k}" for k, n in sorted(st.counts_by_kind(s.name).items())) or "vazia"
            where = f"{s.branch or 'padrão'}@{short(state.get('commit_sha'))}" if s.is_git else "local"
            flag = typer.style("ERRO", fg="red") if state.get("last_error") else typer.style("ok", fg="green")
            if not s.enabled:
                flag = typer.style("off", fg="yellow")
            typer.echo(f"{s.name:<24} {where:<18} {counts:<44} {flag}")
            typer.secho(f"{'':<24} {s.location}", dim=True)
            if state.get("last_error"):
                typer.secho(f"{'':<24} {state['last_error'].splitlines()[0]}", fg="red")


@app.command()
def sync(
    names: Optional[List[str]] = typer.Argument(None, help="Fontes (padrão: todas)."),
    full: bool = typer.Option(False, "--full", help="Reindexa tudo do zero."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Sem saída quando der certo (timer)."),
):
    """git fetch/ff-only em cada fonte, reindexa o que mudou e religa as skills."""
    cfg = load_cfg()
    if quiet:  # execução do agendador: nunca abrir diálogo de login do git
        import os

        os.environ["GCM_INTERACTIVE"] = "never"
    try:
        with Store() as st:
            results, report = sync_mod.sync_all(cfg, st, names=names or None, full=full, blocking=not quiet,
                                                notify_user=quiet)
    except sync_mod.SyncBusy as e:
        if quiet:
            raise typer.Exit(0)
        fail(str(e))
    except ConfigError as e:
        fail(str(e))
    if quiet:
        bad = [r for r in results if not r.ok]
        for r in bad:
            typer.echo(f"{r.name}: {r.error}", err=True)
        raise typer.Exit(1 if bad else 0)
    if not results:
        typer.echo("Nenhuma fonte para sincronizar.")
    if not print_results(results, report):
        raise typer.Exit(1)


# ------------------------------------------------------------------ consulta

@app.command()
def search(
    query: str = typer.Argument(...),
    kind: Optional[str] = typer.Option(None, "--kind", "-k", help="adr | guideline | antipattern | skill | doc"),
    source: Optional[str] = typer.Option(None, "--source", "-s"),
    limit: int = typer.Option(10, "--limit", "-l"),
    everything: bool = ALL_OPT,
):
    """Busca na base de conhecimento (texto e, se ligada, por significado)."""
    cfg = load_cfg()
    sources = _scope(cfg, everything)
    with Store() as st:
        hits = search_mod.search(st, query, _kind(kind), source, limit, sources=sources)
    if not hits:
        typer.echo("Nada encontrado.")
        raise typer.Exit(1)
    for d, snip in hits:
        typer.echo(line(d, snip))


@app.command()
def get(
    ref: str = typer.Argument(..., help="adr:14 · ADR-014 · skill:nome · guideline:nome · <fonte>:<arquivo>"),
    raw: bool = typer.Option(False, "--raw", help="Só o conteúdo, sem cabeçalho."),
    everything: bool = ALL_OPT,
):
    """Imprime um documento (bom para pipes: `sunhub get adr:14 | xclip`)."""
    cfg = load_cfg()
    sources = _scope(cfg, everything)
    with Store() as st:
        doc, others = resolver.resolve(st, ref, cfg.priorities(), sources)
    if not doc:
        fail(f"'{ref}' não encontrado (tente `sunhub search`)")
    if not raw:
        typer.secho(header(doc), dim=True, err=True)
    typer.echo(doc.body.rstrip())
    for o in others[:5]:
        warn(f"outro candidato: {o.uid}")


@app.command()
def context(
    task: str = typer.Argument("", help="Descrição da tarefa (busca ADRs/guidelines/antipatterns relacionados)."),
    adr: Optional[List[str]] = typer.Option(None, "--adr", help="ADR a incluir (repetível)."),
    skill: Optional[List[str]] = typer.Option(None, "--skill", help="Skill a incluir (repetível)."),
    ref: Optional[List[str]] = typer.Option(None, "--ref", help="Qualquer referência (repetível)."),
    everything: bool = ALL_OPT,
):
    """Monta um pacote de contexto para colar em ferramentas sem MCP."""
    refs = [f"adr:{a}" for a in adr or []] + [f"skill:{s}" for s in skill or []] + list(ref or [])
    if not task and not refs:
        fail("informe uma tarefa ou referências (--adr, --skill, --ref)")
    cfg = load_cfg()
    sources = _scope(cfg, everything)
    with Store() as st:
        text, missing = context_mod.build(st, task, refs, cfg.priorities(), sources=sources)
    typer.echo(text)
    for m in missing:
        warn(f"não encontrado: {m}")


# ------------------------------------------------------------------ integrações

@app.command()
def link(
    targets: Optional[str] = typer.Option(None, "--targets", "-t", help=f"Pastas de skills: {','.join(LINK_TARGETS)}"),
    mode: Optional[str] = typer.Option(None, "--mode", "-m", help="symlink (padrão) ou copy"),
):
    """(Re)publica as skills nas pastas das ferramentas."""
    cfg = load_cfg()
    if targets is not None:
        chosen = [t.strip() for t in targets.split(",") if t.strip()]
        bad = [t for t in chosen if t not in LINK_TARGETS]
        if bad:
            fail(f"alvo inválido: {', '.join(bad)}. Use: {', '.join(LINK_TARGETS)}")
        cfg.settings.link_targets = chosen
    if mode is not None:
        if mode not in LINK_MODES:
            fail(f"modo inválido: {mode}")
        cfg.settings.link_mode = mode
    config.save(cfg)
    with Store() as st:
        report = linker.relink(cfg, st)
    for t in cfg.settings.link_targets:
        ok(f"~/{linker.TARGET_DIRS[t]}")
    print_results([], report)
    ok(f"{report.total} link(s) ativos")


@app.command()
def unlink():
    """Despublica todas as skills criadas pelo Sunhub."""
    ok(f"{linker.unlink_all()} link(s) removido(s)")


@app.command("clients")
def clients_cmd():
    """Clientes MCP suportados, detectados e registrados."""
    for c in clients.CLIENTS:
        reason = c.detection()
        det = typer.style("detectado", fg="green") if reason else typer.style("não detectado", dim=True)
        ins = typer.style("registrado", fg="green") if clients.installed(c) else "—"
        typer.echo(f"{c.key:<16} {c.label:<26} {det:<24} {ins}")
        if reason:
            typer.secho(f"{'':<16} motivo: {reason}", dim=True)


@app.command("install-mcp")
def install_mcp(
    keys: Optional[List[str]] = typer.Argument(None, help="vscode, copilot-cli, claude, cursor, gemini…"),
    detected: bool = typer.Option(False, "--detected", "-d", help="Todos os clientes detectados."),
):
    """Registra o servidor MCP do Sunhub nos clientes (com backup do arquivo)."""
    chosen = [c for c in clients.CLIENTS if c.detected()] if detected else []
    try:
        chosen += [clients.get(k) for k in keys or []]
    except clients.ClientError as e:
        fail(str(e))
    if not chosen:
        typer.echo("Diga quais: `sunhub install-mcp vscode claude` ou `sunhub install-mcp --detected`.\n")
        clients_cmd()
        raise typer.Exit(1)
    failed = False
    for c in {c.key: c for c in chosen}.values():
        try:
            ok(clients.install(c))
        except clients.ClientError as e:
            failed = True
            typer.secho(f"✗ {c.label}: {e}", fg="red", err=True)
    if failed:
        raise typer.Exit(1)


@app.command("uninstall-mcp")
def uninstall_mcp(keys: List[str] = typer.Argument(..., help="Clientes.")):
    """Remove o servidor MCP do Sunhub dos clientes."""
    for k in keys:
        try:
            ok(clients.uninstall(clients.get(k)))
        except clients.ClientError as e:
            fail(str(e))


@app.command("init-project")
def init_project(
    path: Path = typer.Argument(Path("."), help="Pasta do projeto."),
    copilot: bool = typer.Option(True, "--copilot/--no-copilot", help="Também em .github/copilot-instructions.md"),
    sources: Optional[str] = typer.Option(None, "--sources", help="Fontes que valem neste projeto (vírgula); "
                                                                   "cria o .sunhub.toml."),
):
    """Prepara um projeto: bloco curto no AGENTS.md e, com --sources, o perfil .sunhub.toml."""
    for f in setup_mod.init_project(path, copilot):
        ok(f)
    if sources:
        names = [s.strip() for s in sources.split(",") if s.strip()]
        known = {s.name for s in load_cfg().sources}
        for n in names:
            if "*" not in n and n not in known:
                warn(f"'{n}' não é uma fonte vinculada (sunhub list)")
        ok(f"perfil: {profile.write(path, names)}")


@app.command()
def doctor():
    """Diagnóstico completo."""
    cfg = load_cfg()
    with Store() as st:
        checks = doctor_mod.run(cfg, st)
    colors = {"ok": "green", "warn": "yellow", "error": "red"}
    for c in checks:
        typer.secho(f"{c.sign} {c.text}", fg=colors[c.level])
    if any(c.level == "error" for c in checks):
        raise typer.Exit(1)


@timer_app.command("enable")
def timer_enable(interval: Optional[int] = typer.Option(None, "--interval", "-i", help="Minutos (mín. 1).")):
    """Liga o sync automático."""
    cfg = load_cfg()
    if interval:
        cfg.settings.sync_interval_minutes = max(1, interval)
        config.save(cfg)
    try:
        ok(scheduler.enable(cfg.settings.sync_interval_minutes))
    except scheduler.TimerError as e:
        fail(str(e))


@timer_app.command("disable")
def timer_disable():
    """Desliga o sync automático."""
    ok(scheduler.disable())


@timer_app.command("status")
def timer_status():
    """Situação do timer."""
    typer.echo(scheduler.status())


# ------------------------------------------------------------------ apps

# ------------------------------------------------------------------ qualidade, novidades, uso

@app.command()
def lint(
    path: Optional[Path] = typer.Argument(None, help="Pasta a verificar (ex.: na CI do repositório de ADRs). "
                                                     "Sem argumento: todas as fontes vinculadas."),
    strict: bool = typer.Option(False, "--strict", help="Avisos também fazem falhar (código de saída 1)."),
):
    """Verifica ADRs, skills e links: status faltando, ADR substituído por um que não existe, skill sem descrição…"""
    if path is not None:
        if not path.is_dir():
            fail(f"{path} não é uma pasta")
        issues = lint_mod.lint_path(path, path.resolve().name)
    else:
        cfg = load_cfg()
        with Store() as st:
            issues = lint_mod.lint_sources(cfg, st)
    typer.echo(lint_mod.format_issues(issues))
    if any(i.level == "error" for i in issues) or (strict and issues):
        raise typer.Exit(1)


@app.command()
def changes(
    days: int = typer.Option(7, "--days", "-d", help="Últimos N dias."),
    everything: bool = ALL_OPT,
):
    """O que mudou nas fontes: ADRs/skills novos, removidos, alterados e mudanças de status."""
    from datetime import datetime, timedelta, timezone

    cfg = load_cfg()
    sources = _scope(cfg, everything)
    with Store() as st:
        rows = st.changes(datetime.now(timezone.utc) - timedelta(days=days), sources=sources)
    if not rows:
        typer.echo(f"Nenhuma novidade nos últimos {days} dia(s).")
        return
    for c in rows:
        when = datetime.fromisoformat(c["ts"]).astimezone().strftime("%d/%m %H:%M")
        color = {"added": "green", "removed": "red", "status": "yellow"}.get(c["change"])
        typer.secho(f"{when}  [{c['source']}] {notify_mod.describe(c)}", fg=color)


@app.command()
def usage(days: int = typer.Option(30, "--days", "-d")):
    """O que o agente consultou: documentos mais lidos, buscas sem resposta (lacunas) e o que nunca é lido."""
    with Store() as st:
        rep = st.usage_report(days)
    typer.echo(f"Últimos {days} dias: {rep['total']} consulta(s) do agente")
    if rep["by_tool"]:
        typer.echo("  " + " · ".join(f"{k} {v}" for k, v in rep["by_tool"].items()))
    if rep["top"]:
        typer.secho("\nMais lidos", bold=True)
        for r in rep["top"]:
            typer.echo(f"  {r['n']:>4}×  {r['kind']}:{r['ref_id']}  {r['title']}  ({r['source']})")
    if rep["gaps"]:
        typer.secho("\nBuscas sem resultado (possíveis lacunas na documentação)", bold=True)
        for r in rep["gaps"]:
            typer.echo(f"  {r['n']:>4}×  {r['query']}")
    typer.echo(f"\nNunca lidos no período: {len(rep['unread'])} documento(s) (sem contar skills)")


@app.command("propose-adr")
def propose_adr(
    title: str = typer.Option(..., "--title", "-t"),
    context: str = typer.Option("", "--context"),
    decision: str = typer.Option("", "--decision"),
    consequences: str = typer.Option("", "--consequences"),
    alternatives: str = typer.Option("", "--alternatives"),
    source: Optional[str] = typer.Option(None, "--source", "-s", help="Fonte que recebe o ADR (padrão: a com mais ADRs)."),
    no_push: bool = typer.Option(False, "--no-push", help="Só cria a branch local, sem enviar nem abrir PR."),
):
    """Cria um ADR novo no formato do repositório, numa branch, e abre um PR (gh) para revisão."""
    from sunhub import propose

    cfg = load_cfg()
    try:
        with Store() as st:
            p = propose.propose(cfg, st, title, context, decision, consequences, alternatives, source, push=not no_push)
    except (propose.ProposalError, ConfigError) as e:
        fail(str(e))
    ok(p.message)


semantic_app = typer.Typer(help="Busca por significado (embeddings locais, opcional).", no_args_is_help=True)
app.add_typer(semantic_app, name="semantic")


@semantic_app.command("on")
def semantic_on():
    """Liga: instala o fastembed se faltar, baixa o modelo (~220 MB, uma vez) e indexa."""
    from sunhub import semantic

    try:
        typer.echo("→ " + semantic.install())
        cfg = load_cfg()
        cfg.settings.semantic = True
        config.save(cfg)
        typer.echo("→ calculando vetores (a primeira vez baixa o modelo)…")
        with Store() as st:
            n = semantic.update_index(st, semantic.get_embedder())
    except Exception as e:  # noqa: BLE001
        fail(f"não consegui ligar a busca por significado: {e}")
    ok(f"busca por significado ligada ({n} documento(s) indexado(s))")


@semantic_app.command("off")
def semantic_off():
    """Desliga (os vetores ficam guardados)."""
    cfg = load_cfg()
    cfg.settings.semantic = False
    config.save(cfg)
    ok("busca por significado desligada")


@semantic_app.command("status")
def semantic_status():
    from sunhub import semantic

    cfg = load_cfg()
    typer.echo(f"ligada: {'sim' if cfg.settings.semantic else 'não'} · fastembed: "
               f"{'instalado' if semantic.installed() else 'não instalado'} · modelo: {semantic.MODEL}")


SETTING_HELP = {
    "sync_interval_minutes": "minutos entre verificações automáticas",
    "link_targets": "pastas de skills: copilot,claude,agents",
    "link_mode": "symlink | copy",
    "notify": "notificação do sistema quando há novidades (true/false)",
    "semantic": "busca por significado (use `sunhub semantic on`)",
    "allow_proposals": "o agente pode propor ADRs, criando branch e PR (true/false)",
    "tray": "o app fica na bandeja ao fechar a janela (true/false)",
    "gui_port": "(legado)",
}


@app.command("config")
def config_cmd(
    key: Optional[str] = typer.Argument(None, help="Configuração."),
    value: Optional[str] = typer.Argument(None, help="Novo valor."),
):
    """Mostra ou altera configurações: `sunhub config`, `sunhub config notify false`."""
    from dataclasses import asdict

    cfg = load_cfg()
    current = asdict(cfg.settings)
    if key is None:
        for k, v in current.items():
            typer.echo(f"{k:<24} {v!s:<22} {SETTING_HELP.get(k, '')}")
        return
    if key not in current:
        fail(f"configuração desconhecida '{key}'. Opções: {', '.join(current)}")
    if value is None:
        typer.echo(current[key])
        return
    old = current[key]
    if isinstance(old, bool):
        if value.lower() not in ("true", "false", "1", "0", "sim", "nao", "não", "on", "off"):
            fail("use true ou false")
        new = value.lower() in ("true", "1", "sim", "on")
    elif isinstance(old, int):
        new = int(value)
    elif isinstance(old, list):
        new = [v.strip() for v in value.split(",") if v.strip()]
    else:
        new = value
    setattr(cfg.settings, key, new)
    config.save(cfg)
    ok(f"{key} = {new}")
    if key == "sync_interval_minutes" and scheduler.status() == "active":
        typer.echo("  (rode `sunhub timer enable` para aplicar ao timer)")


def _launch_app() -> None:
    try:
        from sunhub.desktop.app import main as app_main
    except ImportError as e:
        from sunhub import osutil

        hint = ("  Reinstale com: .\\app.cmd --install" if osutil.IS_WINDOWS
                else "  No Ubuntu/Debian: sudo apt install libegl1 libxkbcommon-x11-0 libxcb-cursor0")
        fail(f"não consegui carregar a interface ({e}).\n{hint}")
    raise typer.Exit(app_main())


@app.command("app")
def app_cmd():
    """Abre o app de desktop do Sunhub."""
    _launch_app()


@app.command(hidden=True)
def gui():
    """Alias de `sunhub app`."""
    _launch_app()


@app.command()
def mcp():
    """Inicia o servidor MCP via stdio (quem chama é o editor/agente)."""
    from sunhub.mcp_server import run

    run()


@app.command()
def uninstall(
    purge: bool = typer.Option(False, "--purge", help=f"Também apaga o hub ({paths.home()})."),
    yes: bool = typer.Option(False, "--yes", "-y"),
):
    """Desfaz tudo o que o Sunhub configurou no sistema (links, MCP, timer, blocos)."""
    if not yes:
        typer.confirm("Remover links de skills, registros MCP, timer e blocos de shell?", abort=True)
    for msg in setup_mod.uninstall(purge):
        ok(msg)


def main() -> None:
    import os
    import sys

    from sunhub import runtime

    if len(sys.argv) == 1 and (os.environ.get("APPIMAGE") or runtime.is_gui_exe()):
        sys.argv.append("app")  # duplo clique no AppImage / Sunhub.exe abre o app
    app()
