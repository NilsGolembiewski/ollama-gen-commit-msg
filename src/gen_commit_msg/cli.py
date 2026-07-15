from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from urllib.parse import urlparse

from gen_commit_msg.generator import GenerationError, generate_message
from gen_commit_msg.git_changes import GitChangesError, collect_changes


class ConfigurationError(Exception):
    """Raised when CLI configuration is missing or unsupported."""


def _parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="gen-commit-msg",
        description="Generate a concise commit message from the current Git changes.",
    )


def _configured_host() -> str:
    host = os.environ.get("OLLAMA_HOST", "").strip()
    if not host:
        raise ConfigurationError("OLLAMA_HOST must point to a local Ollama server.")

    parse_target = host if "://" in host else f"//{host}"
    try:
        hostname = urlparse(parse_target).hostname
    except ValueError as error:
        raise ConfigurationError("OLLAMA_HOST is not a valid endpoint.") from error
    if hostname and hostname.rstrip(".").casefold() == "ollama.com":
        raise ConfigurationError(
            "OLLAMA_HOST must point to a local Ollama server, not ollama.com."
        )
    return host


def main(argv: Sequence[str] | None = None) -> int:
    _parser().parse_args(argv)

    try:
        host = _configured_host()
        changes = collect_changes()
        message = generate_message(changes, host)
    except (ConfigurationError, GitChangesError, GenerationError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(message.strip())
    return 0
