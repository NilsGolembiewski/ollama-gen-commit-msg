# gen-commit-msg

Generate a concise commit message from the current Git changes through the
Generic OpenAI Proxy using the `openrouter/z-ai/glm-5.3-flash` model.

The command prints a commit-message suggestion. It does not stage files, create a
commit, or modify the repository.

## Prerequisites

- Python 3.11 or newer
- Git available on `PATH`
- A running Generic OpenAI Proxy with an upstream provider connection configured
- [uv](https://docs.astral.sh/uv/) for installation or development

This tool holds no provider credential. The proxy keeps named upstream
connections and their API keys server-side and exposes a flattened
OpenAI-compatible API on `<origin>/openai/v1`.

## Install

From this repository, install the command as an isolated uv tool:

```bash
uv tool install .
```

Point the tool at the proxy origin:

```bash
export GENERIC_OPENAI_PROXY_HOST=http://localhost:4119
```

`GENERIC_OPENAI_PROXY_HOST` must be an absolute `http` or `https` origin with no
path, query, fragment, or embedded credentials. A single trailing slash is
accepted. When the variable is unset or blank, the command falls back to
`http://localhost:4119`; a non-blank value that is not a valid origin is rejected
instead of being silently replaced.

The model is a fixed constant, `openrouter/z-ai/glm-5.3-flash`. The proxy routes
the `<provider-alias>/<upstream-model-id>` form, so only the first `/` separates
the alias from the upstream model. No proxy authorization header is sent,
because the proxy is an unauthenticated local intermediary.

## Usage

Run the command from anywhere inside a Git repository:

```bash
gen-commit-msg
```

The generated message is written to stdout, so it can be copied or composed with
other commands. Runtime errors are written to stderr and return a nonzero exit
status.

### Change selection

The index has absolute precedence:

1. If any changes are staged, only `git diff --cached` is selected.
2. If nothing is staged, tracked unstaged changes and all nonignored untracked
   files are selected.

Untracked files are rendered as Git patches without modifying the index. Ignored
files are excluded. Every changed path is still listed.

Before the model sees the selected changes, bulky patches are compacted:

- Dependency lockfiles such as `uv.lock` and `package-lock.json` are sent as
  one-line omissions with insert/delete counts, not as patch bodies.
- Other files over about 300 lines or 16 KiB are truncated to a header and
  prefix, with a remainder count.
- Combined change text is kept under about 32 KiB by omitting the largest
  remaining full patches if needed.

The command does not add exclude flags. Compaction is automatic and silent.

The prompt has this exact structure:

```text
<changes>
...selected Git diff output, which may include omission lines
</changes>

Create a concise commit message that accurately summarizes these changes. Return only the commit message.
```

When recent commit subjects, the current branch, or branch commits are
available, they are inserted before the `<changes>` block as style and thread
context.

If the repository has no selected changes, the command exits before contacting
the proxy.

> [!WARNING]
> Selected change text is sent through the proxy to the configured upstream
> provider. Lockfile bodies and oversized patches are omitted or truncated, but
> remaining source diffs may still contain credentials, tokens, personal data,
> or other sensitive content. Review staged, unstaged, and untracked files
> before running the command.
>
> The default proxy listener is loopback-only and unauthenticated, so any process
> on the machine can use its configured accounts. Keep the proxy bound to
> loopback and keep real API keys in the proxy's server-side configuration.

## Development

Run the command in the uv-managed project environment:

```bash
uv run gen-commit-msg --help
```

Run the test suite and build both distribution formats:

```bash
uv run python -m unittest discover -s tests -v
uv build
```

Automated tests use temporary Git repositories and mocked proxy HTTP transports;
they do not require a running proxy or consume upstream capacity.

## Uninstall

```bash
uv tool uninstall gen-commit-msg
```
