"""Novidades, checagem barata, lint, perfis, busca por significado, propostas de ADR, uso, formatos, AppImage."""
import asyncio
import json
import zipfile

from conftest import commit, git, write
from typer.testing import CliRunner

from sunhub import clients, config, lint, mcp_server, notify, profile, resolver, setup, sync
from sunhub import search as search_mod
from sunhub.cli import app
from sunhub.store import Store


def _add(location, name=None, branch=None):
    cfg = config.load()
    src = config.source_from_location(cfg, location, name, branch)
    with Store() as st:
        return sync.add_source(cfg, st, src)


def _sync():
    with Store() as st:
        return sync.sync_all(config.load(), st)[0]


def test_changes_feed(knowledge_repo):
    r = _add(f"file://{knowledge_repo}")
    assert r.changes[0]["change"] == "source_added"
    (knowledge_repo / "adrs" / "0014-cursor-pagination.md").write_text(
        "# Paginação por cursor\n\nStatus: Superseded by ADR-020\n\nDecisão antiga.\n")
    write(knowledge_repo, "adrs/0020-keyset.md", "# Keyset\n\nStatus: Aceito\n\nSubstitui ADR-014.\n")
    write(knowledge_repo, "skills/nova/SKILL.md", "---\nname: nova\ndescription: nova skill\n---\nx\n")
    (knowledge_repo / "guidelines" / "rest-naming.md").unlink()
    commit(knowledge_repo)
    (res,) = _sync()
    kinds = {(c["change"], c.get("ref")) for c in res.changes}
    assert ("status", "adr:ADR-014") in kinds
    assert ("added", "adr:ADR-020") in kinds and ("added", "skill:nova") in kinds
    assert ("removed", "guideline:rest-naming") in kinds
    text = notify.summarize(res.changes)
    assert "ADR-014 mudou de status: accepted → superseded" in text and "skill nova: nova" in text and "ADR novo: ADR-020 — Keyset" in text
    with Store() as st:
        assert len(st.changes()) == len(res.changes) + 1


def test_cheap_check_skips_fetch(knowledge_repo, monkeypatch):
    _add(f"file://{knowledge_repo}")
    from sunhub import git as gitmod

    calls = []
    real = gitmod.run

    def spy(args, *a, **kw):
        calls.append(args[0])
        return real(args, *a, **kw)

    monkeypatch.setattr(gitmod, "run", spy)
    (res,) = _sync()
    assert res.ok and "nada a baixar" in res.detail and "fetch" not in calls and "ls-remote" in calls
    write(knowledge_repo, "guidelines/nova.md", "# Nova\n\nconteúdo suficiente aqui\n")
    commit(knowledge_repo)
    calls.clear()
    (res,) = _sync()
    assert "fetch" in calls and res.changed


def test_lint(tmp_path):
    kb = tmp_path / "kb"
    write(kb, "adrs/0001-a.md", "# A\n\nSem status aqui, mas texto suficiente.\n")
    write(kb, "adrs/0002-b.md", "# B\n\nStatus: superseded by ADR-099\n\nVeja [guia](../guias/nao-existe.md).\n")
    write(kb, "adrs/0003-c.md", "# C\n\nStatus: aceito\n\nSubstitui ADR-002 e vale a partir de hoje.\n")
    write(kb, "skills/x/SKILL.md", "---\nname: x\n---\ncorpo da skill com instruções\n")
    issues = lint.lint_path(kb, "kb")
    msgs = "\n".join(f"{i.level} {i.path}: {i.message}" for i in issues)
    assert "error adrs/0001-a.md: ADR sem status" in msgs
    assert "error adrs/0002-b.md: diz que foi substituído por ADR-099, mas ADR-099 não existe" in msgs
    assert "warn adrs/0002-b.md: link quebrado: ../guias/nao-existe.md" in msgs
    assert "error skills/x/SKILL.md: skill sem 'description'" in msgs
    res = CliRunner().invoke(app, ["lint", str(kb)])
    assert res.exit_code == 1 and "erro(s)" in res.output


def test_project_profile(knowledge_repo, tmp_path, monkeypatch):
    _add(f"file://{knowledge_repo}")
    project = tmp_path / "meu-projeto"
    (project / "src").mkdir(parents=True)
    profile.write(project, ["outra-fonte"])
    monkeypatch.chdir(project / "src")
    r = CliRunner()
    assert r.invoke(app, ["search", "cursor"]).exit_code == 1
    assert "ADR-014" in r.invoke(app, ["search", "cursor", "--all"]).output
    assert "Nada encontrado" in asyncio.run(mcp_server.sun_search("cursor", project=str(project)))
    profile.write(project, ["eng-*"])
    assert "ADR-014" in asyncio.run(mcp_server.sun_search("cursor", project=str(project)))


def test_semantic_pipeline(knowledge_repo, monkeypatch):
    monkeypatch.setenv("SUNHUB_EMBEDDER", "hash")
    cfg = config.load()
    cfg.settings.semantic = True
    config.save(cfg)
    _add(f"file://{knowledge_repo}")
    with Store() as st:
        assert len(st.vectors("hash-256")) == st.count()
        hits = search_mod.search(st, "classe concentra regras", limit=3)
        assert hits and hits[0][0].ref_id == "god-object"
    (knowledge_repo / "antipatterns" / "god-object.md").unlink()
    commit(knowledge_repo)
    _sync()
    with Store() as st:
        assert len(st.vectors("hash-256")) == st.count()


def test_propose_adr(knowledge_repo, tmp_path):
    bare = tmp_path / "remote.git"
    git(tmp_path, "clone", "-q", "--bare", str(knowledge_repo), str(bare))
    _add(f"file://{bare}", "adrs")
    from sunhub import propose

    cfg = config.load()
    managed = cfg.require("adrs").checkout_dir()
    head_before = git(managed, "rev-parse", "HEAD")
    with Store() as st:
        p = propose.propose(cfg, st, "Usar Kafka para eventos", "Precisamos de eventos.", "Kafka.", "Mais infra.")
    assert p.pushed and p.number == "0015" and p.path == "adrs/0015-usar-kafka-para-eventos.md"
    assert p.branch in git(bare, "branch", "--list", "sunhub/*")
    content = git(bare, "show", f"{p.branch}:{p.path}")
    assert "# ADR-0015: Usar Kafka para eventos" in content and "Proposto" in content and "Kafka." in content
    assert git(managed, "rev-parse", "HEAD") == head_before and git(managed, "status", "--porcelain") == ""


def test_usage_report(knowledge_repo):
    _add(f"file://{knowledge_repo}")
    asyncio.run(mcp_server.sun_get("adr:14"))
    asyncio.run(mcp_server.sun_get("adr:14"))
    asyncio.run(mcp_server.sun_search("kubernetes helm charts"))
    with Store() as st:
        rep = st.usage_report()
    assert rep["top"][0]["ref_id"] == "ADR-014" and rep["top"][0]["n"] == 2
    assert rep["gaps"][0]["query"] == "kubernetes helm charts"
    assert "ADR-014" not in {u["ref_id"] for u in rep["unread"]}


def _pdf(text: str) -> bytes:
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 144] /Contents 4 0 R "
            "/Resources << /Font << /F1 5 0 R >> >> >>", None,
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    stream = f"BT /F1 14 Tf 20 100 Td ({text}) Tj ET".encode()
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        body = (f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream") if o is None else o.encode()
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def test_zip_export_with_html_and_pdf(tmp_path):
    z = tmp_path / "confluence-export.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Espaco/Padroes-de-logs.html",
                    "<html><head><title>Padrões de logs</title><style>x{}</style></head><body>"
                    "<h1>Padrões de logs</h1><p>Sempre JSON com <b>trace_id</b>.</p><ul><li>nível</li></ul></body></html>")
        zf.writestr("Espaco/manual.pdf", _pdf("Politica de retencao de dados por noventa dias"))
        zf.writestr("../fora.md", "# escapou\n")
    r = _add(str(z))
    assert r.ok, r.error
    with Store() as st:
        docs = {d.path: d for d in st.list(source="confluence-export")}
        assert docs["Espaco/Padroes-de-logs.html"].title == "Padrões de logs"
        assert "**trace_id**" in docs["Espaco/Padroes-de-logs.html"].body
        assert "retencao" in docs["Espaco/manual.pdf"].body
        assert not any("fora" in p for p in docs)
        assert st.search("retencao")[0][0].path == "Espaco/manual.pdf"
    (res,) = _sync()
    assert res.detail == "em dia"


def test_appimage_runtime(tmp_path, monkeypatch, env):
    img = tmp_path / "Sunhub-x86_64.AppImage"
    img.write_text("#!/bin/sh\n")
    monkeypatch.setenv("APPIMAGE", str(img))
    spec = clients.server_spec()
    assert spec["command"] == str(img) and spec["args"] == ["mcp"]
    link = setup.link_cli()
    assert link and f'exec "{img}"' in (env / ".local/bin/sunhub").read_text()
    entry = setup.install_launcher()
    assert f"Exec={img} app" in open(entry).read()


def test_mcp_install_is_idempotent(env):
    (env / ".config/Code").mkdir(parents=True)
    c = clients.get("vscode")
    clients.install(c)
    first = (env / ".config/Code/User/mcp.json").read_text()
    assert "já registrado" in clients.install(c)
    assert (env / ".config/Code/User/mcp.json").read_text() == first
    assert not list((env / ".config/Code/User").glob("*.sunhub-bak-*"))
    assert json.loads(first)["servers"]["sunhub"]["args"][-1] == "mcp"
    assert resolver  # import usado
