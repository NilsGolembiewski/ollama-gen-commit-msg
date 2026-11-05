from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import httpx

from gen_commit_msg.cli import DEFAULT_PROXY_ORIGIN, main
from gen_commit_msg.generator import (
    CommitContext,
    GenerationError,
    generate_message as generate,
)
from gen_commit_msg.git_changes import (
    GitCommandError,
    GitRepositoryError,
    NoChangesError,
)


class CliTests(unittest.TestCase):
    @patch.dict(
        os.environ,
        {"GENERIC_OPENAI_PROXY_HOST": "http://proxy.internal:4119"},
        clear=True,
    )
    @patch("gen_commit_msg.cli.recent_commits", return_value="feat: prior change")
    @patch("gen_commit_msg.cli.branch_name", return_value="feature/123")
    @patch("gen_commit_msg.cli.branch_commits", return_value="wip: in progress")
    @patch("gen_commit_msg.cli.generate_message", return_value="Add staged change")
    @patch("gen_commit_msg.cli.collect_changes", return_value="literal diff\n")
    def test_success_prints_only_generated_message(
        self,
        collect_changes,
        generate_message,
        branch_commits,
        branch_name,
        recent_commits,
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
            "literal diff\n",
            "http://proxy.internal:4119",
            context=CommitContext(
                recent_commits="feat: prior change",
                branch_name="feature/123",
                branch_commits="wip: in progress",
            ),
        )
        recent_commits.assert_called_once_with(limit=20)
        branch_name.assert_called_once()
        branch_commits.assert_called_once_with("main")

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

    @patch("gen_commit_msg.cli.collect_changes", return_value="diff\n")
    @patch("gen_commit_msg.cli.generate_message", return_value="feat: change")
    def test_unset_or_blank_proxy_origin_uses_local_default(
        self, generate_message, collect_changes
    ) -> None:
        environments = (
            {},
            {"GENERIC_OPENAI_PROXY_HOST": ""},
            {"GENERIC_OPENAI_PROXY_HOST": "   "},
        )
        for environment in environments:
            with self.subTest(environment=environment):
                generate_message.reset_mock()
                with patch.dict(
                    os.environ, environment, clear=True
                ), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    status = main([])

                self.assertEqual(status, 0)
                self.assertEqual(
                    generate_message.call_args.args[1], "http://localhost:4119"
                )

        self.assertEqual(DEFAULT_PROXY_ORIGIN, "http://localhost:4119")

    @patch("gen_commit_msg.cli.collect_changes")
    @patch("gen_commit_msg.cli.generate_message")
    def test_invalid_proxy_origins_are_rejected(
        self, generate_message, collect_changes
    ) -> None:
        cases = {
            "localhost:4119": "absolute",
            "ftp://localhost:4119": "absolute",
            "http://localhost:4119/api": "origin only",
            "http://localhost:4119?x=1": "origin only",
            "http://localhost:4119#frag": "origin only",
            "http://user:secret@localhost:4119": "credentials",
            "http://[": "valid URL",
            "http://localhost:99999": "valid URL",
            "http://local host:4119": "whitespace",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                stderr = io.StringIO()
                with patch.dict(
                    os.environ, {"GENERIC_OPENAI_PROXY_HOST": value}, clear=True
                ), redirect_stderr(stderr):
                    status = main([])

                self.assertEqual(status, 1)
                self.assertIn("GENERIC_OPENAI_PROXY_HOST", stderr.getvalue())
                self.assertIn(expected, stderr.getvalue())
                self.assertNotIn("secret", stderr.getvalue())
        collect_changes.assert_not_called()
        generate_message.assert_not_called()

    @patch("gen_commit_msg.cli.collect_changes", return_value="diff\n")
    @patch("gen_commit_msg.cli.generate_message", return_value="feat: change")
    def test_trailing_slash_is_normalized(
        self, generate_message, collect_changes
    ) -> None:
        with patch.dict(
            os.environ,
            {"GENERIC_OPENAI_PROXY_HOST": "http://localhost:4119/"},
            clear=True,
        ), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            status = main([])

        self.assertEqual(status, 0)
        self.assertEqual(generate_message.call_args.args[1], "http://localhost:4119")

    @patch.dict(os.environ, {}, clear=True)
    @patch("gen_commit_msg.cli.generate_message")
    @patch("gen_commit_msg.cli.collect_changes")
    def test_git_failures_do_not_call_the_proxy(
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

    @patch.dict(os.environ, {}, clear=True)
    @patch(
        "gen_commit_msg.cli.generate_message",
        side_effect=GenerationError("The proxy could not generate a commit message."),
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
            stderr.getvalue(),
            "error: The proxy could not generate a commit message.\n",
        )
        self.assertNotIn("Traceback", stderr.getvalue())

    @patch.dict(os.environ, {}, clear=True)
    @patch("gen_commit_msg.cli.collect_changes", return_value="diff\n")
    def test_missing_model_response_is_reported_without_traceback(
        self, collect_changes
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": None}}
                    ]
                },
                request=request,
            )

        def generate_missing_response(changes: str, origin: str, context=None) -> str:
            return generate(
                changes,
                origin,
                context=context,
                transport=httpx.MockTransport(handler),
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

    @patch.dict(os.environ, {}, clear=True)
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
