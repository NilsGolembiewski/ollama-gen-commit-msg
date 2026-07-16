from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from ollama import Client, ResponseError


MODEL = "glm-5.2:cloud"
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
    """Raised when Ollama cannot generate a usable commit message."""


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


def generate_message(
    changes: str,
    host: str,
    *,
    context: CommitContext | None = None,
    client_factory: Callable[..., Any] = Client,
) -> str:
    try:
        client = client_factory(host=host)
        response = client.generate(
            model=MODEL,
            prompt=build_prompt(changes, context),
            stream=False,
        )
    except ResponseError as error:
        raise GenerationError(
            "Ollama rejected the generation request. Ensure glm-5.2:cloud is "
            "available and the local Ollama server is signed in."
        ) from error
    except (ConnectionError, httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
        raise GenerationError(
            "Ollama could not generate a commit message. Check OLLAMA_HOST and "
            "the local Ollama server."
        ) from error

    response_text = response.response
    if not isinstance(response_text, str) or not response_text.strip():
        raise GenerationError("Ollama returned an empty commit message.")
    return response_text.strip()
