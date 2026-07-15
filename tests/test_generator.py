from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from ollama import ResponseError

from gen_commit_msg.generator import GenerationError, build_prompt, generate_message


class PromptTests(unittest.TestCase):
    def test_build_prompt_preserves_literal_diff(self) -> None:
        changes = "\ndiff --git a/file b/file\n+new line\n"

        prompt = build_prompt(changes)

        self.assertEqual(
            prompt,
            "<changes>\n"
            "\ndiff --git a/file b/file\n"
            "+new line\n"
            "</changes>\n\n"
            "Create a concise commit message that accurately summarizes these "
            "changes. Return only the commit message.",
        )


class GenerationTests(unittest.TestCase):
    def test_generate_uses_configured_host_cloud_model_and_exact_prompt(self) -> None:
        changes = "diff --git a/file b/file\n+new line\n"
        client = Mock()
        client.generate.return_value = SimpleNamespace(response="  Add new line  \n")
        client_factory = Mock(return_value=client)

        message = generate_message(
            changes,
            "http://localhost:11434",
            client_factory=client_factory,
        )

        client_factory.assert_called_once_with(host="http://localhost:11434")
        client.generate.assert_called_once_with(
            model="glm-5.2:cloud",
            prompt=build_prompt(changes),
            stream=False,
        )
        self.assertEqual(message, "Add new line")

    def test_empty_response_is_rejected(self) -> None:
        client = Mock()
        client.generate.return_value = SimpleNamespace(response="  \n")

        with self.assertRaisesRegex(GenerationError, "empty"):
            generate_message(
                "diff\n",
                "http://localhost:11434",
                client_factory=Mock(return_value=client),
            )

    def test_missing_response_value_is_rejected(self) -> None:
        client = Mock()
        client.generate.return_value = SimpleNamespace(response=None)

        with self.assertRaisesRegex(GenerationError, "empty"):
            generate_message(
                "diff\n",
                "http://localhost:11434",
                client_factory=Mock(return_value=client),
            )

    def test_ollama_response_error_is_translated_without_leaking_details(self) -> None:
        secret = "sensitive diff fragment"
        client = Mock()
        client.generate.side_effect = ResponseError(secret, 500)

        with self.assertRaises(GenerationError) as raised:
            generate_message(
                "diff\n",
                "http://localhost:11434",
                client_factory=Mock(return_value=client),
            )

        self.assertIn("Ollama", str(raised.exception))
        self.assertNotIn(secret, str(raised.exception))

    def test_transport_and_malformed_response_failures_are_translated(self) -> None:
        failures = (
            ConnectionError("sensitive connection detail"),
            httpx.ReadTimeout("sensitive timeout detail"),
            httpx.InvalidURL("sensitive URL detail"),
            ValueError("sensitive response detail"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                client = Mock()
                client.generate.side_effect = failure

                with self.assertRaises(GenerationError) as raised:
                    generate_message(
                        "diff\n",
                        "http://localhost:11434",
                        client_factory=Mock(return_value=client),
                    )

                self.assertIn("Ollama", str(raised.exception))
                self.assertNotIn("sensitive", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
