from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx


# The proxy routes "<alias>/<upstream-model-id>" to the configured provider.
MODEL = "openrouter/z-ai/glm-5.3-flash"
CHAT_COMPLETIONS_PATH = "/openai/v1/chat/completions"
REQUEST_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
INSTRUCTION = (
    "Create a concise commit message that accurately summarizes these changes. "
    "Return only the commit message."
)
RECENT_LIMIT = 20
BRANCH_BASE = "main"


@dataclass(frozen=True)
class CommitContext:
    """Style and thread signals assembled from Git history to guide generation."""

    recent_commits: str
    branch_name: str
    branch_commits: str


class GenerationError(Exception):
    """Raised when the proxy cannot produce a usable commit message."""


def build_prompt(changes: str, context: CommitContext | None = None) -> str:
    sections: list[str] = []
    if context is not None:
        sections.extend(_context_sections(context))
    sections.append(f"<changes>\n{changes}</changes>")
    sections.append(INSTRUCTION)
    return "\n\n".join(sections)


def _context_sections(context: CommitContext) -> list[str]:
    sections: list[str] = []
    if context.recent_commits:
        sections.append(
            "Recent commit subjects (newest first, mirror this style):\n"
            f"{context.recent_commits}"
        )
    if context.branch_name:
        sections.append(f"Current branch: {context.branch_name}")
    if context.branch_commits:
        sections.append(
            "Commit subjects already on this branch since it left the base:\n"
            f"{context.branch_commits}"
        )
    return sections


def _endpoint(origin: str) -> str:
    return origin.rstrip("/") + CHAT_COMPLETIONS_PATH


def _payload(prompt: str) -> dict[str, Any]:
    return {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }


def generate_message(
    changes: str,
    origin: str,
    *,
    context: CommitContext | None = None,
    transport: httpx.BaseTransport | None = None,
) -> str:
    prompt = build_prompt(changes, context)
    endpoint = _endpoint(origin)

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT, transport=transport) as client:
            response = client.post(endpoint, json=_payload(prompt))
    except (httpx.InvalidURL, httpx.HTTPError) as error:
        raise GenerationError(_transport_failure(endpoint, error)) from error

    _raise_for_error_status(response)
    return _message_text(response)


def _transport_failure(endpoint: str, error: Exception) -> str:
    if isinstance(error, httpx.TimeoutException):
        problem = "timed out"
    else:
        problem = "could not be reached"
    return (
        f"The Generic OpenAI Proxy request to {endpoint} {problem}. Check "
        "GENERIC_OPENAI_PROXY_HOST and that the proxy is running."
    )


def _raise_for_error_status(response: httpx.Response) -> None:
    status = response.status_code
    if 200 <= status < 300:
        return
    if status == 400:
        detail = (
            "the request was rejected; the model must be provider-qualified, "
            f"for example {MODEL}"
        )
    elif status == 404:
        detail = "the proxy does not recognize this provider alias or path"
    elif status in (401, 403):
        detail = "the proxy or the upstream provider rejected its credentials"
    elif status == 429:
        detail = "the upstream provider rate limit or quota was reached"
    elif status in (502, 503, 504):
        detail = "the proxy could not reach the upstream provider or timed out"
    else:
        detail = "the proxy reported an unexpected failure"
    raise GenerationError(
        f"Commit message generation failed (HTTP {status}): {detail}."
    )


def _message_text(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError as error:
        raise GenerationError(
            "The Generic OpenAI Proxy returned a response that is not JSON."
        ) from error
    if not isinstance(payload, Mapping):
        raise GenerationError(
            "The Generic OpenAI Proxy returned an unrecognized response."
        )

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise GenerationError(
            "The Generic OpenAI Proxy returned no completion choices."
        )

    choice = choices[0]
    message = choice.get("message") if isinstance(choice, Mapping) else None
    content = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(content, str) or not content.strip():
        raise GenerationError(
            "The Generic OpenAI Proxy returned an empty commit message."
        )
    return content.strip()
