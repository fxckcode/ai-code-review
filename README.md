# AI Code Review — GitHub Action

Automated PR code reviews posted as inline comments, powered by pluggable AI agent CLI adapters.

> **v1 scope**: CI-only (`pull_request` trigger). Local review CLI is out of scope for v1.

---

## Quick start

```yaml
# .github/workflows/pr-review.yml
name: AI Code Review

on:
  pull_request:
    types: [opened, synchronize]

permissions:
  contents: read
  pull-requests: write

concurrency:
  group: ai-review-${{ github.ref }}
  cancel-in-progress: true

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4        # required — action reads AGENTS.md from the workspace
        with:
          fetch-depth: 0

      - uses: org/ai-code-review@v1
        with:
          agent: claude
        env:
          AI_REVIEW_GITHUB_TOKEN: ${{ secrets.AI_REVIEW_GITHUB_TOKEN }}
          AI_REVIEW_CLAUDE_TOKEN:  ${{ secrets.AI_REVIEW_CLAUDE_TOKEN }}
```

---

## Consumer contract

### Preconditions

| Requirement | Detail |
|-------------|--------|
| **Checkout step** | `actions/checkout` must run before this action. The orchestrator reads `AGENTS.md` from `$GITHUB_WORKSPACE`. Missing checkout causes a clear precondition error. |
| **`AGENTS.md` at repo root** | The file must exist and be non-empty. The action fails fast if it is absent or empty. See [Authoring AGENTS.md](#authoring-agentsmd). |
| **Runner** | `ubuntu-latest` (GitHub-hosted). The action installs Python 3.12 via `setup-python@v5`. |
| **Trigger** | `pull_request` event only (v1). `push`, `workflow_dispatch`, and mention triggers are not supported. |

### Permissions

```yaml
permissions:
  contents: read        # read AGENTS.md and repo files
  pull-requests: write  # post the review
```

### Concurrency

Always add a concurrency group to avoid parallel review runs for the same branch:

```yaml
concurrency:
  group: ai-review-${{ github.ref }}
  cancel-in-progress: true
```

### Inputs

| Input | Required | Description |
|-------|----------|-------------|
| `agent` | yes | Agent CLI to use. Implemented: `claude`, `opencode`. Stubs (`cursor`, `antigravity`) fail with `not implemented yet`. |

### Secrets and environment variables

| Variable | Secret? | Description |
|----------|---------|-------------|
| `AI_REVIEW_GITHUB_TOKEN` | yes | GitHub token with `pull-requests: write`. Use a PAT or the default `GITHUB_TOKEN`. |
| `AI_REVIEW_CLAUDE_TOKEN` | for `claude` | Anthropic token. Remapped to `ANTHROPIC_API_KEY`. |
| `AI_REVIEW_OPENCODE_TOKEN` | for `opencode` | Provider API key for OpenCode. Remapped to `OPENAI_API_KEY`. Falls back to repo secret `OPENAI_API_KEY` if unset. |
| `AI_REVIEW_CURSOR_TOKEN` | yes | Token for the Cursor agent (reserved; not implemented yet). |
| `AI_REVIEW_ANTIGRAVITY_TOKEN` | yes | Token for the Antigravity agent (reserved; not implemented yet). |
| `AI_REVIEW_MODEL` | optional | Override the model used by the agent CLI (agent-specific format). |
| `AI_REVIEW_DEBUG` | optional | Set to `1` to emit full agent output in logs instead of last 8 KB. |

Only set the token secret for the agent you are using — unused tokens are ignored.

---

## Supported agents

| Agent | Status | Token secret |
|-------|--------|--------------|
| `claude` | **Implemented** — installs `@anthropic-ai/claude-code` (npm, latest) | `AI_REVIEW_CLAUDE_TOKEN` |
| `opencode` | **Implemented** — installs `opencode-ai` (npm, latest); `opencode run --pure` | `AI_REVIEW_OPENCODE_TOKEN` (or `OPENAI_API_KEY`) |
| `cursor` | Not yet implemented | `AI_REVIEW_CURSOR_TOKEN` |
| `antigravity` | Not yet implemented | `AI_REVIEW_ANTIGRAVITY_TOKEN` |

Selecting an unimplemented agent causes the job to fail red with `not implemented yet`.

---

## Authoring AGENTS.md

`AGENTS.md` at repo root is **mandatory and must be non-empty**. The orchestrator injects it at the
start of every review prompt. It should contain the conventions, invariants, and review priorities
specific to your codebase.

Minimal example:

```markdown
# AGENTS.md

## Hard rules
- No `eval()` or dynamic code execution.
- All database queries must use parameterised statements.

## Warnings
- Public methods without docstrings should be flagged.

## Out of scope
- Whitespace-only changes.
```

See the [`AGENTS.md`](./AGENTS.md) in this repo for a full worked example.

---

## How it works

```
caller workflow (checkout, secrets, concurrency)
  └─→ action.yml  (agent input → AI_AGENT env var)
        └─→ review.py orchestrator
             1  gh pr diff → temp file (truncated at 80 k chars with a prompt note)
             2  read AGENTS.md  (mandatory; fails if missing or empty)
             3  build English prompt = AGENTS.md + base schema
             4  registry.select(AI_AGENT) → adapter
                  ensure_installed  → installs CLI (user-local, latest)
                  remap AI_REVIEW_<AGENT>_TOKEN → native CLI env var
                  run(prompt, diff_path, config) → list of comments
             5  one parse retry on malformed output
             6  filter comments not anchored to the diff (drop + log)
             7  post GitHub PR review  (REQUEST_CHANGES | COMMENT)
```

### Review events

| Condition | Event |
|-----------|-------|
| ≥ 1 anchored 🔴 error | `REQUEST_CHANGES` |
| Warnings or suggestions only | `COMMENT` |
| All comments unanchored to diff | `COMMENT` with summary listing them |
| Empty diff | `COMMENT` — nothing to review |
| No issues found | `COMMENT` — clean summary |

### Failure UX

| Failure | Job result |
|---------|------------|
| Agent timeout (> 5 min) | Posts ⚠️ failed-review comment; **job stays green** |
| Agent crash | Posts ⚠️ failed-review comment; **job stays green** |
| Parse failure after retry | **Job exits red** |
| Unknown agent / missing secret / empty AGENTS.md | **Job exits red** |

---

## Review output format

Each inline comment is posted on the exact diff line it references:

```
🔴 error   — src/app.ts:42 — This mutation bypasses the cache layer.
⚠️ warning  — src/utils.ts:15 — Function mutates its argument; prefer returning a new value.
💡 suggestion — src/helpers.ts:8 — Extract to a named constant for readability.
```

The review summary header is always prefixed `AI Code Review ({agent})`.

---

## Stack

- Python 3.12 (installed at run time via `setup-python@v5`)
- `gh` CLI (pre-installed on GitHub-hosted runners)
- Agent-specific CLI installed by the adapter at run time (e.g. `@anthropic-ai/claude-code`)
- No vendor SDK dependency in the orchestrator
