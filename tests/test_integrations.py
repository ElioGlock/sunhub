import json

from typer.testing import CliRunner

from sunhub import blocks, clients, mcp_server, setup
from sunhub.cli import app


def test_vscode_mcp_install_keeps_existing(env):
    f = env / ".config/Code/User/mcp.json"
    f.parent.mkdir(parents=True)
    f.write_text('{\n  // comentário\n  "servers": {"outro": {"command": "x"}},\n}\n')
    c = clients.get("vscode")
    assert c.detected()
    clients.install(c)
    data = json.loads(f.read_text())
    assert set(data["servers"]) == {"outro", "sunhub"}
    assert data["servers"]["sunhub"]["args"] == ["-m", "sunhub", "mcp"]
    assert clients.installed(c)
    assert list(f.parent.glob("mcp.json.sunhub-bak-*"))
    clients.uninstall(c)
    assert set(json.loads(f.read_text())["servers"]) == {"outro"}


def test_blocks_and_uninstall(env):
    p = env / ".profile"
    p.write_text("export A=1\n")
    setup.setup()
    setup.setup()  # idempotente
    text = p.read_text()
    assert text.count(blocks.SHELL[0]) == 1 and "SUNHUB_HOME" in text and text.startswith("export A=1")
    setup.uninstall()
    assert p.read_text().strip() == "export A=1"


def test_cli_flow(knowledge_repo):
    r = CliRunner()
    res = r.invoke(app, ["add", f"file://{knowledge_repo}"])
    assert res.exit_code == 0, res.output
    res = r.invoke(app, ["search", "cursor", "--kind", "adr"])
    assert "ADR-014" in res.output
    res = r.invoke(app, ["get", "adr:14", "--raw"])
    assert "cursor opaco" in res.output
    res = r.invoke(app, ["context", "listagem paginada", "--skill", "api-standards"])
    assert "api-standards" in res.output and "ADR-014" in res.output
    res = r.invoke(app, ["list"])
    assert "eng-knowledge" in res.output


def test_mcp_tools(knowledge_repo):
    CliRunner().invoke(app, ["add", f"file://{knowledge_repo}"])
    out = mcp_server.sun_get_adr("9")
    assert "ADR-009" in out and "substituído por ADR-014" in out and "Toda API de listagem" in out
    assert "<sunhub-document" in mcp_server.sun_get("skill:api-standards")
    assert "ADR-014" in mcp_server.sun_search("paginação", kind="adr")
    assert "God object" in mcp_server.sun_check_antipatterns("classe de serviço com regras demais")
    assert "eng-knowledge" in mcp_server.sun_status()
    assert "não encontrada" in mcp_server.sun_get("adr:999")
