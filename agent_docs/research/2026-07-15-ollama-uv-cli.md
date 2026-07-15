# Research: Ollama Cloud Python Client and uv CLI Packaging

**Date:** 2026-07-15
**Libraries researched:** Ollama Python 0.6.2, HTTPX 0.28.1, uv 0.11.28 / uv_build 0.11.28

## Current Versions

- `ollama` 0.6.2 is the current official Python client release. It supports Python 3.8+ and depends on `httpx>=0.27` and `pydantic>=2.9`.
- `httpx` 0.28.1 is the current stable HTTPX release. `httpx.HTTPError` covers request and HTTP-status failures; `httpx.InvalidURL` is separate from that hierarchy.
- `uv` 0.11.28 is the current stable project/package manager release.
- The packaged-application template in uv 0.11.28 uses `uv_build>=0.11.28,<0.12`.

## Key Findings

- Ollama's model library identifies the requested cloud model as `glm-5.2:cloud`, with a 976K-token context window.
- There are two distinct cloud connection modes. A local Ollama server can route `*:cloud` models after `ollama signin`; direct requests to `https://ollama.com` instead require an `OLLAMA_API_KEY` bearer token and use the direct API's model names.
- The chosen requirement is local routing: `OLLAMA_HOST` points at the Ollama server, the application creates `ollama.Client(host=<OLLAMA_HOST>)`, and it requests `glm-5.2:cloud`. Authentication remains the local Ollama installation's responsibility.
- The Python client exposes synchronous `Client.generate(model=..., prompt=...)`; generated text is available as `response.response`. API failures use `ollama.ResponseError`, while connection failures are surfaced as `ConnectionError` by the current client.
- Ollama 0.6.2 translates HTTP status and connect errors itself, but other HTTPX transport, timeout, protocol, URL, and decoding failures may propagate. A CLI that promises traceback-free expected failures should declare HTTPX directly and translate its documented exception boundary.
- Ollama 0.6.2 automatically adds `OLLAMA_API_KEY` as a bearer header if that variable is present, even when a host is passed explicitly. The CLI should reject Ollama's direct cloud host because the selected behavior is local-server cloud routing rather than direct cloud API access.
- uv recommends a packaged application for an installable CLI. This uses a `src/` package layout, a `[project.scripts]` entry point, and a declared build system. `uv run <command>` runs the installed project command, `uv build` creates distributions, and `uv tool install .` installs the CLI as a tool.

## Recommended Usage

- Package the application as distribution `gen-commit-msg`, import package `gen_commit_msg`, and console command `gen-commit-msg`.
- Declare Python `>=3.11`, constrain runtime clients to `ollama>=0.6.2,<0.7` and `httpx>=0.28.1,<1`, resolve exact artifacts in `uv.lock`, and use the current uv build backend range.
- Read and validate `OLLAMA_HOST` in the application, then pass it explicitly to `ollama.Client`. Do not add direct-cloud bearer-token behavior because the selected mode is a signed-in local Ollama server.
- Call `Client.generate` with `model="glm-5.2:cloud"`, `stream=False`, and the complete tagged Git diff prompt.
- Use only Python's standard library for argument parsing, Git subprocesses, and tests; this avoids additional runtime and development dependencies.

## Migration Notes

- This is a new project, so no migration is required.
- uv application templates without `--package` are not installable and do not create script entry points. The implementation must use packaged-application metadata and a build system.
- Older cloud examples may list retired GLM models. The current model library and cloud retirement table point to `glm-5.2` / `glm-5.2:cloud`.

## Sources

- https://pypi.org/project/ollama/
- https://github.com/ollama/ollama-python
- https://pypi.org/project/httpx/
- https://www.python-httpx.org/exceptions/
- https://ollama.com/library/glm-5.2
- https://ollama.com/library/glm-5.2/tags
- https://docs.ollama.com/cloud
- https://docs.ollama.com/api/authentication
- https://docs.astral.sh/uv/concepts/projects/init/
- https://docs.astral.sh/uv/concepts/projects/config/
- https://docs.astral.sh/uv/guides/package/
- https://docs.astral.sh/uv/guides/tools/
- https://github.com/astral-sh/uv/releases/tag/0.11.28
