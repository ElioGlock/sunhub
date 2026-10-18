from sunhub import indexer


def test_classify():
    c = indexer.classify
    assert c("skills/x/SKILL.md") == "skill"
    assert c("adrs/0001-a.md") == "adr"
    assert c("docs/decisions/foo.md") == "adr"
    assert c("ADR-7-thing.md") == "adr"
    assert c("guidelines/a.md") == "guideline"
    assert c("docs/padroes/a.md") == "guideline"
    assert c("antipatterns/a.md") == "antipattern"
    assert c("README.md") == "doc"
    assert c("logo.png") is None
    assert c("sunhub.toml") is None


def test_overrides():
    o = indexer.Overrides({"ignore": ["drafts/*"], "kinds": {"adr": ["registros/*"]}})
    assert indexer.classify("registros/x.md", o) == "adr"
    assert indexer.classify("drafts/x.md", o) is None


def test_parse_repo(knowledge_repo):
    docs = {d.path: d for d in indexer.index_source("eng", knowledge_repo, "abc")}
    assert "skills/api-standards/references/errors.md" not in docs  # arquivo auxiliar de skill
    assert "logo.png" not in docs
    skill = docs["skills/api-standards/SKILL.md"]
    assert skill.kind == "skill" and skill.ref_id == "api-standards" and "padrões" in skill.description
    a14 = docs["adrs/0014-cursor-pagination.md"]
    assert (a14.ref_id, a14.status, a14.title) == ("ADR-014", "accepted", "Paginação por cursor")
    assert a14.extra["supersedes"] == ["ADR-009"]
    a9 = docs["adrs/0009-offset.md"]
    assert (a9.ref_id, a9.status, a9.extra["superseded_by"]) == ("ADR-009", "superseded", "ADR-014")
    ap = docs["antipatterns/god-object.md"]
    assert ap.kind == "antipattern" and ap.title == "God object" and ap.tags == ["design", "acoplamento"]
    assert docs["README.md"].ref_id == "readme"


def test_status_variants():
    n = indexer.normalize_status
    assert n("Aceito") == "accepted"
    assert n("**proposto** em 2026") == "proposed"
    assert n("Superseded by ADR-3") == "superseded"
    assert n("custom") == "custom"
