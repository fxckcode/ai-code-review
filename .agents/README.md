# Agent skills (project pack)

Pinned, repo-local skills for agents working on **ai-code-review**.
Sources and hashes live in [`skills-lock.json`](../skills-lock.json) at the repo root.

## Installed skills

| Skill | Use when |
|-------|----------|
| `grill-me` | Sharpen a design/plan with an adversarial interview before coding |
| `code-review` | Two-axis review (standards + spec) against a fixed point (`main`, SHA, tag) |
| `code-review-excellence` | Deeper review craft — severity, evidence, actionable findings |
| `python-testing-patterns` | Author or improve pytest coverage for orchestrator/adapters |
| `github-actions-templates` | Edit `action.yml` / workflows safely |

## How agents should load them

1. Prefer **project** skills under `.agents/skills/<name>/SKILL.md` over global copies.
2. Pass the **exact** `SKILL.md` path into subagents (do not summarize the skill away).
3. Refresh the local index with the skill-registry workflow when skills are added/removed (writes `.atl/skill-registry.md`, gitignored).

## Review gate (before push / PR)

For changes on this repo, run in order:

1. `python -m pytest .github/scripts/tests/`
2. Bugbot review (`branch changes` vs `main`)
3. Security review (`branch changes` vs `main`)
4. Optionally `code-review` with fixed point `main` and spec = Engram `sdd/agent-cli-adapters/*` / `AGENTS.md`

Do not push stacked PR tips until Bugbot + Security are clean of merge-blocking findings.
