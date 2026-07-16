from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence


class GitChangesError(Exception):
    """Base error for collecting Git changes."""


class GitRepositoryError(GitChangesError):
    """Raised when the command is not run in a Git repository."""


class GitCommandError(GitChangesError):
    """Raised when Git cannot produce change data."""


class GitOutputError(GitChangesError):
    """Raised when Git output cannot be represented in the model prompt."""


class NoChangesError(GitChangesError):
    """Raised when the repository has no selected changes."""


def _decode_output(output: bytes, description: str) -> str:
    try:
        return output.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GitOutputError(f"{description} is not valid UTF-8.") from error


def _error_detail(stderr: bytes) -> str:
    return stderr.decode("utf-8", errors="replace").strip()


def _invoke_git(arguments: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            check=False,
            capture_output=True,
        )
    except FileNotFoundError as error:
        raise GitCommandError("Git executable was not found on PATH.") from error
    except OSError as error:
        raise GitCommandError(f"Git could not be executed: {error}.") from error


def _repository_root(cwd: Path) -> Path:
    result = _invoke_git(("rev-parse", "--show-toplevel"), cwd)
    if result.returncode != 0:
        raise GitRepositoryError("Current directory is not inside a Git repository.")

    root = _decode_output(result.stdout, "Git repository path").rstrip("\n")
    if not root:
        raise GitRepositoryError("Git did not return a repository root.")
    return Path(root)


def _diff(arguments: Sequence[str], root: Path) -> str:
    result = _invoke_git(arguments, root)
    if result.returncode != 0:
        detail = _error_detail(result.stderr)
        message = "Git could not produce a diff."
        if detail:
            message = f"{message} {detail}"
        raise GitCommandError(message)
    return _decode_output(result.stdout, "Git diff output")


def _untracked_paths(root: Path) -> list[str]:
    result = _invoke_git(
        ("ls-files", "--others", "--exclude-standard", "-z"),
        root,
    )
    if result.returncode != 0:
        detail = _error_detail(result.stderr)
        message = "Git could not list untracked files."
        if detail:
            message = f"{message} {detail}"
        raise GitCommandError(message)

    output = _decode_output(result.stdout, "Git untracked file list")
    return [path for path in output.split("\0") if path]


def _untracked_diff(relative_path: str, root: Path) -> str:
    result = _invoke_git(
        (
            "diff",
            "--no-index",
            "--no-color",
            "--no-ext-diff",
            "--",
            "/dev/null",
            relative_path,
        ),
        root,
    )
    valid_difference = (
        result.returncode == 1 and bool(result.stdout) and not result.stderr
    )
    if result.returncode == 0 or valid_difference:
        return _decode_output(result.stdout, "Git untracked file diff")

    detail = _error_detail(result.stderr)
    message = f"Git could not produce a diff for untracked file {relative_path!r}."
    if detail:
        message = f"{message} {detail}"
    raise GitCommandError(message)


def recent_commits(cwd: str | Path | None = None, *, limit: int = 20) -> str:
    """Return the most recent commit messages (newest first).

    Returns an empty string for unborn branches and on any Git failure, so the
    prompt is enriched only when history actually exists.
    """
    if limit <= 0:
        return ""
    root = _repository_root(Path(cwd) if cwd is not None else Path.cwd())
    result = _invoke_git(
        ("log", f"-n{limit}", "--no-decorate", "--format=%s"),
        root,
    )
    if result.returncode != 0:
        # Unborn branch (`HEAD` has no commits yet) or a corrupt reflog: no
        # history to mirror, so degrade to an empty section.
        return ""
    return _decode_output(result.stdout, "Git log output").strip("\n")


def branch_name(cwd: str | Path | None = None) -> str:
    """Return the current branch name, or an empty string if it cannot be determined.

    `git rev-parse --abbrev-ref HEAD` prints `HEAD` and exits nonzero on an
    unborn branch; both cases collapse to an empty string here.
    """
    root = _repository_root(Path(cwd) if cwd is not None else Path.cwd())
    result = _invoke_git(("rev-parse", "--abbrev-ref", "HEAD"), root)
    if result.returncode != 0:
        return ""
    name = _decode_output(result.stdout, "Git branch name").strip()
    if not name or name == "HEAD":
        return ""
    return name


def branch_commits(
    base: str = "main", cwd: str | Path | None = None
) -> str:
    """Return commit subjects on the current branch not reachable from `base`.

    Returns an empty string when `base` does not exist (for example a fresh
    repository without a `main` branch) or on any other Git failure, so the
    prompt is enriched only when a real base is available.
    """
    root = _repository_root(Path(cwd) if cwd is not None else Path.cwd())
    result = _invoke_git(
        ("log", "--no-decorate", "--format=%s", f"{base}..HEAD"),
        root,
    )
    if result.returncode != 0:
        # Missing base ref or unborn HEAD: no thread to anchor to.
        return ""
    return _decode_output(result.stdout, "Git branch log output").strip("\n")


def collect_changes(cwd: str | Path | None = None) -> str:
    root = _repository_root(Path(cwd) if cwd is not None else Path.cwd())
    staged = _diff(
        ("diff", "--cached", "--no-color", "--no-ext-diff"),
        root,
    )
    if staged:
        return staged

    chunks = [
        _diff(("diff", "--no-color", "--no-ext-diff"), root),
    ]
    chunks.extend(_untracked_diff(path, root) for path in _untracked_paths(root))
    changes = "".join(chunks)
    if changes:
        return changes
    raise NoChangesError("No Git changes found.")
