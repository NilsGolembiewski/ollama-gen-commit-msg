from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

LOCKFILE_NAMES = frozenset(
    {
        "uv.lock",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lock",
        "bun.lockb",
        "Cargo.lock",
        "composer.lock",
        "Gemfile.lock",
        "poetry.lock",
        "Pipfile.lock",
        "pdm.lock",
        "flake.lock",
        "deno.lock",
        "go.sum",
        "go.work.sum",
        "Podfile.lock",
        "Package.resolved",
        "mix.lock",
        "pubspec.lock",
        "gradle.lockfile",
    }
)
LOCKFILE_SUFFIXES = (".lock", "-lock.json", "-lock.yaml", "-lock.yml")
MAX_FILE_LINES = 300
MAX_FILE_BYTES = 16384
MAX_TOTAL_BYTES = 32768


class GitChangesError(Exception):
    """Base error for collecting Git changes."""


class GitRepositoryError(GitChangesError):
    """Raised when the command is not run in a Git repository."""


class GitCommandError(GitChangesError):
    """Raised when Git cannot produce change data."""


class NoChangesError(GitChangesError):
    """Raised when the repository has no selected changes."""


def _decode_output(output: bytes) -> str:
    """Decode Git stdout destined for the model prompt.

    Git output (diff bodies, log subjects) is raw bytes and may contain byte
    sequences that are not valid UTF-8, for example a Latin-1 encoded file in
    the changeset. Invalid bytes become U+FFFD replacement characters instead
    of aborting commit-message generation, and the result stays valid UTF-8.
    """
    return output.decode("utf-8", errors="replace")


def _decode_path(output: bytes) -> str:
    """Decode a path that will be passed to a follow-up Git command.

    `os.fsdecode` uses surrogateescape, so bytes that are not valid UTF-8
    round-trip exactly through `subprocess` instead of being replaced.
    """
    return os.fsdecode(output)


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

    root = _decode_path(result.stdout).rstrip("\n")
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
    return _decode_output(result.stdout)


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

    return [_decode_path(path) for path in result.stdout.split(b"\0") if path]


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
        return _decode_output(result.stdout)

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
    return _decode_output(result.stdout).strip("\n")


def branch_name(cwd: str | Path | None = None) -> str:
    """Return the current branch name, or an empty string if it cannot be determined.

    `git rev-parse --abbrev-ref HEAD` prints `HEAD` and exits nonzero on an
    unborn branch; both cases collapse to an empty string here.
    """
    root = _repository_root(Path(cwd) if cwd is not None else Path.cwd())
    result = _invoke_git(("rev-parse", "--abbrev-ref", "HEAD"), root)
    if result.returncode != 0:
        return ""
    name = _decode_output(result.stdout).strip()
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
    return _decode_output(result.stdout).strip("\n")


def collect_changes(cwd: str | Path | None = None) -> str:
    root = _repository_root(Path(cwd) if cwd is not None else Path.cwd())
    staged = _diff(
        ("diff", "--cached", "--no-color", "--no-ext-diff"),
        root,
    )
    if staged:
        return _compact_diff(staged)

    chunks = [
        _diff(("diff", "--no-color", "--no-ext-diff"), root),
    ]
    chunks.extend(_untracked_diff(path, root) for path in _untracked_paths(root))
    changes = "".join(chunks)
    if changes:
        return _compact_diff(changes)
    raise NoChangesError("No Git changes found.")


def _compact_diff(text: str) -> str:
    if not text:
        return text
    pieces = [_compact_piece(patch) for patch in _split_file_patches(text)]
    pieces = _enforce_total_budget(pieces)
    result = "".join(piece.text for piece in pieces)
    if result == text:
        return text
    return result


def _split_file_patches(diff: str) -> list[str]:
    starts: list[int] = []
    if diff.startswith("diff --git "):
        starts.append(0)
    index = 0
    while True:
        found = diff.find("\ndiff --git ", index)
        if found == -1:
            break
        starts.append(found + 1)
        index = found + 1
    if not starts:
        return [diff]
    parts: list[str] = []
    if starts[0] > 0:
        parts.append(diff[: starts[0]])
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(diff)
        parts.append(diff[start:end])
    return parts


@dataclass
class _DiffPiece:
    text: str
    path: str = ""
    kind: str = ""
    insertions: int = 0
    deletions: int = 0
    mode: str = "full"


def _compact_piece(patch: str) -> _DiffPiece:
    if not patch.startswith("diff --git "):
        return _DiffPiece(text=patch, mode="preamble")
    path = _patch_path(patch)
    kind = _patch_kind(patch)
    insertions, deletions = _count_edits(patch)
    piece = _DiffPiece(
        text=patch,
        path=path,
        kind=kind,
        insertions=insertions,
        deletions=deletions,
    )
    if _is_lockfile(path):
        piece.text = _omission_line("omitted lockfile", piece)
        piece.mode = "lockfile"
        return piece
    truncated = _truncate_patch(patch, path)
    if truncated != patch:
        piece.text = truncated
        piece.mode = "truncated"
    return piece


def _omission_line(label: str, piece: _DiffPiece) -> str:
    return (
        f"{label}: {piece.path} ({piece.kind}, "
        f"+{piece.insertions} -{piece.deletions})\n"
    )


def _truncate_patch(patch: str, path: str) -> str:
    lines = patch.splitlines(keepends=True)
    if not lines:
        return patch
    kept: list[str] = []
    kept_bytes = 0
    for index, line in enumerate(lines):
        encoded_length = len(line.encode("utf-8"))
        if index == 0:
            kept.append(line)
            kept_bytes += encoded_length
            continue
        if len(kept) + 1 > MAX_FILE_LINES or kept_bytes + encoded_length > MAX_FILE_BYTES:
            break
        kept.append(line)
        kept_bytes += encoded_length
    if len(kept) == len(lines):
        return patch
    prefix = "".join(kept)
    dropped_lines = len(lines) - len(kept)
    dropped_bytes = len(patch.encode("utf-8")) - kept_bytes
    return (
        f"{prefix}omitted remainder: {path} "
        f"({dropped_lines} lines, {dropped_bytes} bytes)\n"
    )


def _enforce_total_budget(pieces: list[_DiffPiece]) -> list[_DiffPiece]:
    def total_size() -> int:
        return sum(len(piece.text) for piece in pieces)

    while total_size() > MAX_TOTAL_BYTES:
        candidates = [piece for piece in pieces if piece.mode == "full"]
        if not candidates:
            candidates = [piece for piece in pieces if piece.mode == "truncated"]
        if not candidates:
            break
        largest = max(candidates, key=lambda piece: len(piece.text))
        largest.text = _omission_line("omitted large diff", largest)
        largest.mode = "large"
    return pieces


def _is_lockfile(path: str) -> bool:
    name = Path(path).name
    return name in LOCKFILE_NAMES or name.endswith(LOCKFILE_SUFFIXES)


def _patch_path(patch: str) -> str:
    header = patch.split("\n", 1)[0]
    if not header.startswith("diff --git "):
        return ""
    src, dst = _parse_diff_git_paths(header[len("diff --git ") :])
    src_path = _strip_ab_prefix(src)
    dst_path = _strip_ab_prefix(dst)
    if dst_path and dst_path != "/dev/null":
        return dst_path
    if src_path and src_path != "/dev/null":
        return src_path
    return dst_path or src_path


def _parse_diff_git_paths(rest: str) -> tuple[str, str]:
    if rest.startswith('"'):
        src, index = _read_quoted_git_path(rest, 0)
        if index < len(rest) and rest[index] == " ":
            index += 1
        if index < len(rest) and rest[index] == '"':
            dst, _ = _read_quoted_git_path(rest, index)
        else:
            dst = rest[index:]
        return src, dst
    if rest.startswith("a/") and " b/" in rest:
        src, dst = rest.split(" b/", 1)
        return src, dst
    parts = rest.split(" ", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return rest, rest


def _read_quoted_git_path(text: str, start: int) -> tuple[str, int]:
    index = start + 1
    chars: list[str] = []
    while index < len(text):
        char = text[index]
        if char == '"':
            return "".join(chars), index + 1
        if char == "\\" and index + 1 < len(text):
            next_char = text[index + 1]
            escapes = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
            chars.append(escapes.get(next_char, next_char))
            index += 2
            continue
        chars.append(char)
        index += 1
    return "".join(chars), index


def _strip_ab_prefix(path: str) -> str:
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def _patch_kind(patch: str) -> str:
    for line in patch.splitlines():
        if line.startswith("new file mode "):
            return "added"
        if line.startswith("deleted file mode "):
            return "deleted"
        if line.startswith("rename from "):
            return "renamed"
    return "modified"


def _count_edits(patch: str) -> tuple[int, int]:
    insertions = 0
    deletions = 0
    for line in patch.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            insertions += 1
        elif line.startswith("-"):
            deletions += 1
    return insertions, deletions
