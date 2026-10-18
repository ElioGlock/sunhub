"""CLI `sunhub`."""
from pathlib import Path
from typing import List, NoReturn, Optional

import typer

from sunhub import __version__, clients, config, linker, paths, resolver, systemd
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
timer_app = typer.Typer(help="Sync automático (systemd --user).", no_args_is_help=True)
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


@app.callback()
def _root(version: bool = typer.Option(False, "--version", callback=_version, is_eager=True, help="Mostra a versão.")):
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
    location: str = typer.Argument(..., help="URL Git (https/ssh) ou pasta local."),
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
            typer.echo(f"→ clonando {src.location} …" if src.is_git else f"→ indexando {src.local} …")
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
    try:
        with Store() as st:
            results, report = sync_mod.sync_all(cfg, st, names=names or None, full=full, blocking=not quiet)
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
):
    """Busca na base de conhecimento."""
    with Store() as st:
        hits = st.search(query, _kind(kind), source, limit)
    if not hits:
        typer.echo("Nada encontrado.")
        raise typer.Exit(1)
    for d, snip in hits:
        typer.echo(line(d, snip))


@app.command()
def get(
    ref: str = typer.Argument(..., help="adr:14 · ADR-014 · skill:nome · guideline:nome · <fonte>:<arquivo>"),
    raw: bool = typer.Option(False, "--raw", help="Só o conteúdo, sem cabeçalho."),
):
    """Imprime um documento (bom para pipes: `sunhub get adr:14 | xclip`)."""
    cfg = load_cfg()
    with Store() as st:
        doc, others = resolver.resolve(st, ref, cfg.priorities())
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
):
    """Monta um pacote de contexto para colar em ferramentas sem MCP."""
    refs = [f"adr:{a}" for a in adr or []] + [f"skill:{s}" for s in skill or []] + list(ref or [])
    if not task and not refs:
        fail("informe uma tarefa ou referências (--adr, --skill, --ref)")
    cfg = load_cfg()
    with Store() as st:
        text, missing = context_mod.build(st, task, refs, cfg.priorities())
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
        det = typer.style("detectado", fg="green") if c.detected() else typer.style("não detectado", dim=True)
        ins = typer.style("registrado", fg="green") if clients.installed(c) else "—"
        typer.echo(f"{c.key:<16} {c.label:<26} {det:<24} {ins}")


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
):
    """Adiciona um bloco curto no AGENTS.md do projeto apontando para o Sunhub."""
    for f in setup_mod.init_project(path, copilot):
        ok(f)


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
def timer_enable(interval: Optional[int] = typer.Option(None, "--interval", "-i", help="Minutos (mín. 5).")):
    """Liga o sync automático."""
    cfg = load_cfg()
    if interval:
        cfg.settings.sync_interval_minutes = max(5, interval)
        config.save(cfg)
    try:
        ok(systemd.enable(cfg.settings.sync_interval_minutes))
    except systemd.TimerError as e:
        fail(str(e))


@timer_app.command("disable")
def timer_disable():
    """Desliga o sync automático."""
    ok(systemd.disable())


@timer_app.command("status")
def timer_status():
    """Situação do timer."""
    typer.echo(systemd.status())


# ------------------------------------------------------------------ apps

def _launch_app() -> None:
    try:
        from sunhub.desktop.app import main as app_main
    except ImportError as e:
        fail(f"não consegui carregar a interface ({e}).\n"
             "  No Ubuntu/Debian: sudo apt install libegl1 libxkbcommon-x11-0 libxcb-cursor0")
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
    app()
