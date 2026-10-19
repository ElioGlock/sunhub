import subprocess
from pathlib import Path

import pytest


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def write(root: Path, rel: str, text: str) -> None:
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, "utf-8")


def commit(repo: Path, msg: str = "update") -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", msg)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("SUNHUB_HOME", str(home / "sunhub"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for k, v in {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "T",
                 "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)
    (tmp_path / "gitconfig").write_text("[protocol \"file\"]\n\tallow = always\n[uploadpack]\n\tallowFilter = true\n")
    return home


@pytest.fixture
def knowledge_repo(tmp_path) -> Path:
    repo = tmp_path / "remote" / "eng-knowledge"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    write(repo, "README.md", "# Conhecimento de engenharia\n\nRepositório de padrões.\n")
    write(repo, "skills/api-standards/SKILL.md",
          "---\nname: api-standards\ndescription: Aplica os padrões de API ao criar endpoints.\n---\n\n"
          "1. Recursos no plural.\n2. Paginação conforme ADR vigente.\n")
    write(repo, "skills/api-standards/references/errors.md", "# Erros\n\nproblem+json\n")
    write(repo, "adrs/0014-cursor-pagination.md",
          "# Paginação por cursor\n\n**Status:** Aceito\n\nSubstitui ADR-009.\n\n"
          "## Decisão\n\nToda API de listagem usa cursor opaco.\n")
    write(repo, "adrs/0009-offset.md",
          "# ADR-009: Paginação por offset\n\n## Status\n\nSuperseded by ADR-014\n\nUsar offset e limit.\n")
    write(repo, "guidelines/rest-naming.md", "# Nomenclatura REST\n\nSubstantivos no plural: /orders\n")
    write(repo, "antipatterns/god-object.md",
          "---\ntitle: God object\ntags: [design, acoplamento]\n---\n\nUma classe concentra regras demais de serviço.\n")
    (repo / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    commit(repo, "inicial")
    return repo
