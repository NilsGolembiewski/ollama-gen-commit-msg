from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

from gen_commit_msg.cli import main
from gen_commit_msg.generator import GenerationError, generate_message as generate
from gen_commit_msg.git_changes import (
    GitCommandError,
    GitRepositoryError,
    NoChangesError,
)


class CliTests(unittest.TestCase):
    @patch.dict(os.environ, {"OLLAMA_HOST": "http://localhost:11434"}, clear=True)
    @patch("gen_commit_msg.cli.generate_message", return_value="Add staged change")
    @patch("gen_commit_msg.cli.collect_changes", return_value="literal diff\n")
    def test_success_prints_only_generated_message(
        self, collect_changes, generate_message
    ) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main([])

        self.assertEqual(status, 0)
        self.assertEqual(stdout.getvalue(), "Add staged change\n")
        self.assertEqual(stderr.getvalue(), "")
        collect_changes.assert_called_once()
        generate_message.assert_called_once_with(
            "literal diff\n", "http://localhost:11434"
        )

    @patch("gen_commit_msg.cli.collect_changes")
    @patch("gen_commit_msg.cli.generate_message")
    def test_help_needs_no_environment_or_services(
        self, generate_message, collect_changes
    ) -> None:
        stdout = io.StringIO()

        with patch.dict(os.environ, {}, clear=True), redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as raised:
                main(["--help"])

        self.assertEqual(raised.exception.code, 0)
        self.assertIn("gen-commit-msg", stdout.getvalue())
        collect_changes.assert_not_called()
        generate_message.assert_not_called()

    @patch("gen_commit_msg.cli.collect_changes")
    @patch("gen_commit_msg.cli.generate_message")
    def test_missing_or_blank_host_is_rejected(
        self, generate_message, collect_changes
    ) -> None:
        for environment in ({}, {"OLLAMA_HOST": "   "}):
            with self.subTest(environment=environment):
                stderr = io.StringIO()
                with patch.dict(os.environ, environment, clear=True), redirect_stderr(
                    stderr
                ):
                    status = main([])

                self.assertEqual(status, 1)
                self.assertIn("OLLAMA_HOST", stderr.getvalue())
        collect_changes.assert_not_called()
        generate_message.assert_not_called()

    @patch("gen_commit_msg.cli.collect_changes")
    @patch("gen_commit_msg.cli.generate_message")
    def test_direct_cloud_host_is_rejected(
        self, generate_message, collect_changes
    ) -> None:
        for host in ("https://ollama.com", "https://ollama.com/api"):
            with self.subTest(host=host):
                stderr = io.StringIO()
                with patch.dict(
                    os.environ, {"OLLAMA_HOST": host}, clear=True
                ), redirect_stderr(stderr):
                    status = main([])

                self.assertEqual(status, 1)
                self.assertIn("local Ollama server", stderr.getvalue())
        collect_changes.assert_not_called()
        generate_message.assert_not_called()

    @patch("gen_commit_msg.cli.collect_changes")
    @patch("gen_commit_msg.cli.generate_message")
    def test_malformed_host_is_rejected_without_traceback(
        self, generate_message, collect_changes
    ) -> None:
        stderr = io.StringIO()
        with patch.dict(
            os.environ, {"OLLAMA_HOST": "http://["}, clear=True
        ), redirect_stderr(stderr):
            status = main([])

        self.assertEqual(status, 1)
        self.assertIn("OLLAMA_HOST", stderr.getvalue())
        collect_changes.assert_not_called()
        generate_message.assert_not_called()

    @patch.dict(os.environ, {"OLLAMA_HOST": "http://localhost:11434"}, clear=True)
    @patch("gen_commit_msg.cli.generate_message")
    @patch("gen_commit_msg.cli.collect_changes")
    def test_git_failures_do_not_call_ollama(
        self, collect_changes, generate_message
    ) -> None:
        failures = (
            NoChangesError("No Git changes found."),
            GitRepositoryError("Current directory is not inside a Git repository."),
            GitCommandError("Git executable was not found on PATH."),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                collect_changes.reset_mock()
                generate_message.reset_mock()
                collect_changes.side_effect = failure
                stdout = io.StringIO()
                stderr = io.StringIO()

                with redirect_stdout(stdout), redirect_stderr(stderr):
                    status = main([])

                self.assertEqual(status, 1)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue().count("\n"), 1)
                self.assertIn(str(failure), stderr.getvalue())
                generate_message.assert_not_called()

    @patch.dict(os.environ, {"OLLAMA_HOST": "http://localhost:11434"}, clear=True)
    @patch(
        "gen_commit_msg.cli.generate_message",
        side_effect=GenerationError("Ollama could not generate a commit message."),
    )
    @patch("gen_commit_msg.cli.collect_changes", return_value="diff\n")
    def test_generation_failure_is_reported_without_traceback(
        self, collect_changes, generate_message
    ) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main([])

        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(), "error: Ollama could not generate a commit message.\n"
        )
        self.assertNotIn("Traceback", stderr.getvalue())

    @patch.dict(os.environ, {"OLLAMA_HOST": "http://localhost:11434"}, clear=True)
    @patch("gen_commit_msg.cli.collect_changes", return_value="diff\n")
    def test_missing_model_response_is_reported_without_traceback(
        self, collect_changes
    ) -> None:
        client = Mock()
        client.generate.return_value = Mock(response=None)

        def generate_missing_response(changes: str, host: str) -> str:
            return generate(
                changes,
                host,
                client_factory=Mock(return_value=client),
            )

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch(
            "gen_commit_msg.cli.generate_message", side_effect=generate_missing_response
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            status = main([])

        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue().count("\n"), 1)
        self.assertIn("empty commit message", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    @patch.dict(os.environ, {"OLLAMA_HOST": "http://localhost:11434"}, clear=True)
    @patch("gen_commit_msg.cli.generate_message", return_value="  Add feature  \n")
    @patch("gen_commit_msg.cli.collect_changes", return_value="diff\n")
    def test_success_trims_only_outer_message_whitespace(
        self, collect_changes, generate_message
    ) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main([])

        self.assertEqual(status, 0)
        self.assertEqual(stdout.getvalue(), "Add feature\n")
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
