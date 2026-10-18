"""Wrapper fino do git do sistema (herda SSH agent, credential helper, gh auth)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def available() -> bool:
    return shutil.which("git") is not None


def version() -> str | None:
    try:
        return run(["--version"], timeout=10).replace("git version", "").strip()
    except GitError:
        return None


def run(args: list[str], cwd: Path | str | None = None, timeout: int = 300) -> str:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"  # nunca travar pedindo senha
    try:
        p = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as e:
        raise GitError("git não está instalado") from e
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {args[0]} excedeu {timeout}s") from e
    if p.returncode != 0:
        lines = (p.stderr or p.stdout).strip().splitlines()
        raise GitError(_friendly("\n".join(lines[-6:]) or f"git {args[0]} falhou"))
    return p.stdout.strip()


def _friendly(msg: str) -> str:
    low = msg.lower()
    hints = ("could not read username", "authentication failed", "permission denied (publickey)",
             "repository not found", "could not read from remote")
    if any(h in low for h in hints):
        return msg + "\n→ sem acesso: o repositório precisa ser público ou você precisa ser colaborador (SSH ou `gh auth login`)."
    return msg


def clone(url: str, dest: Path, branch: str | None = None, sparse_paths: list[str] | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone", "--quiet", "--filter=blob:none"]
    if branch:
        args += ["--branch", branch]
    if sparse_paths:
        args.append("--sparse")
    args += ["--", url, str(dest)]
    try:
        run(args, timeout=1800)
        if sparse_paths:
            run(["sparse-checkout", "set", "--", *sparse_paths], cwd=dest)
    except GitError:
        shutil.rmtree(dest, ignore_errors=True)
        raise


def head(repo: Path) -> str:
    return run(["rev-parse", "HEAD"], cwd=repo)


def current_branch(repo: Path) -> str:
    return run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)


def default_branch(repo: Path) -> str | None:
    try:
        ref = run(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=repo)
        return ref.split("/", 1)[1] if "/" in ref else ref
    except GitError:
        return None


def checkout(repo: Path, ref: str) -> None:
    run(["checkout", "--quiet", "--detach", ref], cwd=repo)


def update(repo: Path, branch: str | None = None, ref: str | None = None) -> tuple[str, str]:
    """fetch + avanço ff-only (nunca sobrescreve nada). Retorna (commit_antigo, commit_novo)."""
    old = head(repo)
    run(["fetch", "--quiet", "--prune", "--tags", "origin"], cwd=repo, timeout=900)
    if ref:
        checkout(repo, ref)
    else:
        target = branch or (current_branch(repo) if current_branch(repo) != "HEAD" else default_branch(repo))
        if not target:
            raise GitError("não consegui descobrir a branch; defina 'branch' na fonte")
        if current_branch(repo) != target:
            run(["checkout", "--quiet", target], cwd=repo)
        try:
            run(["merge", "--ff-only", "--quiet", f"origin/{target}"], cwd=repo)
        except GitError as e:
            raise GitError(f"{e}\n→ o clone gerenciado tem alterações locais; o Sunhub não sobrescreve. "
                           "Rode `sunhub remove` + `sunhub add` para clonar de novo.") from e
    return old, head(repo)


def changed_files(repo: Path, old: str, new: str) -> list[tuple[str, str, str | None]]:
    """[(status, caminho, caminho_novo_se_renomeado)]"""
    out = run(["diff", "--name-status", "-M", old, new], cwd=repo)
    changes: list[tuple[str, str, str | None]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if not parts or not parts[0]:
            continue
        status = parts[0][0]
        if status in ("R", "C") and len(parts) >= 3:
            changes.append((status, parts[1], parts[2]))
        elif len(parts) >= 2:
            changes.append((status, parts[1], None))
    return changes


def ls_remote(url: str) -> bool:
    try:
        run(["ls-remote", "--heads", "--", url], timeout=30)
        return True
    except GitError:
        return False
