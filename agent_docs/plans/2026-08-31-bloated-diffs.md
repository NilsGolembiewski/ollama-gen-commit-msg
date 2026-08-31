# Plan: Stop Bloated Diffs From Inflating Prompt Tokens

**Date:** 2026-08-31
**Status:** implemented
**Goal:** Keep `gen-commit-msg` from sending huge Git patches (especially lockfile updates) to `glm-5.2:cloud`, while still giving the model enough signal to write an accurate commit message.

**Problem:** `collect_changes()` returns literal Git stdout and `build_prompt()` wraps that string unchanged. A lockfile bump (`uv.lock`, `package-lock.json`, `yarn.lock`, and similar) can be tens or hundreds of thousands of tokens. The original 2026-07-15 contract explicitly forbade summarizing or truncating. That contract is the bug for model cost and context limits. Source files next to the lockfile (for example `pyproject.toml`) already carry the human-meaningful dependency signal.

**Approach:** Deepen `git_changes.collect_changes` without changing its external interface (`cwd -> str` of selected change text). After the existing staged-vs-unstaged/untracked selection, compact the unified diff in process:

1. Always replace known lockfile patches with a one-line omission that keeps path, change kind, and insert/delete counts.
2. Truncate any remaining single-file patch that exceeds a per-file line/byte budget, keeping the Git header and a prefix of the body plus an omission trailer.
3. If the concatenated result still exceeds a total byte budget, demote the largest remaining full patches to one-line omissions until the budget is met. Every changed path still appears.

Small non-lockfile diffs must stay **byte-identical** to today's Git stdout so the existing collector tests remain valid. `cli.py` and `generator.py` stay orchestration and prompt/model modules; they do not learn lockfile names or budgets. No new CLI flags, no new dependencies, no stderr on success.

**Dependencies:** None beyond the current package (Python 3.11+, Git on `PATH`, existing test helper in `tests/test_git_changes.py`). Compaction is in-process text processing of Git's own unified diff.

**Acceptance criteria:**

- Selection rules are unchanged: nonempty staged diff still wins; otherwise unstaged tracked plus nonignored untracked; collection is repository-wide; the index is never mutated; ignored files stay excluded; no changes still raises `NoChangesError` before any model call.
- A lockfile-only change is still a change: the command must not raise `NoChangesError` and must not send lockfile body text to the model.
- Known lockfile patches never appear as unified-diff bodies in the string returned by `collect_changes`. Each is replaced by exactly one omission line that includes the path, kind (`added` / `deleted` / `modified` / `renamed`), and `+N -M` counts derived from that file's patch.
- Non-lockfile patches under the per-file budget are concatenated in Git's original file order and match `git diff` / `git diff --cached` / no-index untracked patches exactly (existing tests must keep passing).
- A non-lockfile patch over the per-file budget keeps its `diff --git` header and a leading prefix, then an omission trailer with path and remaining line/byte counts. The omitted body is not present.
- After per-file compaction, if the full string is still over the total budget, largest remaining full (non-lockfile, non-truncated) patches are replaced with one-line omissions until the result is `<= MAX_TOTAL_BYTES`, or only summaries/truncation trailers remain. No changed path is dropped.
- `build_prompt` continues to insert whatever `collect_changes` returns without further parsing, stripping, or truncation.
- Success stdout is still only the generated message; compaction is silent (empty stderr on success).
- README no longer claims large diffs are not truncated. It documents lockfile omission, size budgets, and that every changed path is still listed.
- `uv run python -m unittest discover -s tests -v` and `uv lock --check` pass.

**Proposed constants** (live in `src/gen_commit_msg/git_changes.py`, not passed through the public interface):

| Name | Value | Role |
| --- | --- | --- |
| `MAX_FILE_LINES` | `300` | Truncate a non-lockfile patch after this many lines |
| `MAX_FILE_BYTES` | `16384` | Truncate a non-lockfile patch after this many UTF-8 bytes (do not split a line) |
| `MAX_TOTAL_BYTES` | `32768` | After per-file processing, demote remaining full patches until the whole string fits |
| `LOCKFILE_NAMES` | frozen exact basenames | Always omit, even when small |
| `LOCKFILE_SUFFIXES` | `(".lock", "-lock.json", "-lock.yaml", "-lock.yml")` | Basename suffix match, case-sensitive |

Exact basenames to include at least: `uv.lock`, `package-lock.json`, `npm-shrinkwrap.json`, `yarn.lock`, `pnpm-lock.yaml`, `bun.lock`, `bun.lockb`, `Cargo.lock`, `composer.lock`, `Gemfile.lock`, `poetry.lock`, `Pipfile.lock`, `pdm.lock`, `flake.lock`, `deno.lock`, `go.sum`, `go.work.sum`, `Podfile.lock`, `Package.resolved`, `mix.lock`, `pubspec.lock`, `gradle.lockfile`. Suffixes already cover `uv.lock` and `Cargo.lock`; keep those names in the set anyway so the list is obvious to readers.

Match against the destination path (`b/...` from the `diff --git` header; for deletions, the `a/...` path). Nested paths such as `frontend/yarn.lock` match on basename. Do not treat snapshots, `dist/`, or `*.min.js` as lockfiles; the size budgets cover those.

**Omission line formats** (exact; trailing newline required):

```text
omitted lockfile: {path} ({kind}, +{insertions} -{deletions})
omitted large diff: {path} ({kind}, +{insertions} -{deletions})
omitted remainder: {path} ({lines} lines, {bytes} bytes)
```

`kind` is `added` if the patch has `new file mode`, `deleted` if `deleted file mode`, `renamed` if it has `rename from`, otherwise `modified`. Insertions/deletions count patch body lines that start with `+` or `-` but are not `+++` / `---` headers. Binary lockfiles with no `+`/`-` body still get an omission line (`+0 -0`) rather than Git's `Binary files differ` payload.

**Truncation rule:** Count lines and UTF-8 bytes of the whole per-file patch (header included). Keep a prefix of complete lines that is `<= MAX_FILE_LINES` and `<= MAX_FILE_BYTES`, and always keep at least the first line (`diff --git ...`). Append `omitted remainder: ...` using the dropped line count and dropped byte count.

---

## Design

### Seam

`collect_changes(cwd=None) -> str` remains the only public collector interface. Callers (`cli.py`) already treat that string as “the change text to put in `<changes>`”. Compaction belongs behind that interface:

- Deleting a separate compact function would force CLI/generator/tests to relearn lockfile policy. Putting it inside `collect_changes` concentrates the policy in one module (locality) and leaves CLI/generator interfaces unchanged (leverage).
- Do **not** add `budget=` / `compact=` parameters to the public function. Tests construct real files that exceed the production constants. Do not expose an internal seam just for tests.
- Do **not** compact in `build_prompt` / `generate_message`. The generator's job is tagged prompt + Ollama. Lockfile names and Git patch splitting do not belong there.
- Do **not** introduce a second collector (`collect_prompt_changes`). Two adapters are not needed; compaction does not vary by environment.

Internal structure (private, not part of the interface):

1. Existing selection: staged `git diff --cached` or unstaged `git diff` plus no-index untracked patches. Literal concatenate as today.
2. `_compact_diff(text) -> str`: if empty, return empty; otherwise split, map each file patch, then enforce the total budget.
3. `collect_changes` returns `_compact_diff(literal)`.

### Why split Git's unified diff instead of extra Git invocations

Alternative considered: `git diff --numstat` plus per-path `git diff -- <path>`, or pathspecs `:(exclude)uv.lock`. Rejected because:

- Per-path diffs multiply subprocesses and can disagree with a single `git diff` on renames.
- Pathspec excludes need the lockfile path list up front and still leave non-lockfile bloat unsolved.
- Post-processing the already-collected unified diff preserves today's Git invocation pattern, keeps small diffs byte-identical, and handles staged, unstaged, and untracked the same way.

Split on lines that match `^diff --git ` (start of line, no `+`/`-`/` ` prefix). Content that happens to contain the text `diff --git` is always prefixed in unified diffs, so it will not split. Preserve any preamble before the first header (should not occur for these Git commands; if it does, keep it verbatim). Quoted Git paths (`diff --git "a/foo bar" "b/foo bar"`) need a small C-quote unescape when extracting the path; do not use a shell.

### Rejected alternatives

- **Drop lockfiles entirely:** lockfile-only commits would look like no changes or lose the only signal.
- **Silent byte cap on the whole blob:** the model would not know which files vanished; a `uv.lock` sitting in the middle of the blob could eat the whole budget and hide `src/` changes after it.
- **Parse lockfiles for version bumps:** ecosystem-specific and brittle. Manifest files (`pyproject.toml`, `package.json`, `Cargo.toml`) stay in the full diff when they changed.
- **tiktoken / token counting:** extra dependency; character/line budgets are enough for this CLI.
- **CLI exclude globs or `--full-diff`:** extra interface for a problem that should be automatic. Out of scope.
- **Always send `git diff --stat`:** too lossy for source changes.

### Contract change

This plan **supersedes** the 2026-07-15 rule that model-facing text is unparsed, unsummarized, untruncated Git stdout. Selection, UTF-8 strictness, no index mutation, and “generator does not rewrite the string it is given” remain. README and any comments that say large diffs are not truncated must be updated in the same change.

---

## Task 1: Omit Lockfile Patch Bodies

**Slice:** After literal selection, replace lockfile file-patches with one-line omissions. Non-lockfile output stays identical to current Git stdout. This alone removes the usual token blow-up.

**Files:**

- Modify: `src/gen_commit_msg/git_changes.py`
- Modify: `tests/test_git_changes.py`

**Context:** Reuse `GitRepository` from `tests/test_git_changes.py`. Existing tests use small names like `staged.txt` and must keep passing without edits if compaction is a no-op for them. Add new tests rather than rewriting the exact-equality cases.

**Steps:**

1. Add failing tests on `collect_changes`:

   - Staged `src/app.py` plus staged `uv.lock` (dozens of inserted lines). Assert the `app.py` patch equals the independently invoked `git diff --cached -- src/app.py` output, `uv.lock` body lines are absent, and the result contains `omitted lockfile: uv.lock (modified, +N -M)` with counts matching the staged lockfile patch.
   - Staged new `package-lock.json` (`added`) and deleted `yarn.lock` (`deleted`).
   - Nested untracked `frontend/pnpm-lock.yaml` on the unstaged fallback path; assert omission uses path `frontend/pnpm-lock.yaml`, kind `added`, and `git status --porcelain=v1 -z` is unchanged.
   - Lockfile-only repository (modified `Cargo.lock`, nothing else). Assert no `NoChangesError`, no lockfile body, one omission line.
   - Basename suffix: `vendor/app.lock` is omitted; `notes.lock.md` is not.
   - `go.sum` (name-set, not a `.lock` suffix) is omitted.
   - Path with spaces that is not a lockfile remains a literal patch (existing space-in-name coverage plus a staged `"my file.txt"` next to `uv.lock`).

2. Run the Git tests and confirm only the new cases fail:

   ```bash
   uv run python -m unittest tests.test_git_changes -v
   ```

3. Implement `_compact_diff` and call it at the end of `collect_changes` (both the staged return and the fallback concatenate). Implement header splitting, path extraction (including quoted paths), lockfile matching, kind detection, `+`/`-` counts, and the lockfile omission line. Leave per-file/total budgets unimplemented in this task so non-lockfile huge files still pass through (Task 2).

4. Re-run Git tests, then the full suite, to prove staged precedence, untracked fallback, UTF-8 errors, and generator/CLI tests still pass.

**Verification:**

```bash
uv run python -m unittest tests.test_git_changes -v
uv run python -m unittest discover -s tests -v
```

---

## Task 2: Cap Oversized Non-Lockfile Patches

**Slice:** Apply `MAX_FILE_LINES` / `MAX_FILE_BYTES` truncation and `MAX_TOTAL_BYTES` demotion so generated files, huge refactors, and accidental binary-as-text dumps cannot inflate the prompt even when they are not lockfiles.

**Files:**

- Modify: `src/gen_commit_msg/git_changes.py`
- Modify: `tests/test_git_changes.py`

**Context:** Build oversize fixtures in the temp repo rather than injecting budgets. A 400-line tracked file exceeds `MAX_FILE_LINES` (300). Several ~12 KiB files exceed `MAX_TOTAL_BYTES` (32768) after per-file processing. Truncation must not cut mid-line. Total-budget demotion uses `omitted large diff:` and prefers replacing the largest remaining full patch first. Already-omitted lockfiles and already-truncated files are not expanded again; truncated files may be replaced wholesale by `omitted large diff:` if that is what it takes to fit, but a path must not disappear.

**Steps:**

1. Add failing tests:

   - One tracked file with 400 short lines, staged. Result starts with that file's `diff --git` header, does not contain the last added lines, contains `omitted remainder:`, and reports remaining line and byte counts that add up with the kept prefix to the original patch.
   - One file just under both per-file limits remains byte-identical to `git diff --cached`.
   - Three staged non-lockfile files whose full patches together exceed 32768 bytes, each individually under 300 lines and 16384 bytes. After collection, `len(result) <= 32768`, every path is mentioned (full patch or `omitted large diff:`), and at least one path was demoted.
   - Mix: small `src/app.py` + huge generated-looking `data.json` + `uv.lock`. App patch is complete; lockfile is `omitted lockfile:`; `data.json` is truncated or demoted; no lockfile or omitted body leaks.
   - Untracked large file on the fallback path is truncated the same way as a tracked file.

2. Run Git tests red, then implement truncation and total-budget demotion inside `_compact_diff` after the lockfile pass (lockfile omission still happens first and unconditionally).

3. Confirm lockfile tests from Task 1 still pass and existing exact-equality tests still pass.

**Verification:**

```bash
uv run python -m unittest tests.test_git_changes -v
uv run python -m unittest discover -s tests -v
```

---

## Task 3: Document the New Prompt-Size Contract

**Slice:** Align user-facing docs with the new behavior. No CLI or generator code changes are expected if Tasks 1–2 kept those interfaces stable.

**Files:**

- Modify: `README.md`
- Modify only if a test or comment still claims literal untruncated diffs: `tests/test_generator.py` (generator should still claim it does not rewrite its input; that remains true)

**Context:** README currently says “Large diffs are not truncated.” Replace that with the real policy. Keep the privacy warning: compaction reduces lockfile volume but the remaining patch is still sent to a cloud model.

**Steps:**

1. In the “Change selection” section, state:

   - Selection precedence is unchanged.
   - Lockfiles (named list or “dependency lockfiles such as `uv.lock` and `package-lock.json`”) are sent as one-line omissions with insert/delete counts, not as patch bodies.
   - Other files over about 300 lines or 16 KiB are truncated; the combined change text is kept under about 32 KiB.
   - Every changed path is still listed. The command does not stage, commit, or add exclude flags.

2. Keep the prompt diagram: `<changes>` still wraps the collector output, which may now include omission lines mixed with literal patches.

3. Run the full suite and `uv lock --check`. No lockfile or dependency change should be required.

**Verification:**

```bash
uv lock --check
uv run python -m unittest discover -s tests -v
uv run gen-commit-msg --help
```

---

## Implementation notes

- Keep Git subprocess capture as bytes and strict UTF-8 decode **before** compaction. Compaction runs on `str`. Non-UTF-8 diffs still raise `GitOutputError` and never reach the model.
- Do not log, print, or return token estimates.
- Do not change `INSTRUCTION`, model name, or `CommitContext` assembly.
- Prefer a few small private helpers (`_split_file_patches`, `_patch_path`, `_is_lockfile`, `_count_edits`, `_summarize_patch`) over a parallel class hierarchy.
- Order of file patches in the compacted output must match Git's original order.

## Out of scope

- CLI flags to disable compaction or pass extra pathspecs.
- Configurable budgets or user lockfile lists.
- Extracting package version ranges from lockfile contents.
- Truncating recent-commit / branch-commit context (those are already short subjects).
- Streaming or chunked model calls.
- Changing Ollama host/model routing.

## Suggested implementation order

Task 1 (lockfiles) delivers most of the token win and can ship without Task 2. Task 2 is the safety net for non-lockfile bloat. Task 3 must land with whichever of 1–2 is implemented so README does not lie.
