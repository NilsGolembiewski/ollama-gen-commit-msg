from __future__ import annotations

import json
import unittest
from typing import Any

import httpx

from gen_commit_msg.generator import (
    INSTRUCTION,
    MODEL,
    CommitContext,
    GenerationError,
    build_prompt,
    generate_message,
)

ORIGIN = "http://localhost:4119"
ENDPOINT = "http://localhost:4119/openai/v1/chat/completions"


def completion(content: Any = "feat: add thing") -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
    }


class RecordingTransport(httpx.BaseTransport):
    """Mock transport that records each request and returns a canned response."""

    def __init__(
        self,
        *,
        payload: Any = None,
        status: int = 200,
        raw_body: bytes | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.payload = completion() if payload is None else payload
        self.status = status
        self.raw_body = raw_body
        self.failure = failure
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        if self.raw_body is not None:
            return httpx.Response(
                self.status, content=self.raw_body, request=request
            )
        return httpx.Response(self.status, json=self.payload, request=request)


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

    def test_build_prompt_includes_recent_commits_style_section(self) -> None:
        changes = "diff --git a/file b/file\n+new line\n"
        context = CommitContext(
            recent_commits="feat: prior\nfix: older",
            branch_name="",
            branch_commits="",
        )

        prompt = build_prompt(changes, context)

        self.assertIn(
            "Recent commit subjects (newest first, mirror this style):\n"
            "feat: prior\nfix: older",
            prompt,
        )
        self.assertIn("<changes>", prompt)
        self.assertIn(INSTRUCTION, prompt)

    def test_build_prompt_includes_branch_and_branch_commits(self) -> None:
        changes = "diff\n"
        context = CommitContext(
            recent_commits="feat: prior",
            branch_name="feature/123-ss0",
            branch_commits="wip: scaffold\nwip: wire proxy",
        )

        prompt = build_prompt(changes, context)

        self.assertIn("Current branch: feature/123-ss0", prompt)
        self.assertIn(
            "Commit subjects already on this branch since it left the base:\n"
            "wip: scaffold\nwip: wire proxy",
            prompt,
        )

    def test_build_prompt_omits_empty_context_sections(self) -> None:
        changes = "diff\n"
        context = CommitContext(recent_commits="", branch_name="", branch_commits="")

        prompt = build_prompt(changes, context)

        self.assertEqual(
            prompt,
            "<changes>\ndiff\n</changes>\n\n" + INSTRUCTION,
        )


class GenerationTests(unittest.TestCase):
    def test_generate_posts_qualified_model_to_flattened_endpoint(self) -> None:
        changes = "diff --git a/file b/file\n+new line\n"
        transport = RecordingTransport(payload=completion("  Add new line  \n"))

        message = generate_message(changes, ORIGIN, transport=transport)

        self.assertEqual(message, "Add new line")
        self.assertEqual(len(transport.requests), 1)
        request = transport.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), ENDPOINT)
        self.assertNotIn("authorization", request.headers)

        body = json.loads(request.content)
        self.assertEqual(body["model"], MODEL)
        self.assertEqual(body["model"], "openrouter/z-ai/glm-5.3-flash")
        self.assertIs(body["stream"], False)
        self.assertEqual(
            body["messages"],
            [{"role": "user", "content": build_prompt(changes)}],
        )

    def test_generate_trims_trailing_slash_from_origin(self) -> None:
        transport = RecordingTransport()

        generate_message("diff\n", ORIGIN + "/", transport=transport)

        self.assertEqual(str(transport.requests[0].url), ENDPOINT)

    def test_generate_passes_context_into_prompt(self) -> None:
        changes = "diff\n"
        context = CommitContext(
            recent_commits="feat: prior",
            branch_name="feature/123",
            branch_commits="wip: in progress",
        )
        transport = RecordingTransport()

        generate_message(changes, ORIGIN, context=context, transport=transport)

        body = json.loads(transport.requests[0].content)
        self.assertEqual(
            body["messages"],
            [{"role": "user", "content": build_prompt(changes, context)}],
        )

    def test_empty_response_is_rejected(self) -> None:
        for content in ("  \n", None, ""):
            with self.subTest(content=content):
                with self.assertRaisesRegex(GenerationError, "empty commit message"):
                    generate_message(
                        "diff\n",
                        ORIGIN,
                        transport=RecordingTransport(payload=completion(content)),
                    )

    def test_missing_choices_are_rejected(self) -> None:
        for payload in ({}, {"choices": []}, {"choices": [{}]}, ["unexpected"]):
            with self.subTest(payload=payload):
                with self.assertRaises(GenerationError):
                    generate_message(
                        "diff\n",
                        ORIGIN,
                        transport=RecordingTransport(payload=payload),
                    )

    def test_non_json_response_is_rejected(self) -> None:
        with self.assertRaisesRegex(GenerationError, "not JSON"):
            generate_message(
                "diff\n",
                ORIGIN,
                transport=RecordingTransport(raw_body=b"<html>error</html>"),
            )

    def test_error_statuses_are_classified_without_leaking_body(self) -> None:
        secret = "sensitive upstream detail"
        cases: tuple[tuple[int, str], ...] = (
            (400, "provider-qualified"),
            (404, "provider alias"),
            (401, "credentials"),
            (403, "credentials"),
            (429, "rate limit"),
            (302, "unexpected failure"),
            (500, "unexpected failure"),
            (502, "upstream provider"),
            (504, "upstream provider"),
        )
        for status, expected in cases:
            with self.subTest(status=status):
                transport = RecordingTransport(
                    status=status,
                    payload={"error": {"message": secret}},
                )

                with self.assertRaises(GenerationError) as raised:
                    generate_message("diff\n", ORIGIN, transport=transport)

                message = str(raised.exception)
                self.assertIn(str(status), message)
                self.assertIn(expected, message)
                self.assertNotIn(secret, message)

    def test_transport_failures_are_translated_without_leaking_details(self) -> None:
        failures = (
            httpx.ConnectError("sensitive connection detail"),
            httpx.ReadTimeout("sensitive timeout detail"),
            httpx.InvalidURL("sensitive URL detail"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with self.assertRaises(GenerationError) as raised:
                    generate_message(
                        "diff\n",
                        ORIGIN,
                        transport=RecordingTransport(failure=failure),
                    )

                message = str(raised.exception)
                self.assertIn("GENERIC_OPENAI_PROXY_HOST", message)
                self.assertNotIn("sensitive", message)

    def test_unreachable_proxy_error_names_the_endpoint(self) -> None:
        with self.assertRaises(GenerationError) as raised:
            generate_message(
                "diff\n",
                ORIGIN,
                transport=RecordingTransport(
                    failure=httpx.ConnectError("connection refused")
                ),
            )

        self.assertIn(ENDPOINT, str(raised.exception))
        self.assertIn("could not be reached", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
