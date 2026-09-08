from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gen_commit_msg.git_changes import (
    GitCommandError,
    GitRepositoryError,
    NoChangesError,
    branch_commits,
    branch_name,
    collect_changes,
    recent_commits,
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

    def test_non_utf8_diff_content_is_replaced_not_fatal(self) -> None:
        self.repository.commit_baseline({"tracked.txt": "unchanged\n"})
        (self.repository.path / "latin1.txt").write_bytes(b"caf\xe9 lat\xe9\n")
        self.repository.run("add", "latin1.txt")

        actual = collect_changes(self.repository.path)

        self.assertIn("latin1.txt", actual)
        self.assertIn("caf\ufffd lat\ufffd\n", actual)
        self.assertNotIn("caf\xe9", actual)
        actual.encode("utf-8")  # prompt-safe: no surrogate escapes leaked

    def test_non_utf8_untracked_filename_is_rendered(self) -> None:
        self.repository.commit_baseline({"tracked.txt": "unchanged\n"})
        raw_name = os.fsdecode(b"caf\xff.txt")
        (self.repository.path / raw_name).write_bytes(b"payload\n")

        actual = collect_changes(self.repository.path)

        self.assertIn("payload", actual)
        encoded = actual.encode("utf-8")  # prompt-safe: no surrogate escapes
        self.assertIn(b'"a/caf\\377.txt"', encoded)

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


class DiffCompactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = GitRepository()

    def tearDown(self) -> None:
        self.repository.close()

    def _cached_diff(self, *paths: str) -> str:
        return self.repository.run(
            "diff", "--cached", "--no-color", "--no-ext-diff", "--", *paths
        ).stdout.decode("utf-8")

    def _cached_numstat(self, relative_path: str) -> tuple[int, int]:
        output = self.repository.run(
            "diff",
            "--cached",
            "--numstat",
            "--no-color",
            "--no-ext-diff",
            "--",
            relative_path,
        ).stdout.decode("utf-8").strip()
        insertions, deletions, _path = output.split("\t", 2)
        return int(insertions), int(deletions)

    def _untracked_numstat(self, relative_path: str) -> tuple[int, int]:
        result = self.repository.run_unchecked(
            "diff",
            "--no-index",
            "--numstat",
            "--no-color",
            "--no-ext-diff",
            "--",
            "/dev/null",
            relative_path,
        )
        output = result.stdout.decode("utf-8").strip()
        insertions, deletions, _path = output.split("\t", 2)
        return int(insertions), int(deletions)

    def test_staged_lockfile_is_omitted_next_to_source_patch(self) -> None:
        lock_before = "version = 1\n" + "".join(f"pkg-{i}\n" for i in range(10))
        lock_after = "version = 1\n" + "".join(f"pkg-{i}\n" for i in range(40))
        self.repository.commit_baseline(
            {"src/app.py": "print('before')\n", "uv.lock": lock_before}
        )
        self.repository.write("src/app.py", "print('after')\n")
        self.repository.write("uv.lock", lock_after)
        self.repository.run("add", "src/app.py", "uv.lock")

        insertions, deletions = self._cached_numstat("uv.lock")
        source_patch = self._cached_diff("src/app.py")

        actual = collect_changes(self.repository.path)

        self.assertEqual(
            actual,
            source_patch
            + f"omitted lockfile: uv.lock (modified, +{insertions} -{deletions})\n",
        )
        self.assertNotIn("pkg-39", actual)

    def test_added_and_deleted_lockfiles_are_omitted_with_kind(self) -> None:
        self.repository.commit_baseline({"yarn.lock": "old-lock\nline-2\n"})
        self.repository.write("package-lock.json", "new-lock\nline-2\nline-3\n")
        self.repository.run("rm", "yarn.lock")
        self.repository.run("add", "package-lock.json")

        added_ins, added_del = self._cached_numstat("package-lock.json")
        deleted_ins, deleted_del = self._cached_numstat("yarn.lock")

        actual = collect_changes(self.repository.path)

        self.assertEqual(
            actual,
            f"omitted lockfile: package-lock.json (added, +{added_ins} -{added_del})\n"
            f"omitted lockfile: yarn.lock (deleted, +{deleted_ins} -{deleted_del})\n",
        )
        self.assertNotIn("new-lock", actual)
        self.assertNotIn("old-lock", actual)

    def test_untracked_nested_lockfile_is_omitted_without_mutating_status(self) -> None:
        self.repository.commit_baseline({"tracked.txt": "unchanged\n"})
        self.repository.write(
            "frontend/pnpm-lock.yaml",
            "lockfileVersion: 9\n" + "".join(f"dep-{i}: 1.0.{i}\n" for i in range(20)),
        )
        status_before = self.repository.run("status", "--porcelain=v1", "-z").stdout
        insertions, deletions = self._untracked_numstat("frontend/pnpm-lock.yaml")

        actual = collect_changes(self.repository.path)

        self.assertEqual(
            actual,
            "omitted lockfile: frontend/pnpm-lock.yaml "
            f"(added, +{insertions} -{deletions})\n",
        )
        self.assertNotIn("dep-19", actual)
        self.assertEqual(
            self.repository.run("status", "--porcelain=v1", "-z").stdout,
            status_before,
        )

    def test_lockfile_only_change_is_still_a_change(self) -> None:
        self.repository.commit_baseline({"Cargo.lock": "version = 3\nname = \"a\"\n"})
        self.repository.write("Cargo.lock", "version = 3\nname = \"b\"\n")
        self.repository.run("add", "Cargo.lock")
        insertions, deletions = self._cached_numstat("Cargo.lock")

        actual = collect_changes(self.repository.path)

        self.assertEqual(
            actual,
            f"omitted lockfile: Cargo.lock (modified, +{insertions} -{deletions})\n",
        )
        self.assertNotIn('name = "b"', actual)

    def test_lock_suffix_omits_app_lock_but_not_notes_lock_md(self) -> None:
        self.repository.commit_baseline(
            {
                "vendor/app.lock": "held\n",
                "notes.lock.md": "notes before\n",
            }
        )
        self.repository.write("vendor/app.lock", "held\nreleased\n")
        self.repository.write("notes.lock.md", "notes after\n")
        self.repository.run("add", "vendor/app.lock", "notes.lock.md")

        notes_patch = self._cached_diff("notes.lock.md")
        insertions, deletions = self._cached_numstat("vendor/app.lock")

        actual = collect_changes(self.repository.path)

        self.assertEqual(
            actual,
            notes_patch
            + f"omitted lockfile: vendor/app.lock (modified, +{insertions} -{deletions})\n",
        )
        self.assertIn("notes after", actual)
        self.assertNotIn("released", actual)

    def test_go_sum_is_omitted_as_named_lockfile(self) -> None:
        self.repository.commit_baseline({"go.sum": "mod@v1 h1:aaa\n"})
        self.repository.write("go.sum", "mod@v1 h1:bbb\n")
        self.repository.run("add", "go.sum")
        insertions, deletions = self._cached_numstat("go.sum")

        actual = collect_changes(self.repository.path)

        self.assertEqual(
            actual,
            f"omitted lockfile: go.sum (modified, +{insertions} -{deletions})\n",
        )
        self.assertNotIn("h1:bbb", actual)

    def test_spaced_source_path_stays_literal_next_to_lockfile(self) -> None:
        self.repository.commit_baseline(
            {"my file.txt": "before\n", "uv.lock": "version = 1\n"}
        )
        self.repository.write("my file.txt", "after\n")
        self.repository.write("uv.lock", "version = 2\n")
        self.repository.run("add", "my file.txt", "uv.lock")

        source_patch = self._cached_diff("my file.txt")
        insertions, deletions = self._cached_numstat("uv.lock")

        actual = collect_changes(self.repository.path)

        self.assertEqual(
            actual,
            source_patch
            + f"omitted lockfile: uv.lock (modified, +{insertions} -{deletions})\n",
        )
        self.assertIn("after", actual)

    def test_oversized_file_patch_is_truncated_with_remainder_counts(self) -> None:
        lines = [f"line-{index:04d}\n" for index in range(400)]
        self.repository.write("big.txt", "".join(lines))
        self.repository.run("add", "big.txt")
        original = self._cached_diff("big.txt")

        actual = collect_changes(self.repository.path)

        self.assertTrue(actual.startswith("diff --git a/big.txt b/big.txt\n"))
        self.assertIn("omitted remainder: big.txt (", actual)
        self.assertNotIn("line-0399", actual)
        prefix, trailer = actual.split("omitted remainder: big.txt ", 1)
        self.assertTrue(original.startswith(prefix))
        self.assertLess(len(prefix), len(original))
        dropped = original[len(prefix) :]
        self.assertIn(f"({dropped.count(chr(10))} lines, {len(dropped.encode('utf-8'))} bytes)", trailer)
        prefix_lines = prefix.splitlines(keepends=True)
        self.assertLessEqual(len(prefix_lines), 300)
        self.assertLessEqual(len(prefix.encode("utf-8")), 16384)
        next_line = dropped.splitlines(keepends=True)[0]
        self.assertTrue(
            len(prefix_lines) + 1 > 300
            or len(prefix.encode("utf-8")) + len(next_line.encode("utf-8")) > 16384
        )

    def test_patch_just_under_per_file_limits_stays_literal(self) -> None:
        self.repository.write(
            "medium.txt", "".join(f"row-{index:03d}\n" for index in range(200))
        )
        self.repository.run("add", "medium.txt")
        expected = self._cached_diff("medium.txt")

        actual = collect_changes(self.repository.path)

        self.assertEqual(actual, expected)
        self.assertNotIn("omitted remainder:", actual)

    def test_total_budget_demotes_largest_full_patches(self) -> None:
        payload = ("x" * 100 + "\n") * 140
        for name in ("a.txt", "b.txt", "c.txt"):
            self.repository.write(name, payload)
        self.repository.run("add", "a.txt", "b.txt", "c.txt")
        originals = {name: self._cached_diff(name) for name in ("a.txt", "b.txt", "c.txt")}
        for original in originals.values():
            self.assertLessEqual(len(original.splitlines()), 300)
            self.assertLessEqual(len(original.encode("utf-8")), 16384)
        self.assertGreater(sum(len(text) for text in originals.values()), 32768)

        actual = collect_changes(self.repository.path)

        self.assertLessEqual(len(actual), 32768)
        self.assertIn("omitted large diff:", actual)
        for name in ("a.txt", "b.txt", "c.txt"):
            self.assertTrue(
                name in actual,
                f"{name} must still be listed",
            )
            if f"omitted large diff: {name} " in actual:
                insertions, deletions = self._cached_numstat(name)
                self.assertIn(
                    f"omitted large diff: {name} (added, +{insertions} -{deletions})\n",
                    actual,
                )
            else:
                self.assertIn(originals[name], actual)

    def test_mixed_source_generated_and_lockfile_are_compacted_together(self) -> None:
        self.repository.commit_baseline(
            {
                "src/app.py": "print('before')\n",
                "uv.lock": "version = 1\n",
            }
        )
        self.repository.write("src/app.py", "print('after')\n")
        self.repository.write("uv.lock", "version = 2\n" + "".join(f"pkg-{i}\n" for i in range(30)))
        self.repository.write(
            "data.json", "".join(f'{{"i": {index}, "pad": "{"y" * 40}"}}\n' for index in range(400))
        )
        self.repository.run("add", "src/app.py", "uv.lock", "data.json")
        source_patch = self._cached_diff("src/app.py")
        lock_ins, lock_del = self._cached_numstat("uv.lock")

        actual = collect_changes(self.repository.path)

        self.assertIn(source_patch, actual)
        self.assertIn(
            f"omitted lockfile: uv.lock (modified, +{lock_ins} -{lock_del})\n",
            actual,
        )
        self.assertNotIn("pkg-29", actual)
        self.assertNotIn('"i": 399', actual)
        self.assertTrue(
            "omitted remainder: data.json " in actual
            or "omitted large diff: data.json " in actual
        )

    def test_untracked_oversized_file_is_truncated(self) -> None:
        self.repository.commit_baseline({"tracked.txt": "unchanged\n"})
        self.repository.write(
            "new-big.txt", "".join(f"fresh-{index:04d}\n" for index in range(400))
        )

        actual = collect_changes(self.repository.path)

        self.assertTrue(actual.startswith("diff --git "))
        self.assertIn("omitted remainder: new-big.txt (", actual)
        self.assertNotIn("fresh-0399", actual)


class CommitHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = GitRepository()

    def tearDown(self) -> None:
        self.repository.close()

    def _commit(self, subject: str, files: dict[str, str]) -> None:
        for relative_path, content in files.items():
            self.repository.write(relative_path, content)
        self.repository.run("add", "--all")
        self.repository.run("commit", "--quiet", "-m", subject)

    def test_recent_commits_returns_subjects_newest_first(self) -> None:
        self._commit("feat: initial", {"a.txt": "1\n"})
        self._commit("fix: leak", {"a.txt": "2\n"})
        self._commit("docs: readme", {"README.md": "x\n"})

        result = recent_commits(self.repository.path, limit=20)

        self.assertEqual(
            result,
            "docs: readme\nfix: leak\nfeat: initial",
        )

    def test_recent_commits_respects_limit(self) -> None:
        for index in range(5):
            self._commit(f"feat: {index}", {"a.txt": f"{index}\n"})

        result = recent_commits(self.repository.path, limit=3)

        self.assertEqual(result, "feat: 4\nfeat: 3\nfeat: 2")

    def test_recent_commits_zero_limit_returns_empty(self) -> None:
        self._commit("feat: only", {"a.txt": "1\n"})

        self.assertEqual(recent_commits(self.repository.path, limit=0), "")

    def test_recent_commits_unborn_branch_returns_empty(self) -> None:
        self.repository.write("a.txt", "1\n")

        self.assertEqual(recent_commits(self.repository.path), "")

    def test_branch_name_returns_current_branch(self) -> None:
        self._commit("feat: initial", {"a.txt": "1\n"})
        self.repository.run("checkout", "--quiet", "-b", "feature/123-ss0")

        self.assertEqual(
            branch_name(self.repository.path), "feature/123-ss0"
        )

    def test_branch_name_unborn_branch_returns_empty(self) -> None:
        self.repository.write("a.txt", "1\n")

        self.assertEqual(branch_name(self.repository.path), "")

    def test_branch_name_detached_head_returns_empty(self) -> None:
        self._commit("feat: initial", {"a.txt": "1\n"})
        self.repository.run("checkout", "--quiet", "--detach", "HEAD")

        self.assertEqual(branch_name(self.repository.path), "")

    def test_branch_commits_returns_subjects_since_base(self) -> None:
        self._commit("feat: initial", {"a.txt": "1\n"})
        self.repository.run("branch", "-m", "main")
        self.repository.run("checkout", "--quiet", "-b", "feature/456")
        self._commit("wip: scaffold", {"a.txt": "2\n"})
        self._commit("wip: wire", {"a.txt": "3\n"})

        result = branch_commits("main", self.repository.path)

        self.assertEqual(result, "wip: wire\nwip: scaffold")

    def test_branch_commits_missing_base_returns_empty(self) -> None:
        self._commit("feat: initial", {"a.txt": "1\n"})

        result = branch_commits("main", self.repository.path)

        self.assertEqual(result, "")

    def test_branch_commits_unborn_head_returns_empty(self) -> None:
        self.repository.write("a.txt", "1\n")

        self.assertEqual(branch_commits("main", self.repository.path), "")


if __name__ == "__main__":
    unittest.main()
