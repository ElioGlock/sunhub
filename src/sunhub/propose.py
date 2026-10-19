"""O agente (ou você) propõe um ADR: arquivo no formato do repositório, numa branch, com PR.

Nunca mexe no clone gerenciado: usa um `git worktree` separado em ~/sunhub/.state/proposals.
Desligado por padrão (settings.allow_proposals) porque escreve no repositório remoto.
"""
from __future__ import annotations

import re
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath

from sunhub import git, indexer, osutil, paths
from sunhub.config import Config, Source
from sunhub.store import Store

SECTIONS = {
    "context": r"contexto|context|problema|motiva\w*",
    "decision": r"decis[ãa]o|decision",
    "alternatives": r"alternativas?(?:\s+consideradas)?|alternatives?(?:\s+considered)?|op[çc][õo]es(?:\s+consideradas)?|options?",
    "consequences": r"consequ[êe]ncias|consequences|impactos?",
}


class ProposalError(RuntimeError):
    pass


@dataclass
class Proposal:
    source: str
    number: str
    path: str
    branch: str
    file: Path
    pushed: bool
    pr_url: str | None
    message: str


def _pick_source(cfg: Config, store: Store, name: str | None) -> Source:
    if name:
        src = cfg.require(name)
    else:
        counts = Counter(d.source for d in store.list(kind="adr"))
        git_sources = [s for s in cfg.enabled_sources if s.is_git]
        if not git_sources:
            raise ProposalError("nenhuma fonte Git vinculada para receber o ADR")
        src = max(git_sources, key=lambda s: counts.get(s.name, 0))
    if not src.is_git:
        raise ProposalError(f"{src.name} não é um repositório Git")
    return src


def layout(store: Store, src: Source) -> dict:
    """Descobre pasta, numeração, padrão de nome e template a partir dos ADRs existentes."""
    adrs = [d for d in store.list(kind="adr", source=src.name) if (d.ref_id or "").startswith("ADR-")]
    numbers = [int(re.sub(r"\D", "", d.ref_id)) for d in adrs]
    folder = Counter(str(PurePosixPath(d.path).parent) for d in adrs).most_common(1)
    folder = folder[0][0] if folder else "docs/adr"
    width, prefix, sep = 4, "", "-"
    for d in adrs:
        m = re.match(r"^(adr[-_]?)?(\d+)([-_ ])", PurePosixPath(d.path).name, re.I)
        if m:
            prefix, width, sep = m.group(1) or "", len(m.group(2)), m.group(3)
            break
    root = src.checkout_dir()
    template = None
    for cand in sorted((root / folder).glob("*")) if (root / folder).is_dir() else []:
        if re.search(r"templat|modelo", cand.name, re.I) and cand.suffix.lower() in (".md", ".markdown"):
            template = cand.read_text("utf-8", errors="replace")
            break
    return {"folder": folder, "width": max(width, 3), "prefix": prefix, "sep": sep,
            "next": (max(numbers) + 1) if numbers else 1, "template": template}


def _fill_template(template: str, number: str, title: str, fields: dict[str, str]) -> str:
    _, body = indexer.split_frontmatter(template)
    text = re.sub(r"(?i)\b(ADR[-_ ]?)?(N{3,4}|X{3,4}|0{3,4})\b", lambda m: (m.group(1) or "") + number, body, count=3)
    text = re.sub(r"^#\s+.*$", f"# ADR-{number}: {title}", text, count=1, flags=re.M)
    text = re.sub(r"(?i)(AAAA-MM-DD|YYYY-MM-DD)", date.today().isoformat(), text)
    text = re.sub(r"(?im)^(\s*[-*]?\s*\**\s*(?:status|situação)\s*\**\s*:\s*\**).*$", r"\1 Proposto", text, count=1)
    lines = text.splitlines()
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = re.match(r"^(#{2,4})\s+(.+?)\s*$", line)
        key = next((k for k, pat in SECTIONS.items() if m and re.fullmatch(pat, m.group(2).strip(), re.I)), None)
        if key and fields.get(key):
            out += ["", fields[key].strip(), ""]
            i += 1
            while i < len(lines) and not re.match(r"^#{1,4}\s", lines[i]):
                i += 1
            continue
        i += 1
    return "\n".join(out).rstrip() + "\n"


def render(number: str, title: str, fields: dict[str, str], author: str | None, template: str | None) -> str:
    if template:
        return _fill_template(template, number, title, fields)
    parts = [f"# ADR-{number}: {title}", "", "- **Status:** Proposto", f"- **Data:** {date.today().isoformat()}"]
    if author:
        parts.append(f"- **Autores:** {author}")
    parts += ["", "## Contexto", "", fields.get("context", "").strip() or "_(a preencher)_", "",
              "## Decisão", "", fields.get("decision", "").strip() or "_(a preencher)_", ""]
    if fields.get("alternatives"):
        parts += ["## Alternativas consideradas", "", fields["alternatives"].strip(), ""]
    parts += ["## Consequências", "", fields.get("consequences", "").strip() or "_(a preencher)_", "",
              "_Proposto com o Sunhub._", ""]
    return "\n".join(parts)


def _git_author(repo: Path) -> str | None:
    try:
        return git.run(["config", "user.name"], cwd=repo) or None
    except git.GitError:
        return None


def _compare_url(remote: str, base: str, branch: str) -> str | None:
    m = re.match(r"^(?:https://(?:[^@/]+@)?github\.com/|git@github\.com:)([^/]+/[^/]+?)(?:\.git)?/?$", remote)
    return f"https://github.com/{m.group(1)}/compare/{base}...{branch}?expand=1" if m else None


def propose(cfg: Config, store: Store, title: str, context: str, decision: str, consequences: str,
            alternatives: str = "", source: str | None = None, push: bool = True) -> Proposal:
    title = " ".join(title.split())
    if len(title) < 4:
        raise ProposalError("informe um título para o ADR")
    src = _pick_source(cfg, store, source)
    repo = src.checkout_dir()
    if not (repo / ".git").exists():
        raise ProposalError(f"{src.name} ainda não foi clonada (rode sunhub sync)")
    lay = layout(store, src)
    number = str(lay["next"]).zfill(lay["width"])
    slug = indexer.slugify(title)[:50]
    filename = f"{lay['prefix']}{number}{lay['sep']}{slug}.md"
    rel = str(PurePosixPath(lay["folder"]) / filename)
    branch = f"sunhub/adr-{number}-{slug[:30]}"
    base = src.branch or git.default_branch(repo) or git.current_branch(repo)

    work = paths.state_dir() / "proposals" / branch.replace("/", "_")
    osutil.rmtree(work, ignore_errors=True)
    work.parent.mkdir(parents=True, exist_ok=True)
    try:
        git.run(["fetch", "--quiet", "origin", base], cwd=repo, timeout=300)
    except git.GitError:
        pass
    git.run(["worktree", "prune"], cwd=repo)
    try:
        git.run(["branch", "-D", branch], cwd=repo)
    except git.GitError:
        pass
    git.run(["worktree", "add", "--quiet", "-b", branch, str(work), f"origin/{base}"], cwd=repo)

    fields = {"context": context, "decision": decision, "consequences": consequences, "alternatives": alternatives}
    target = work / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(number, title, fields, _git_author(repo), lay["template"]), "utf-8")
    git.run(["add", rel], cwd=work)
    identity = [] if _git_author(repo) else ["-c", "user.name=Sunhub", "-c", "user.email=sunhub@localhost"]
    git.run([*identity, "commit", "--quiet", "-m", f"docs(adr): propõe ADR-{number}: {title}"], cwd=work)

    pushed, pr_url, msg = False, None, ""
    if push:
        try:
            git.run(["push", "--quiet", "-u", "origin", branch], cwd=work, timeout=300)
            pushed = True
        except git.GitError as e:
            msg = f"não consegui enviar a branch ({str(e).splitlines()[0]}); o arquivo ficou em {target}"
    if pushed:
        remote = git.run(["remote", "get-url", "origin"], cwd=repo)
        if shutil.which("gh"):
            p = osutil.run(["gh", "pr", "create", "--head", branch, "--base", base, "--title",
                                f"ADR-{number}: {title}", "--body",
                                "Proposta de ADR criada pelo Sunhub a pedido do agente. Revise antes de aceitar."],
                               cwd=work, capture_output=True, text=True, timeout=120)
            if p.returncode == 0:
                pr_url = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else None
        if not pr_url:
            pr_url = _compare_url(remote, base, branch)
        msg = f"ADR-{number} proposto na branch {branch}" + (f": {pr_url}" if pr_url else "")
        git.run(["worktree", "remove", "--force", str(work)], cwd=repo)
    elif not push:
        msg = f"ADR-{number} criado localmente na branch {branch}: {target}"
    return Proposal(src.name, number, rel, branch, target, pushed, pr_url, msg)
