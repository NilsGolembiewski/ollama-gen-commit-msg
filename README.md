# gen-commit-msg

Generate a concise commit message from the current Git changes with Ollama Cloud's
`glm-5.2:cloud` model.

The command prints a commit-message suggestion. It does not stage files, create a
commit, or modify the repository.

## Prerequisites

- Python 3.11 or newer
- Git available on `PATH`
- A local Ollama server with an Ollama account
- [uv](https://docs.astral.sh/uv/) for installation or development

Sign the local Ollama installation in and make the cloud model available:

```bash
ollama signin
ollama pull glm-5.2:cloud
```

## Install

From this repository, install the command as an isolated uv tool:

```bash
uv tool install .
```

Set `OLLAMA_HOST` to the local Ollama server that will route cloud-model requests:

```bash
export OLLAMA_HOST=http://localhost:11434
```

The direct cloud API host `https://ollama.com` is intentionally rejected. This
tool uses the `glm-5.2:cloud` model through a signed-in local Ollama server and
does not implement direct `OLLAMA_API_KEY` mode.

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

If the repository has no selected changes, the command exits before contacting
Ollama.

> [!WARNING]
> Selected change text is sent to an Ollama cloud model. Lockfile bodies and
> oversized patches are omitted or truncated, but remaining source diffs may
> still contain credentials, tokens, personal data, or other sensitive content.
> Review staged, unstaged, and untracked files before running the command.

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

Automated tests use temporary Git repositories and fake Ollama clients; they do
not require an account or consume cloud capacity.

## Uninstall

```bash
uv tool uninstall gen-commit-msg
```
