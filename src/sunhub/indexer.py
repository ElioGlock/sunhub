"""Classifica e extrai metadados dos arquivos de uma fonte.

Tolerante por design: ADRs e guias têm formato livre; extraímos o que der
(id, status, título) e o agente lê o corpo para o resto.
"""
from __future__ import annotations

import fnmatch
import re
import sys
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Iterator

import yaml

from sunhub.formats import RICH_EXT, html_to_markdown, pdf_to_text
from sunhub.models import KINDS, Document

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

OVERRIDE_FILE = "sunhub.toml"
SKILL_FILE = "SKILL.md"
TEXT_EXT = {".md", ".markdown", ".mdx", ".rst", ".txt", ".adoc"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".idea", ".vscode", ".tox"}
MAX_BYTES = 512 * 1024

ADR_DIRS = {"adr", "adrs", "decisions", "decision-records", "architecture-decisions",
            "architecture-decision-records", "decisoes", "decisões"}
GUIDELINE_DIRS = {"guidelines", "guideline", "standards", "conventions", "rules", "regras", "padroes",
                  "padrões", "diretrizes", "boas-praticas", "boas-práticas", "guides", "guias"}
ANTIPATTERN_DIRS = {"antipatterns", "anti-patterns", "anti_patterns", "antipattern", "antipadroes",
                    "antipadrões", "anti-padroes", "anti-padrões"}

FM_RE = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
H1_RE = re.compile(r"^#[ \t]+(.+?)[ \t#]*$", re.M)
ADR_NUM_RE = re.compile(r"\bADR[-_ ]?0*(\d{1,5})\b", re.I)
LEADING_NUM_RE = re.compile(r"^0*(\d{1,5})(?=[-_ .])")
STATUS_LINE_RE = re.compile(r"^[ \t>*_|-]*(?:status|situação|situacao|estado)[*_]*[ \t]*[:：][ \t]*[*_]*[ \t]*(.+)$", re.I | re.M)
STATUS_HEADING_RE = re.compile(r"^#{1,6}[ \t]*(?:status|situação|situacao|estado)[ \t]*$\s*^[ \t>*_-]*(.+)$", re.I | re.M)
SUPERSEDED_BY_RE = re.compile(r"(?:superseded[ \t]+by|substitu[íi]d[oa][ \t]+(?:por|pelo|pela))[ \t:*_\[]*(ADR[-_ ]?\d+)", re.I)
SUPERSEDES_RE = re.compile(r"(?:\bsupersedes\b|\bsubstitui\b)[ \t:*_\[]*(ADR[-_ ]?\d+)", re.I)

STATUS_ALIASES = {
    "accepted": "accepted", "approved": "accepted", "aceito": "accepted", "aceita": "accepted",
    "aprovado": "accepted", "aprovada": "accepted", "vigente": "accepted",
    "proposed": "proposed", "draft": "proposed", "proposto": "proposed", "proposta": "proposed",
    "rascunho": "proposed",
    "superseded": "superseded", "substituído": "superseded", "substituido": "superseded",
    "substituída": "superseded", "substituida": "superseded",
    "deprecated": "deprecated", "obsoleto": "deprecated", "obsoleta": "deprecated",
    "depreciado": "deprecated", "descontinuado": "deprecated",
    "rejected": "rejected", "rejeitado": "rejected", "rejeitada": "rejected",
}


# ---------------------------------------------------------------- helpers

def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:80] or "doc"


def adr_id(number: int | str) -> str:
    return f"ADR-{int(number):03d}"


def normalize_status(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip().strip("*_`[]()").lower()
    words = [w.strip("*_`[]()") for w in re.split(r"[\s,;.:(|]+", s)]
    first = next((w for w in words if w), "")
    if not first:
        return None
    return STATUS_ALIASES.get(first, first[:30])


def split_frontmatter(raw: str) -> tuple[dict, str]:
    m = FM_RE.match(raw)
    if not m:
        return {}, raw
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        data = {}
    return (data if isinstance(data, dict) else {}), raw[m.end():]


def _tags(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = re.split(r"[,;]", value)
    return [str(t).strip() for t in value if str(t).strip()][:20]


def _humanize(stem: str) -> str:
    s = re.sub(r"^\d+[-_ ]+", "", stem)
    s = re.sub(r"[-_]+", " ", s).strip()
    return s[:1].upper() + s[1:] if s else stem


# ---------------------------------------------------------------- overrides

class Overrides:
    """`sunhub.toml` opcional na raiz da fonte:

        ignore = ["drafts/**"]
        [kinds]
        adr = ["decisoes/**"]
        guideline = ["docs/padroes/**"]
    """

    def __init__(self, data: dict | None = None):
        data = data or {}
        kinds = data.get("kinds") or {}
        self.kinds = {k: list(v) for k, v in kinds.items() if k in KINDS and isinstance(v, list)}
        self.ignore = list(data.get("ignore") or [])

    @classmethod
    def load(cls, root: Path) -> "Overrides":
        f = root / OVERRIDE_FILE
        try:
            return cls(tomllib.loads(f.read_text("utf-8")))
        except (OSError, tomllib.TOMLDecodeError):
            return cls()

    def ignored(self, rel: str) -> bool:
        return any(fnmatch.fnmatch(rel, p) for p in self.ignore)

    def kind_for(self, rel: str) -> str | None:
        for kind, patterns in self.kinds.items():
            if any(fnmatch.fnmatch(rel, p) for p in patterns):
                return kind
        return None


# ---------------------------------------------------------------- classify

def classify(rel: str, overrides: Overrides | None = None) -> str | None:
    overrides = overrides or Overrides()
    p = PurePosixPath(rel)
    if rel == OVERRIDE_FILE or overrides.ignored(rel):
        return None
    if p.name == SKILL_FILE:
        return "skill"
    if p.suffix.lower() not in TEXT_EXT | RICH_EXT:
        return None
    forced = overrides.kind_for(rel)
    if forced:
        return forced
    segs = {s.lower() for s in p.parent.parts}
    name = p.name.lower()
    if segs & ANTIPATTERN_DIRS or "antipattern" in name or "anti-pattern" in name:
        return "antipattern"
    if segs & ADR_DIRS or re.match(r"^adr[-_ ]?\d+", name):
        return "adr"
    if segs & GUIDELINE_DIRS:
        return "guideline"
    return "doc"


def inside_skill(root: Path, rel: str) -> bool:
    """Arquivo auxiliar de uma skill (a pasta inteira é linkada; só o SKILL.md é indexado)."""
    p = PurePosixPath(rel)
    if p.name == SKILL_FILE:
        return False
    parent = p.parent
    while True:
        if (root / parent / SKILL_FILE).is_file():
            return True
        if parent == parent.parent or str(parent) in ("", "."):
            return False
        parent = parent.parent


def iter_files(root: Path) -> Iterator[Path]:
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        if (d / SKILL_FILE).is_file():
            yield d / SKILL_FILE
            continue
        for e in entries:
            if e.is_symlink():  # nunca seguir links para fora do repositório
                continue
            if e.is_dir():
                if e.name not in SKIP_DIRS:
                    stack.append(e)
            elif e.is_file():
                yield e


# ---------------------------------------------------------------- parse

def parse_document(root: Path, rel: str, source: str, kind: str, commit: str | None = None) -> Document | None:
    f = root / rel
    suffix = PurePosixPath(rel).suffix.lower()
    rich_title = ""
    if suffix == ".pdf":
        res = pdf_to_text(f)
        if not res or not res[1].strip():
            return None
        rich_title, raw = res
    else:
        try:
            if f.stat().st_size > MAX_BYTES:
                return None
            raw = f.read_text("utf-8", errors="replace")
        except OSError:
            return None
        if "\x00" in raw[:2048]:
            return None
        if suffix in (".html", ".htm"):
            rich_title, raw = html_to_markdown(raw)
            if not raw.strip():
                return None
    fm, body = split_frontmatter(raw)
    p = PurePosixPath(rel)
    tags = _tags(fm.get("tags"))

    if kind == "skill":
        name = slugify(str(fm.get("name") or p.parent.name or source))
        desc = str(fm.get("description") or "").strip() or None
        return Document(source=source, path=rel, kind="skill", title=str(fm.get("title") or name), body=raw,
                        ref_id=name, description=desc, tags=tags, commit_sha=commit)

    h1 = H1_RE.search(body)
    title = str(fm.get("title") or rich_title or (h1.group(1).strip() if h1 else _humanize(p.stem)))[:200]
    head = body[:4000]
    extra: dict = {}
    status = normalize_status(fm.get("status"))

    if kind == "adr":
        number = None
        for key in ("id", "adr", "number"):
            if fm.get(key) is not None:
                m = re.search(r"\d+", str(fm[key]))
                if m:
                    number = m.group(0)
                    break
        for text in (p.name, title):
            if number is None:
                m = ADR_NUM_RE.search(text)
                number = m.group(1) if m else None
        if number is None:
            m = LEADING_NUM_RE.match(p.name)
            number = m.group(1) if m else None
        ref_id = adr_id(number) if number is not None else slugify(p.stem)
        if not status:
            m = STATUS_LINE_RE.search(head) or STATUS_HEADING_RE.search(head)
            status = normalize_status(m.group(1)) if m else None
        sb = fm.get("superseded_by") or fm.get("superseded-by")
        m = SUPERSEDED_BY_RE.search(head)
        if sb or m:
            num = re.search(r"\d+", str(sb or m.group(1)))
            if num:
                extra["superseded_by"] = adr_id(num.group(0))
                status = status if status not in (None, "accepted") else "superseded"
        sp = fm.get("supersedes")
        found = [sp] if isinstance(sp, (str, int)) else list(sp or [])
        found += SUPERSEDES_RE.findall(head)
        sup = []
        for item in found:
            num = re.search(r"\d+", str(item))
            if num and adr_id(num.group(0)) not in sup and adr_id(num.group(0)) != ref_id:
                sup.append(adr_id(num.group(0)))
        if sup:
            extra["supersedes"] = sup
        if fm.get("date"):
            extra["date"] = str(fm.get("date"))
    else:
        stem = p.stem
        if stem.lower() in ("readme", "index") and p.parent.name:
            stem = p.parent.name
        ref_id = slugify(fm.get("id") or stem)
        if kind == "antipattern" and fm.get("severity"):
            extra["severity"] = str(fm["severity"])

    return Document(source=source, path=rel, kind=kind, title=title, body=raw, ref_id=ref_id, status=status,
                    tags=tags, description=(str(fm["description"]).strip() if fm.get("description") else None),
                    commit_sha=commit, extra=extra)


def index_file(root: Path, rel: str, source: str, commit: str | None, overrides: Overrides) -> Document | None:
    kind = classify(rel, overrides)
    if kind is None or (kind != "skill" and inside_skill(root, rel)):
        return None
    return parse_document(root, rel, source, kind, commit)


def index_source(source: str, root: Path, commit: str | None = None) -> list[Document]:
    overrides = Overrides.load(root)
    docs = []
    for f in iter_files(root):
        rel = f.relative_to(root).as_posix()
        doc = index_file(root, rel, source, commit, overrides)
        if doc:
            docs.append(doc)
    return docs


def index_paths(source: str, root: Path, rels: list[str], commit: str | None = None) -> list[Document]:
    overrides = Overrides.load(root)
    docs = []
    for rel in rels:
        if (root / rel).is_file() and not (root / rel).is_symlink():
            doc = index_file(root, rel, source, commit, overrides)
            if doc:
                docs.append(doc)
    return docs
