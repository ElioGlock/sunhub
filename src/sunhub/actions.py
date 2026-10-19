"""Operações compostas usadas pelo app de desktop (e reutilizáveis na CLI)."""
from __future__ import annotations

from sunhub import clients, config, scheduler
from sunhub import setup as setup_mod
from sunhub import sync as sync_mod
from sunhub.config import LINK_TARGETS, ConfigError
from sunhub.store import Store


def first_line(text: str | None) -> str:
    return text.strip().splitlines()[0] if text and text.strip() else ""


def sync_message(results, report) -> tuple[str, str]:
    bad = [r for r in results if not r.ok]
    good = [r for r in results if r.ok]
    parts = []
    if good:
        changed = [r for r in good if r.changed]
        parts.append(f"{len(good)} fonte(s) em dia" + (f", {len(changed)} atualizada(s)" if changed else ""))
    if report and report.created:
        parts.append(f"{len(report.created)} skill(s) publicada(s)")
    for r in bad:
        parts.append(f"{r.name}: {first_line(r.error)}")
    return ("; ".join(parts) or "nada a sincronizar"), ("warn" if bad else "ok")


def first_setup(urls: list[str], targets: list[str], client_keys: list[str], timer: bool) -> tuple[str, str]:
    setup_mod.setup()
    cfg = config.load()
    cfg.settings.link_targets = [t for t in targets if t in LINK_TARGETS]
    errors: list[str] = []
    for u in urls:
        try:
            cfg.add(config.source_from_location(cfg, u))
        except ConfigError as e:
            errors.append(f"{u}: {e}")
    config.save(cfg)
    with Store() as st:
        results, report = sync_mod.sync_all(cfg, st)
    for r in results:
        if not r.ok:
            errors.append(f"{r.name}: {first_line(r.error)}")
    for key in client_keys:
        try:
            clients.install(clients.get(key))
        except clients.ClientError as e:
            errors.append(first_line(str(e)))
    if timer:
        try:
            scheduler.enable(cfg.settings.sync_interval_minutes)
        except scheduler.TimerError as e:
            errors.append(f"timer: {first_line(str(e))}")
    ok_n = sum(1 for r in results if r.ok)
    msg = f"{ok_n} fonte(s) vinculada(s), {report.total if report else 0} link(s) de skills"
    if errors:
        msg += ". Problemas: " + " | ".join(errors)
    return msg, ("warn" if errors else "ok")


def add_source(location: str, name: str | None = None, branch: str | None = None) -> tuple[str, str]:
    cfg = config.load()
    src = config.source_from_location(cfg, location, name, branch)
    with Store() as st:
        r = sync_mod.add_source(cfg, st, src)
    if r and r.ok:
        if "só diferenças" in r.detail:
            return (f"{r.name}: mesma origem de outra fonte, então baixei só o que faltava e indexei só o que a "
                    f"branch tem de diferente ({r.docs} documento(s))"), "ok"
        return f"{r.name}: {r.detail}, {r.docs} documentos", "ok"
    return f"{src.name} cadastrada, mas falhou: {first_line(r.error if r else '')}", "warn"


def sync(names: list[str] | None = None) -> tuple[str, str]:
    cfg = config.load()
    with Store() as st:
        return sync_message(*sync_mod.sync_all(cfg, st, names=names))


def remove_source(name: str) -> tuple[str, str]:
    cfg = config.load()
    with Store() as st:
        sync_mod.remove_source(cfg, st, name)
    return f"{name} removida", "ok"
