from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
from ollama import Client, ResponseError


MODEL = "glm-5.2:cloud"
INSTRUCTION = (
    "Create a concise commit message that accurately summarizes these changes. "
    "Return only the commit message."
)


class GenerationError(Exception):
    """Raised when Ollama cannot generate a usable commit message."""


def build_prompt(changes: str) -> str:
    return f"<changes>\n{changes}</changes>\n\n{INSTRUCTION}"


def generate_message(
    changes: str,
    host: str,
    *,
    client_factory: Callable[..., Any] = Client,
) -> str:
    try:
        client = client_factory(host=host)
        response = client.generate(
            model=MODEL,
            prompt=build_prompt(changes),
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
