import os

from conftest import commit, write

from sunhub import config, linker, resolver, sync
from sunhub.store import Store


def _add(url, name=None):
    cfg = config.load()
    src = config.source_from_location(cfg, url, name)
    with Store() as st:
        r = sync.add_source(cfg, st, src)
    return cfg, src, r


def test_clone_index_search_resolve(knowledge_repo):
    cfg, src, r = _add(f"file://{knowledge_repo}")
    assert r.ok, r.error
    assert src.name == "eng-knowledge" and r.docs == 6
    with Store() as st:
        hits = st.search("cursor")
        assert hits and hits[0][0].ref_id == "ADR-014"
        assert st.search("paginacao")  # sem acento também acha
        for ref in ("adr:14", "ADR-014", "adr:ADR-14", "14"):
            doc, _ = resolver.resolve(st, ref)
            assert doc and doc.ref_id == "ADR-014", ref
        assert resolver.resolve(st, "skill:api-standards")[0].kind == "skill"
        assert resolver.resolve(st, "guideline:rest-naming")[0].kind == "guideline"
        assert resolver.resolve(st, "antipattern:god")[0].ref_id == "god-object"
        assert resolver.resolve(st, "eng-knowledge:README.md")[0].kind == "doc"
        assert resolver.resolve(st, "adr:999")[0] is None


def test_incremental_update(knowledge_repo):
    cfg, src, _ = _add(f"file://{knowledge_repo}")
    write(knowledge_repo, "adrs/0020-auth.md", "# Autenticação OIDC\n\nStatus: proposto\n")
    write(knowledge_repo, "guidelines/rest-naming.md", "# Nomenclatura REST v2\n\nkebab-case\n")
    (knowledge_repo / "antipatterns" / "god-object.md").unlink()
    commit(knowledge_repo)
    with Store() as st:
        (r,), _ = sync.sync_all(cfg, st)
        assert r.ok and r.changed and "arquivo" in r.detail
        assert resolver.resolve(st, "adr:20")[0].status == "proposed"
        assert resolver.resolve(st, "guideline:rest-naming")[0].title == "Nomenclatura REST v2"
        assert st.count(kind="antipattern") == 0
        (r2,), _ = sync.sync_all(cfg, st)
        assert r2.ok and not r2.changed


def test_local_folder_and_remove(knowledge_repo, tmp_path):
    cfg, src, r = _add(str(knowledge_repo), "local-kb")
    assert src.local and r.ok
    with Store() as st:
        sync.remove_source(config.load(), st, "local-kb")
        assert st.count() == 0
    assert knowledge_repo.exists()  # pasta local do usuário nunca é apagada


def test_bad_url_reports_error(tmp_path):
    cfg, src, r = _add(f"file://{tmp_path}/nao-existe")
    assert not r.ok and r.error
    assert config.load().get(src.name)  # fica cadastrada para corrigir


def test_links(knowledge_repo, tmp_path, env):
    cfg, _, _ = _add(f"file://{knowledge_repo}")
    link = env / ".copilot/skills/api-standards"
    assert link.is_symlink() and (link / "SKILL.md").exists()
    assert (env / ".claude/skills/sunhub/SKILL.md").exists()  # skill roteadora
    # o usuário já tem uma skill própria com o mesmo nome em outra pasta: não mexer
    own = env / ".agents/skills/api-standards"
    linker.unlink_all()
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("minha")
    with Store() as st:
        rep = linker.relink(config.load(), st)
    assert str(own) in rep.conflicts and (own / "SKILL.md").read_text() == "minha"
    # segunda fonte com skill de mesmo nome → nomes com prefixo
    other = tmp_path / "remote" / "other"
    other.mkdir()
    os.system(f"cd {other} && git init -q -b main")
    write(other, "skills/api-standards/SKILL.md", "---\nname: api-standards\ndescription: outra\n---\n")
    commit(other)
    _add(f"file://{other}")
    names = sorted(p.name for p in (env / ".copilot/skills").iterdir())
    assert names == ["eng-knowledge--api-standards", "other--api-standards", "sunhub"]
    assert linker.unlink_all() > 0
    assert not (env / ".copilot/skills/sunhub").exists()
    assert (own / "SKILL.md").exists()
