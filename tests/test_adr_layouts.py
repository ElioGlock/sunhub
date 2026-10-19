"""ADRs em pastas numeradas e com escopo por stack (ex.: docs/02-adrs/stacks/flutter/ADR-flutter-0001-x.md)."""
from conftest import commit, git, write

from sunhub import config, indexer, resolver, sync
from sunhub.store import Store

FLUTTER = """# ADR-flutter-0001: Arquitetura modular com Clean Architecture

**UUID do documento:** de03ae77

Metadados

- Status: proposed
- Data: 2026-10-01
- Escopo: flutter

## Contexto

Apps Flutter corporativos concentram jornadas de negócio.
"""
DOTNET_2 = """# ADR-dotnet-0002: Remoção do Fluent Assertions

- Status: accepted
- Data: 2026-09-10

Substitui ADR-dotnet-0001.
"""
DOTNET_1 = """# ADR-dotnet-0001: Fluent Assertions nos testes

- Status: superseded

Substituído por ADR-dotnet-0002.
"""


def test_classify_numbered_and_scoped():
    c = indexer.classify
    assert c("docs/02-adrs/stacks/flutter/ADR-flutter-0001-arquitetura.md") == "adr"
    assert c("docs/02-adrs/stacks/dotnet/qualquer-nome.md") == "adr"  # pasta "02-adrs"
    assert c("docs/02-adrs/README.md") == "doc"
    assert c("docs/02-adrs/stacks/flutter/README.md") == "doc"
    assert c("docs/02-adrs/template.md") == "doc"
    assert c("docs/02-adrs/ADR-0000-template.md") == "doc"
    assert c("notas/ADR-dotnet-0003-validacao.md") == "adr"  # nome já diz que é ADR
    assert c("docs/03-guidelines/x.md") == "guideline"
    assert c("docs/04-antipatterns/x.md") == "antipattern"
    assert c("docs/adr-guide-notes.md") == "doc"
    assert c("docs/2024-adrs-notes.md") == "doc"


def test_scoped_ids():
    assert indexer.parse_adr_ref("ADR-flutter-0001: X") == ("flutter", 1)
    assert indexer.parse_adr_ref("ADR-014") == (None, 14)
    assert indexer.parse_adr_ref("ADR 14") == (None, 14)
    assert indexer.parse_adr_ref("adr_front-end_0003") == ("front-end", 3)
    assert indexer.parse_adr_ref("ADR de 2025") is None
    assert indexer.adr_id(1, "Flutter") == "ADR-flutter-0001"
    assert indexer.parse_adr_key("flutter-1") == ("flutter", 1)
    assert indexer.parse_adr_key("12") == (None, 12)


def _repo(tmp_path):
    repo = tmp_path / "remote" / "architecture-standards"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    write(repo, "docs/02-adrs/README.md", "# ADRs\n\nÍndice das decisões por stack.\n")
    write(repo, "docs/02-adrs/stacks/flutter/ADR-flutter-0001-arquitetura-modular.md", FLUTTER)
    write(repo, "docs/02-adrs/stacks/dotnet/ADR-dotnet-0001-fluent-assertions.md", DOTNET_1)
    write(repo, "docs/02-adrs/stacks/dotnet/ADR-dotnet-0002-remocao-fluent-assertions.md", DOTNET_2)
    commit(repo, "inicial")
    return repo


def test_parse_scoped_adrs(tmp_path):
    docs = {d.path: d for d in indexer.index_source("as", _repo(tmp_path))}
    f = docs["docs/02-adrs/stacks/flutter/ADR-flutter-0001-arquitetura-modular.md"]
    assert (f.kind, f.ref_id, f.status) == ("adr", "ADR-flutter-0001", "proposed")
    assert f.extra["scope"] == "flutter" and f.extra["date"] == "2026-10-01"
    d1 = docs["docs/02-adrs/stacks/dotnet/ADR-dotnet-0001-fluent-assertions.md"]
    d2 = docs["docs/02-adrs/stacks/dotnet/ADR-dotnet-0002-remocao-fluent-assertions.md"]
    assert (d1.status, d1.extra["superseded_by"]) == ("superseded", "ADR-dotnet-0002")
    assert d2.extra["supersedes"] == ["ADR-dotnet-0001"]
    assert docs["docs/02-adrs/README.md"].kind == "doc"


def test_resolve_scoped(tmp_path):
    repo = _repo(tmp_path)
    cfg = config.load()
    cfg.add(config.Source(name="as", url=repo.as_uri()))
    with Store() as st:
        sync.sync_all(cfg, st, relink=False)
        for ref in ("ADR-flutter-0001", "adr:flutter-1", "adr:ADR-FLUTTER-1", "flutter-0001"):
            doc, _ = resolver.resolve(st, ref if not ref.startswith("flutter") else "adr:" + ref)
            assert doc and doc.ref_id == "ADR-flutter-0001", ref
        doc, others = resolver.resolve(st, "adr:1")  # sem escopo: os dois "0001"
        assert {doc.ref_id, *[o.ref_id for o in others]} == {"ADR-flutter-0001", "ADR-dotnet-0001"}


def test_upgrade_reindexes_quietly(tmp_path):
    repo = _repo(tmp_path)
    cfg = config.load()
    cfg.add(config.Source(name="as", url=repo.as_uri()))
    with Store() as st:
        sync.sync_all(cfg, st, relink=False)
        # simula um índice feito por uma versão antiga, que tratava tudo como doc
        st.conn.execute("UPDATE documents SET kind = 'doc'")
        st.conn.commit()
        st.meta_set("indexer:as", "1")
        n_changes = len(st.conn.execute("SELECT * FROM changes").fetchall())
        results, _ = sync.sync_all(cfg, st, relink=False)
        assert results[0].ok
        assert len(st.list(kind="adr")) == 3
        assert len(st.conn.execute("SELECT * FROM changes").fetchall()) == n_changes  # nada de "novidades"
        assert st.meta_get("indexer:as") == indexer.VERSION
