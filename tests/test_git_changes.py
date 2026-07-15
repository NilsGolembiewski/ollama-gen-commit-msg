from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gen_commit_msg.git_changes import (
    GitCommandError,
    GitOutputError,
    GitRepositoryError,
    NoChangesError,
    collect_changes,
)


class GitRepository:
    def __init__(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self._temporary_directory.name)
        self.run("init", "--quiet")
        self.run("config", "user.name", "Test User")
        self.run("config", "user.email", "test@example.com")

    def close(self) -> None:
        self._temporary_directory.cleanup()

    def run(self, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", *arguments],
            cwd=self.path,
            check=True,
            capture_output=True,
        )

    def run_unchecked(self, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", *arguments],
            cwd=self.path,
            check=False,
            capture_output=True,
        )

    def write(self, relative_path: str, content: str) -> Path:
        path = self.path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def commit_baseline(self, files: dict[str, str]) -> None:
        for relative_path, content in files.items():
            self.write(relative_path, content)
        self.run("add", "--all")
        self.run("commit", "--quiet", "-m", "Create baseline")


class GitChangeCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = GitRepository()

    def tearDown(self) -> None:
        self.repository.close()

    def test_staged_changes_take_precedence(self) -> None:
        self.repository.commit_baseline(
            {"staged.txt": "before\n", "unstaged.txt": "before\n"}
        )
        self.repository.write("staged.txt", "after\n")
        self.repository.run("add", "staged.txt")
        self.repository.write("unstaged.txt", "not staged\n")
        self.repository.write("untracked.txt", "new\n")

        expected = self.repository.run(
            "diff", "--cached", "--no-color", "--no-ext-diff"
        ).stdout.decode("utf-8")

        actual = collect_changes(self.repository.path)

        self.assertEqual(actual, expected)
        self.assertIn("staged.txt", actual)
        self.assertNotIn("unstaged.txt", actual)
        self.assertNotIn("untracked.txt", actual)

    def test_collection_from_subdirectory_covers_repository(self) -> None:
        self.repository.write("root.txt", "root change\n")
        self.repository.run("add", "root.txt")
        nested_directory = self.repository.path / "nested"
        nested_directory.mkdir()
        expected = self.repository.run(
            "diff", "--cached", "--no-color", "--no-ext-diff"
        ).stdout.decode("utf-8")

        actual = collect_changes(nested_directory)

        self.assertEqual(actual, expected)

    def test_without_staged_changes_includes_unstaged_and_untracked_diffs(self) -> None:
        self.repository.commit_baseline({"tracked.txt": "before\n"})
        self.repository.write("tracked.txt", "after\n")
        self.repository.write("nested/new.txt", "nested\n")
        self.repository.write("with space.txt", "spaced\n")
        status_before = self.repository.run("status", "--porcelain=v1", "-z").stdout

        expected = self._expected_fallback_diff()
        actual = collect_changes(self.repository.path)

        self.assertEqual(actual, expected)
        self.assertIn("tracked.txt", actual)
        self.assertIn("nested/new.txt", actual)
        self.assertIn("with space.txt", actual)
        self.assertEqual(
            self.repository.run("status", "--porcelain=v1", "-z").stdout,
            status_before,
        )

    def test_untracked_only_repository_is_supported(self) -> None:
        self.repository.write("new.txt", "new content\n")

        actual = collect_changes(self.repository.path)

        self.assertEqual(actual, self._expected_fallback_diff())
        self.assertIn("new file mode", actual)

    def test_ignored_untracked_file_is_excluded(self) -> None:
        self.repository.commit_baseline({".gitignore": "ignored.txt\n"})
        self.repository.write("visible.txt", "visible\n")
        self.repository.write("ignored.txt", "secret\n")

        actual = collect_changes(self.repository.path)

        self.assertIn("visible.txt", actual)
        self.assertNotIn("ignored.txt", actual)

    def test_empty_untracked_file_is_rendered_by_git(self) -> None:
        self.repository.write("empty.txt", "")

        actual = collect_changes(self.repository.path)

        self.assertEqual(actual, self._expected_fallback_diff())
        self.assertIn("new file mode", actual)
        self.assertIn("empty.txt", actual)

    def test_clean_repository_reports_no_changes(self) -> None:
        self.repository.commit_baseline({"tracked.txt": "unchanged\n"})

        with self.assertRaises(NoChangesError):
            collect_changes(self.repository.path)

    def test_non_repository_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(GitRepositoryError):
                collect_changes(directory)

    def test_disappearing_untracked_path_is_not_silently_omitted(self) -> None:
        completed_processes = [
            subprocess.CompletedProcess([], 0, b"/tmp/repository\n", b""),
            subprocess.CompletedProcess([], 0, b"", b""),
            subprocess.CompletedProcess([], 0, b"", b""),
            subprocess.CompletedProcess([], 0, b"vanished.txt\0", b""),
            subprocess.CompletedProcess([], 1, b"", b"error: vanished.txt"),
        ]

        with patch(
            "gen_commit_msg.git_changes._invoke_git", side_effect=completed_processes
        ):
            with self.assertRaises(GitCommandError):
                collect_changes("/tmp/repository")

    def test_non_utf8_diff_is_rejected_without_lossy_replacement(self) -> None:
        completed_processes = [
            subprocess.CompletedProcess([], 0, b"/tmp/repository\n", b""),
            subprocess.CompletedProcess([], 0, b"diff\xff", b""),
        ]

        with patch(
            "gen_commit_msg.git_changes._invoke_git", side_effect=completed_processes
        ):
            with self.assertRaisesRegex(GitOutputError, "not valid UTF-8"):
                collect_changes("/tmp/repository")

    def _expected_fallback_diff(self) -> str:
        chunks = [
            self.repository.run(
                "diff", "--no-color", "--no-ext-diff"
            ).stdout.decode("utf-8")
        ]
        untracked = self.repository.run(
            "ls-files", "--others", "--exclude-standard", "-z"
        ).stdout.split(b"\0")
        for raw_path in filter(None, untracked):
            relative_path = raw_path.decode("utf-8")
            result = self.repository.run_unchecked(
                "diff",
                "--no-index",
                "--no-color",
                "--no-ext-diff",
                "--",
                "/dev/null",
                relative_path,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, b"")
            chunks.append(result.stdout.decode("utf-8"))
        return "".join(chunks)


if __name__ == "__main__":
    unittest.main()
