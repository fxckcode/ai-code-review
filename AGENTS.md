# AGENTS.md — AI Code Review Rules for this Repository

This file is read by the AI Code Review action before every review run.
It encodes the invariants, boundaries, and conventions that matter for PRs in this repo.

---

## Project snapshot

This repo is a **composite GitHub Action** (`action.yml`) that runs pluggable AI agent CLI
adapters to post structured code-review comments on pull requests.  The runtime is:

- **Orchestrator** — `.github/scripts/review.py` (vendor-neutral; no SDK imports allowed)
- **Adapter layer** — `.github/scripts/adapters/` (one file per agent; thin wrappers)
- **Registry** — `.github/scripts/adapters/registry.py` (enum + select)
- **Tests** — `.github/scripts/tests/` (pytest; fake adapter; integration tests skip when CLI absent)

---

## Hard rules — flag every violation as 🔴 error

### 1. No `shell=True` in subprocess calls

Any `subprocess.run(...)` or `subprocess.Popen(...)` call with `shell=True` is a command-injection
vulnerability.  All subprocess calls MUST pass a list of arguments, never a string.

```python
# 🔴 WRONG
subprocess.run(f"claude --print {prompt}", shell=True)

# ✅ RIGHT
subprocess.run(["claude", "--print"], input=prompt, ...)
```

### 2. Secrets must never appear in logs or argv

`AI_REVIEW_*` token values must not be passed as command-line arguments or printed to stdout/stderr.
They must be remapped inside `_build_env()` and passed exclusively through the child process
environment.  Log only the last 8 KB of adapter output unless `AI_REVIEW_DEBUG=1`.

### 3. No vendor SDK imports in the orchestrator

`review.py` must not import `anthropic`, `openai`, or any agent-specific SDK.  All vendor logic
lives in `adapters/<agent>.py`.  Reject any PR that adds an SDK import to the orchestrator.

### 4. Adapter contract must not be silently broken

Every adapter MUST implement the base class contract from `adapters/base.py`:

```python
transport: str  # "acp" | "argv"
def ensure_installed(self) -> None: ...
def run(self, *, prompt: str, diff_path: str, config: dict) -> list[dict]: ...
```

PRs that add or modify an adapter must confirm these three members are present and correctly typed.
Return type for `run` is always `list[dict]` with keys `path`, `line`, `severity`, `body`.

### 5. Unknown agent must fail, not silently no-op

`registry.select()` must raise `ValueError` for any agent string not in the enum.  If a PR changes
registry behaviour such that an unknown agent falls through without raising, flag it as an error.

### 6. AGENTS.md must stay non-empty

The orchestrator refuses to start if this file is missing or empty.  PRs must not delete or empty
this file.  PRs that rename or move it must update the path in `review.py` (`agents_md_path`).

---

## Warnings — flag as ⚠️ warning

### 7. New agent without tests

Every new adapter file must have corresponding test coverage in `.github/scripts/tests/`.  At
minimum: token-absent fail, `NotImplementedError` for stub adapters, and subprocess safety.

### 8. Timeout handling must stay green

`subprocess.TimeoutExpired` from the adapter call site must result in `post_failure_comment()` +
`sys.exit(0)`, **not** `sys.exit(1)`.  Parse failures after retry must stay red (`sys.exit(1)`).
Review any change to the exception handling block in `main()` carefully.

### 9. Registry enum must be updated when a new agent is added

Adding an adapter file without updating the enum in `registry.py` means the new agent is
unreachable at runtime.  Check that the adapter name is in `VALID_AGENTS` and that `select()`
returns it.

### 10. `ensure_installed` must fail loud on install error

Install failures must raise an exception that propagates to `main()`, causing `sys.exit(1)`.
Silent failures (swallowed exceptions, `pass`) leave the runner in an undefined state.

### 11. Diff temp-file must be cleaned up

The orchestrator writes the PR diff to a `tempfile.NamedTemporaryFile`.  PRs that change this
path must ensure the file is deleted after use (e.g., via `try/finally` or context manager).

---

## Suggestions — flag as 💡 suggestion

### 12. Prefer `Path` over raw `os.path` string operations

The codebase uses `pathlib.Path` throughout.  New code should follow suit.

### 13. Config dict keys should be consistent

The `config` dict passed to adapters currently carries `model`, `debug`, `timeout`.  New adapters
that need additional keys should add them in `main()` at the same site, not inside the adapter.

### 14. English-only in orchestrator output and summaries

Review summaries, error messages, and log lines in `review.py` and adapter files must be in
English.  The AGENTS.md file itself may use the project language, but the orchestrator's runtime
output must be English.

---

## Out of scope for this reviewer

- Style-only nits (whitespace, blank lines) unless they violate an explicit rule above.
- GitHub Actions YAML indentation preferences.
- Changes to files under `.cursor/`, `.atl/`, or `*.lock` — skip those.
- Test-only PRs that do not touch orchestrator or adapter logic.
