from __future__ import annotations

from dataclasses import dataclass, field

KINDS = ("skill", "adr", "guideline", "antipattern", "doc")
KIND_LABELS = {
    "skill": "Skills",
    "adr": "ADRs",
    "guideline": "Guidelines",
    "antipattern": "Antipatterns",
    "doc": "Docs",
}


@dataclass
class Document:
    source: str
    path: str  # caminho relativo (posix) dentro da fonte
    kind: str
    title: str
    body: str
    ref_id: str | None = None
    status: str | None = None
    tags: list[str] = field(default_factory=list)
    description: str | None = None
    commit_sha: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.path}"

    @property
    def ref(self) -> str:
        return f"{self.kind}:{self.ref_id}" if self.ref_id else self.uid
