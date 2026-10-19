"""Wrapper fino do git do sistema (herda SSH agent, credential helper, gh auth)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from sunhub import osutil


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
        p = osutil.run(
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


def _is_partial(repo: Path) -> bool:
    try:
        return run(["config", "--get", "remote.origin.promisor"], cwd=repo) == "true"
    except GitError:
        return False


def clone(url: str, dest: Path, branch: str | None = None, sparse_paths: list[str] | None = None,
          reference: Path | None = None) -> None:
    """Clona o repositório completo (histórico de texto é pequeno e permite reaproveitar objetos).

    Com `reference` (outro clone do mesmo repositório), reaproveita os objetos que já existem
    localmente e baixa só o que falta; `--dissociate` mantém os dois clones independentes.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    use_ref = bool(reference and (reference / ".git").exists() and not _is_partial(reference))
    attempts = [True, False] if use_ref else [False]
    for with_ref in attempts:
        args = ["clone", "--quiet"]
        if with_ref:
            args += ["--reference-if-able", str(reference), "--dissociate"]
        if branch:
            args += ["--branch", branch]
        if sparse_paths:
            args.append("--sparse")
        args += ["--", url, str(dest)]
        try:
            run(args, timeout=1800)
            if sparse_paths:
                run(["sparse-checkout", "set", "--", *sparse_paths], cwd=dest)
            return
        except GitError:
            shutil.rmtree(dest, ignore_errors=True)
            if with_ref:
                continue  # sem reaproveitar objetos, tenta de novo
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


def local_ref(repo: Path, ref: str) -> str | None:
    try:
        return run(["rev-parse", "--verify", "--quiet", ref], cwd=repo) or None
    except GitError:
        return None


def remote_heads(repo: Path, branches: list[str]) -> dict[str, str]:
    """Último commit de cada branch no remoto, sem baixar nada (`git ls-remote`)."""
    out = run(["ls-remote", "--heads", "origin", *[f"refs/heads/{b}" for b in branches]], cwd=repo, timeout=60)
    heads = {}
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.startswith("refs/heads/"):
            heads[ref[len("refs/heads/"):]] = sha
    return heads


def _target_branch(repo: Path, branch: str | None) -> str:
    current = current_branch(repo)
    target = branch or (current if current != "HEAD" else default_branch(repo))
    if not target:
        raise GitError("não consegui descobrir a branch; defina 'branch' na fonte")
    return target


def update(repo: Path, branch: str | None = None, ref: str | None = None,
           also: list[str] | None = None) -> tuple[str, str, bool]:
    """Atualiza o clone com segurança (fetch + avanço ff-only; nunca sobrescreve nada).

    Antes, pergunta ao remoto só o último commit de cada branch (`ls-remote`, alguns bytes): se nada
    mudou, nem faz o fetch. `also` são outras branches que precisam estar em dia (ex.: a base de uma
    variação). Retorna (commit_antigo, commit_novo, baixou_algo).
    """
    old = head(repo)
    if not ref:
        target = _target_branch(repo, branch)
        names = [target, *[b for b in (also or []) if b and b != target]]
        try:
            heads = remote_heads(repo, names)
            unchanged = all(heads.get(n) and heads[n] == local_ref(repo, f"refs/remotes/origin/{n}") for n in names)
            if unchanged and heads[target] == old:
                return old, old, False
        except GitError:
            pass  # sem ls-remote (proxy, servidor antigo): segue com o fetch normal
    run(["fetch", "--quiet", "--prune", "--tags", "origin"], cwd=repo, timeout=900)
    if ref:
        checkout(repo, ref)
    else:
        if current_branch(repo) != target:
            run(["checkout", "--quiet", target], cwd=repo)
        try:
            run(["merge", "--ff-only", "--quiet", f"origin/{target}"], cwd=repo)
        except GitError as e:
            raise GitError(f"{e}\n→ o clone gerenciado tem alterações locais; o Sunhub não sobrescreve. "
                           "Rode `sunhub remove` + `sunhub add` para clonar de novo.") from e
    return old, head(repo), True


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


def merge_base(repo: Path, a: str, b: str) -> str:
    return run(["merge-base", a, b], cwd=repo)
