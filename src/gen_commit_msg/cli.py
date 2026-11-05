from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from urllib.parse import urlparse

from gen_commit_msg.generator import (
    BRANCH_BASE,
    RECENT_LIMIT,
    CommitContext,
    GenerationError,
    generate_message,
)
from gen_commit_msg.git_changes import (
    GitChangesError,
    branch_commits,
    branch_name,
    collect_changes,
    recent_commits,
)


DEFAULT_PROXY_ORIGIN = "http://localhost:4119"


class ConfigurationError(Exception):
    """Raised when CLI configuration is missing or unsupported."""


def _parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="gen-commit-msg",
        description="Generate a concise commit message from the current Git changes.",
    )


def _configured_origin() -> str:
    """Return the proxy origin, falling back to the documented local default."""
    configured = os.environ.get("GENERIC_OPENAI_PROXY_HOST", "").strip()
    if not configured:
        return DEFAULT_PROXY_ORIGIN
    return _validated_origin(configured)


def _validated_origin(value: str) -> str:
    if any(character.isspace() for character in value):
        raise ConfigurationError(
            "GENERIC_OPENAI_PROXY_HOST must not contain whitespace."
        )

    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ConfigurationError(
            "GENERIC_OPENAI_PROXY_HOST is not a valid URL."
        ) from error

    if parsed.scheme not in ("http", "https") or not hostname:
        raise ConfigurationError(
            "GENERIC_OPENAI_PROXY_HOST must be an absolute http(s) URL, for "
            f"example {DEFAULT_PROXY_ORIGIN}."
        )
    if parsed.username or parsed.password:
        raise ConfigurationError(
            "GENERIC_OPENAI_PROXY_HOST must not embed credentials."
        )
    if (
        parsed.path not in ("", "/")
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError(
            "GENERIC_OPENAI_PROXY_HOST must be an origin only: no path, query, "
            "or fragment."
        )

    host_part = f"[{hostname}]" if ":" in hostname else hostname
    netloc = host_part if port is None else f"{host_part}:{port}"
    return f"{parsed.scheme}://{netloc}"


def main(argv: Sequence[str] | None = None) -> int:
    _parser().parse_args(argv)

    try:
        origin = _configured_origin()
        changes = collect_changes()
        context = CommitContext(
            recent_commits=recent_commits(limit=RECENT_LIMIT),
            branch_name=branch_name(),
            branch_commits=branch_commits(BRANCH_BASE),
        )
        message = generate_message(changes, origin, context=context)
    except (ConfigurationError, GitChangesError, GenerationError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(message.strip())
    return 0
