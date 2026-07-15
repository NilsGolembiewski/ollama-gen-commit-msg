# gen-commit-msg Implementation Plan

**Goal:** Build an installable Python command named `gen-commit-msg` that selects the correct Git changes, sends their literal diff text to Ollama's `glm-5.2:cloud` model through the endpoint in `OLLAMA_HOST`, and prints the generated commit message.

**Approach:** Create a small packaged application with three cohesive boundaries: Git change collection, prompt/model generation, and CLI orchestration. Use real temporary Git repositories to test selection and literal diff behavior, and fake the Ollama client in automated tests so the suite is deterministic and does not need credentials or consume cloud capacity. Manage metadata, dependencies, locking, execution, tests, builds, and tool installation with uv.

**Dependencies:** Python `>=3.11`; uv 0.11.28; `uv_build>=0.11.28,<0.12`; official `ollama>=0.6.2,<0.7`; `httpx>=0.28.1,<1` for the transport exception boundary (all resolved exactly in `uv.lock`); Git executable available on `PATH`. Research details are in `agent_docs/research/2026-07-15-ollama-uv-cli.md`.

**Acceptance criteria:**
- `uv tool install .` installs an executable named `gen-commit-msg`, and `gen-commit-msg --help` works without Git or Ollama access.
- Normal execution requires a nonblank `OLLAMA_HOST`; the value is passed explicitly to `ollama.Client(host=...)`.
- Generation always uses the fixed local-routing cloud model name `glm-5.2:cloud`. An `OLLAMA_HOST` whose parsed hostname is `ollama.com` is rejected with guidance to point at a local Ollama server; direct cloud API-key handling is not added.
- The model receives one prompt in exactly this shape, with the selected Git output inserted without parsing, summarizing, truncating, or stripping it:

  ```text
  <changes>
  {literal Git diff output}</changes>

  Create a concise commit message that accurately summarizes these changes. Return only the commit message.
  ```

  Git's normal trailing newline places `</changes>` on the line after the diff. No extra blank line is inserted inside the tags.
- If the index contains any staged change, only `git diff --cached` output is sent; unstaged edits and untracked files are excluded.
- If the index has no staged change, tracked unstaged output from `git diff` is followed by Git-generated patches for every nonignored untracked file. Untracked patches come from `git diff --no-index -- /dev/null <file>`; the CLI never mutates the index to expose them.
- Collection covers the whole repository even when the command starts in a subdirectory, handles spaces in filenames safely, preserves deterministic Git ordering, and honors `.gitignore` for untracked files.
- Git stdout is captured as bytes and decoded once with strict UTF-8 so locale settings cannot transform it. Non-UTF-8 diff output fails clearly rather than being silently replaced or sent in altered form.
- If there are no selected changes, the command does not call Ollama and reports a concise error on stderr with a nonzero exit status.
- Git context/command failures, Ollama connection/API failures, and empty model responses are reported on stderr without a traceback; generated message text is the only success output on stdout.
- The command generates text only. It does not stage files, write a commit, modify repository state, add model-selection flags, or enforce Conventional Commits.
- The complete standard-library test suite passes without a live Ollama service, `uv lock --check` succeeds, and both source and wheel distributions build successfully.

---

## Task 1: Package an End-to-End Staged-Change Slice

**Slice:** Deliver an installable `gen-commit-msg` command that can collect staged changes, build the required prompt, call a fakeable Ollama boundary using `glm-5.2:cloud`, and print a generated message. This first slice intentionally reports “no changes” when no staged diff exists; the fallback behavior is added in Task 2.

**Files:**
- Create: `.gitignore`
- Create: `.python-version`
- Create: `pyproject.toml`
- Create: `uv.lock`
- Create: `README.md`
- Create: `src/gen_commit_msg/__init__.py`
- Create: `src/gen_commit_msg/cli.py`
- Create: `src/gen_commit_msg/git_changes.py`
- Create: `src/gen_commit_msg/generator.py`
- Create: `tests/__init__.py`
- Create: `tests/test_cli.py`
- Create: `tests/test_git_changes.py`
- Create: `tests/test_generator.py`

**Context:** The repository currently has no application files or commits. The distribution and console command must be `gen-commit-msg`; the import package is `gen_commit_msg`. Configure `[project.scripts]` as `gen-commit-msg = "gen_commit_msg.cli:main"`, use the `uv_build` backend, target Python 3.11+, declare `ollama>=0.6.2,<0.7` and `httpx>=0.28.1,<1`, and let `uv.lock` record the exact resolution. Keep Git subprocess logic in `git_changes.py`, exact prompt and Ollama logic in `generator.py`, and environment/stdio/exit orchestration in `cli.py`. Do not add Click, Typer, pytest, or direct-cloud authentication.

**Steps:**
1. Add the minimal packaged-application metadata and empty package/test structure needed to execute tests with uv. Include `.venv/`, `dist/`, Python caches, and build metadata in `.gitignore`; pin the development interpreter in `.python-version`; add the console script and build system to `pyproject.toml`; then run `uv lock` to create `uv.lock`.
2. In `tests/test_git_changes.py`, create a temporary real Git repository helper that sets repository-local `user.name` and `user.email`, allowing committed baselines without relying on global Git configuration. Add a test that stages one change while also leaving a separate tracked edit and an untracked file. Assert that the collector returns exactly the stdout from a separately invoked `git diff --cached --no-color --no-ext-diff`, includes the staged path, and excludes the other paths. Also assert that calling from a nested directory still collects repository-wide staged output.
3. In `tests/test_generator.py`, write tests that pass representative diff text to the prompt builder and assert exact equality with:

   ```text
   <changes>
   {diff text}</changes>

   Create a concise commit message that accurately summarizes these changes. Return only the commit message.
   ```

   Verify the diff is not stripped or transformed. With a fake client factory, assert that the factory receives the exact host, `generate` receives `model="glm-5.2:cloud"`, the exact prompt, and `stream=False`, and the returned response text is trimmed only at the outer edges before display.
4. In `tests/test_cli.py`, write orchestration tests that inject or patch the change collector and message generator. Assert that a valid local-server `OLLAMA_HOST` flows to generation, the selected diff flows unchanged, the generated message is printed once to stdout, and success returns exit status 0. Add tests that `--help` exits successfully without reading Git or environment state; that a missing or whitespace-only `OLLAMA_HOST` writes a clear stderr message; and that direct hosts such as `https://ollama.com` and `https://ollama.com/api` are rejected with local-routing guidance. Every invalid-host case must return nonzero and call neither Git nor Ollama.
5. Run the focused tests and verify they fail because the functional implementation does not exist yet:

   ```bash
   uv run python -m unittest tests.test_git_changes tests.test_generator tests.test_cli -v
   ```

6. Implement `git_changes.py` with a small typed exception hierarchy and subprocess helper. Capture stdout/stderr as bytes, decode stdout once with strict UTF-8, and turn decode failures into a clear Git-output error instead of using locale-dependent text mode or lossy replacement. Resolve the repository root with `git rev-parse --show-toplevel`, run all later commands from that root using argument arrays rather than a shell, and return staged stdout unchanged when `git diff --cached --no-color --no-ext-diff` is nonempty. For this slice, raise a dedicated no-changes exception when staged output is empty.
7. Implement `generator.py` with constants for `glm-5.2:cloud` and the exact instruction. Build the tagged prompt without parsing or truncating the diff. Instantiate `ollama.Client(host=host)`, call synchronous `generate(..., stream=False)`, read `response.response`, and return only outer-whitespace-trimmed generated text.
8. Implement `cli.py` with standard-library `argparse`, a `main(argv: Sequence[str] | None = None) -> int` entry point, explicit validation of `OLLAMA_HOST` (including rejection of the parsed direct-cloud hostname `ollama.com`), orchestration of the two boundaries, stdout reserved for the successful message, and stderr/nonzero handling for the errors introduced in this slice.
9. Run `uv lock --check` and the focused tests again. Refactor only after the staged-selection, exact-prompt, model-call, environment, and output assertions pass.

**Verification:**

```bash
uv lock --check
uv run python -m unittest tests.test_git_changes tests.test_generator tests.test_cli -v
uv run gen-commit-msg --help
```

---

## Task 2: Add the Unstaged-and-Untracked Fallback

**Slice:** When there is no staged diff, collect all tracked unstaged changes plus nonignored untracked files as literal Git-generated patch output, without modifying the index or changing the Ollama/prompt behavior delivered by Task 1.

**Files:**
- Modify: `src/gen_commit_msg/git_changes.py`
- Modify: `tests/test_git_changes.py`

**Context:** `collect_changes` already resolves the repository root and gives any nonempty staged diff absolute precedence. Extend only the empty-staged branch. Plain `git diff` does not include untracked files, so list them with `git ls-files --others --exclude-standard -z` and ask Git to render each as a new-file patch with `git diff --no-index --no-color --no-ext-diff -- /dev/null <relative-path>`. A no-index status of 1 normally means “differences,” but operational races can also fail at that status; accept status 1 only when Git produced patch stdout and no stderr, otherwise raise a typed command error using captured stderr. Preserve each command's stdout and concatenate the tracked diff followed by untracked patches in Git's deterministic listing order. Never use `git add -N`, never invoke a shell, and never include ignored files.

**Steps:**
1. Reuse the temporary-repository helper with repository-local Git identity from Task 1 to create committed tracked baselines for fallback tests.
2. Add a failing test for a repository with no staged changes, one modified tracked file, and multiple untracked files including a nested path and a filename containing spaces. Compute expected output by independently invoking the exact unstaged and no-index Git commands, then assert byte-for-text equality with the collector's concatenated result and deterministic order.
3. Add failing tests proving that an ignored untracked file is excluded, an untracked-only repository is supported, an empty untracked file is represented if Git emits a patch, and the collector leaves `git status --porcelain=v1 -z` unchanged before and after collection. Add a mocked race/error case in which a listed path disappears and no-index returns status 1 with stderr but no patch; require a typed Git command error instead of silently omitting that file.
4. Add failing tests for a clean repository and a non-Git directory. The former must raise the dedicated no-changes error; the latter must raise a Git-context error carrying a concise user-facing message.
5. Run the Git-focused tests and verify the new fallback cases fail:

   ```bash
   uv run python -m unittest tests.test_git_changes -v
   ```

6. Implement the empty-staged branch in `git_changes.py`: collect unstaged tracked output, obtain the NUL-delimited untracked path list, render every untracked path through Git's no-index diff, and concatenate nonempty chunks without stripping them or inserting summaries. Keep repository-root execution and argv-based subprocess safety.
7. Distinguish expected no-index status 1 with patch stdout and empty stderr from status 1 operational failures. Ensure disappearing paths, malformed command execution, missing Git, or nonrepository context becomes the module's typed Git error rather than leaking `subprocess` details.
8. Run the Git-focused tests, then the complete suite to prove staged precedence and model prompt behavior still pass. Refactor repeated Git invocation code only while preserving literal stdout and status semantics.

**Verification:**

```bash
uv run python -m unittest tests.test_git_changes -v
uv run python -m unittest discover -s tests -v
```

---

## Task 3: Complete Failure Handling and User Documentation

**Slice:** Make the command predictable in real terminal use: all expected Git/Ollama failures are concise and traceback-free, success output remains script-friendly, and the README explains installation, local Ollama cloud routing, selection precedence, privacy implications, and limitations.

**Files:**
- Modify: `src/gen_commit_msg/cli.py`
- Modify: `src/gen_commit_msg/generator.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_generator.py`
- Modify: `README.md`

**Context:** The local Ollama route is intentional. Users set `OLLAMA_HOST` to their Ollama server (commonly `http://localhost:11434`), authenticate that local installation with `ollama signin`, and make `glm-5.2:cloud` available with `ollama pull glm-5.2:cloud`. Do not add application-level `OLLAMA_API_KEY` validation or direct-cloud mode; the official client owns any generic environment handling it performs internally. The generated message is the only successful stdout content. Expected runtime failures should return status 1 and write one actionable stderr message; argparse retains its standard usage status for invalid CLI arguments.

**Steps:**
1. In `tests/test_generator.py`, add failing cases for an empty/whitespace-only `response.response`, `ollama.ResponseError`, `ConnectionError`, representative `httpx.HTTPError` subclasses such as `ReadTimeout`, `httpx.InvalidURL`, and malformed-response `ValueError`. Require the model boundary to translate these into a typed generation error whose public message does not include a traceback, prompt/diff content, host secrets, or environment values.
2. In `tests/test_cli.py`, add failing cases for a clean repository, invocation outside a repository, Git executable/command failure, Ollama API failure, Ollama connection failure, and empty model output. Assert status 1, empty stdout, a concise actionable stderr line, and that Ollama is never called when Git collection fails or finds no changes.
3. Add a CLI success test whose generated text has surrounding whitespace. Assert output is exactly the trimmed message plus the terminal newline, with no progress labels, host/model details, or diff text on stdout or stderr.
4. Run the focused tests and confirm the newly specified failures are red:

   ```bash
   uv run python -m unittest tests.test_generator tests.test_cli -v
   ```

5. Introduce or complete typed `GenerationError` translation in `generator.py`. Around the narrow client-construction/generation boundary, catch `ollama.ResponseError`, the built-in `ConnectionError` produced by Ollama, `httpx.HTTPError`, `httpx.InvalidURL`, and malformed-response `ValueError`; reject an empty response and preserve only safe actionable context for CLI display.
6. Complete `cli.py` error mapping. Catch only expected typed domain/configuration errors, write them to stderr, return status 1, and avoid broad exception handling that would hide programming defects. Keep Git and model calls ordered so no remote request occurs without selected changes.
7. Expand `README.md` with: purpose; prerequisites (Python, uv for development/install, Git, Ollama); `ollama signin` and `ollama pull glm-5.2:cloud`; local-server `OLLAMA_HOST` setup; why direct `https://ollama.com` is rejected; local install with `uv tool install .`; development usage with `uv run gen-commit-msg`; staged-versus-fallback examples; the exact prompt contract; errors; uninstall instructions; and a prominent warning that the complete selected diff is sent to a cloud model and may contain secrets.
8. State explicitly in the README that the command only prints a suggestion, does not run `git commit`, does not stage files, does not truncate large diffs, excludes ignored files, and does not implement direct cloud API-key mode.
9. Run the complete suite and refactor error-message duplication only after all output and no-call assertions pass.

**Verification:**

```bash
uv run python -m unittest tests.test_generator tests.test_cli -v
uv run python -m unittest discover -s tests -v
uv run gen-commit-msg --help
```

---

## Task 4: Verify the Locked Build and Installed Tool

**Slice:** Prove that a fresh locked environment, package build, and isolated uv tool installation all work, while keeping live cloud use as an explicit manual smoke test rather than part of automated verification.

**Files:**
- Modify if needed: `pyproject.toml`
- Modify if dependency resolution changed: `uv.lock`
- Modify if verification reveals an install instruction issue: `README.md`

**Context:** This task adds no feature behavior. It validates the final artifact from a clean uv-managed environment. Automated checks must not require `OLLAMA_HOST`, an Ollama account, or network calls to a model. A live smoke test is optional because it consumes cloud service and requires the user's signed-in Ollama installation.

**Steps:**
1. Run `uv lock --check` to prove `pyproject.toml` and `uv.lock` agree, then run `uv sync --locked` to reproduce the environment from the lockfile.
2. Run the entire standard-library suite. If anything fails, fix the owning source/test task and rerun this step rather than weakening assertions.
3. Remove stale build artifacts if present, run `uv build`, and verify uv creates both an sdist and wheel under `dist/` without missing-package or entry-point warnings.
4. Install the current project into isolated temporary uv tool/bin directories, run the installed executable with `--help`, and uninstall it. This verifies the wheel metadata and `[project.scripts]` entry point rather than relying only on the editable project environment.
5. Review `git diff --check` and `git status --short` to ensure only the intended source, tests, metadata, lockfile, README, and agent documentation are present.
6. Optionally, with a signed-in local Ollama server and a disposable dirty Git repository, run `OLLAMA_HOST=http://localhost:11434 gen-commit-msg` once and manually confirm that only a concise message is printed. Do not make this live call a CI or acceptance-test dependency.

**Verification:**

```bash
uv lock --check
uv sync --locked
uv run python -m unittest discover -s tests -v
uv build
UV_TOOL_DIR=/tmp/gen-commit-msg-tools UV_TOOL_BIN_DIR=/tmp/gen-commit-msg-bin uv tool install --force .
/tmp/gen-commit-msg-bin/gen-commit-msg --help
UV_TOOL_DIR=/tmp/gen-commit-msg-tools UV_TOOL_BIN_DIR=/tmp/gen-commit-msg-bin uv tool uninstall gen-commit-msg
git diff --check
git status --short
```
