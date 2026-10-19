"""Verifica a qualidade dos repositórios de conhecimento (o agente é tão bom quanto o que lê).

    sunhub lint            # todas as fontes vinculadas
    sunhub lint caminho/   # uma pasta qualquer (ex.: na CI do repositório de ADRs)
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from sunhub import indexer
from sunhub.models import Document

LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
INVALID = {"superseded", "deprecated", "rejected"}


@dataclass
class Issue:
    level: str  # error | warn
    source: str
    path: str
    message: str

    @property
    def sign(self) -> str:
        return "✗" if self.level == "error" else "!"


def lint_docs(docs: list[Document], source: str, root: Path | None = None,
              known_adrs: set[str] | None = None) -> list[Issue]:
    issues: list[Issue] = []
    add = lambda level, d, msg: issues.append(Issue(level, source, d.path, msg))  # noqa: E731
    adrs = [d for d in docs if d.kind == "adr"]
    ids = Counter(d.ref_id for d in adrs if (d.ref_id or "").startswith("ADR-"))
    all_ids = set(ids) | (known_adrs or set())
    by_id = {d.ref_id: d for d in adrs}

    for d in docs:
        fm, body = indexer.split_frontmatter(d.body)
        if d.kind == "skill":
            if not d.description:
                add("error", d, "skill sem 'description' no front matter: o agente não sabe quando usá-la")
            elif len(d.description) > 1024:
                add("warn", d, f"description com {len(d.description)} caracteres (o limite comum é 1024)")
            if not fm.get("name"):
                add("warn", d, f"sem 'name' no front matter; usando o nome da pasta ({d.ref_id})")
        elif d.kind == "adr":
            if not (d.ref_id or "").startswith("ADR-"):
                add("error", d, "não achei o número do ADR: use 0014-titulo.md, 'ADR-014' no título ou 'id:' no front matter")
            elif ids[d.ref_id] > 1:
                add("error", d, f"{d.ref_id} aparece em mais de um arquivo")
            if not d.status:
                add("error", d, "ADR sem status (ex.: 'Status: Aceito' ou 'status: accepted' no front matter)")
            sb = d.extra.get("superseded_by")
            if sb and sb not in all_ids:
                add("error", d, f"diz que foi substituído por {sb}, mas {sb} não existe")
            if d.status == "superseded" and not sb:
                add("warn", d, "status superseded sem dizer qual ADR o substitui ('Superseded by ADR-NNN')")
            for target in d.extra.get("supersedes", []):
                other = by_id.get(target)
                if other is None and target not in all_ids:
                    add("warn", d, f"diz que substitui {target}, mas {target} não existe")
                elif other is not None and other.status not in INVALID:
                    add("warn", d, f"substitui {target}, mas {target} ainda está como '{other.status or 'sem status'}'")
        if len(re.sub(r"\s+", "", re.sub(r"^#.*$", "", body, flags=re.M))) < 20:
            add("warn", d, "documento praticamente vazio")
        if root is not None and PurePosixPath(d.path).suffix.lower() in (".md", ".markdown", ".mdx"):
            base = (root / d.path).parent
            for target in LINK_RE.findall(body):
                if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith(("#", "/")):
                    continue
                rel = target.split("#", 1)[0]
                if rel and not (base / rel).exists():
                    add("warn", d, f"link quebrado: {target}")
    return issues


def lint_path(root: Path, name: str = "local") -> list[Issue]:
    root = Path(root).resolve()
    return lint_docs(indexer.index_source(name, root), name, root)


def lint_sources(cfg, store) -> list[Issue]:
    known = {d.ref_id for d in store.list(kind="adr") if (d.ref_id or "").startswith("ADR-")}
    issues: list[Issue] = []
    for s in cfg.enabled_sources:
        root = s.checkout_dir()
        issues += lint_docs(store.list(source=s.name), s.name, root if root.is_dir() else None, known)
    return issues


def format_issues(issues: list[Issue]) -> str:
    if not issues:
        return "✓ nenhum problema encontrado"
    lines, current = [], None
    for i in sorted(issues, key=lambda x: (x.source, x.path, x.level)):
        key = f"{i.source}/{i.path}"
        if key != current:
            lines.append(key)
            current = key
        lines.append(f"  {i.sign} {i.message}")
    e = sum(1 for i in issues if i.level == "error")
    lines.append(f"\n{e} erro(s), {len(issues) - e} aviso(s)")
    return "\n".join(lines)
