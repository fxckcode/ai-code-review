#!/usr/bin/env python3
"""
AI Code Review — Claude + GitHub CLI inline comments.

Fetches PR diff, sends to Claude, posts structured feedback as
inline PR review comments via the GitHub API (gh).
"""

import os
import json
import subprocess
import sys

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-20250514")
MAX_TOKENS = int(os.environ.get("CLAUDE_MAX_TOKENS", "4096"))

REPO = os.environ.get("REPO") or os.environ.get("GITHUB_REPOSITORY", "")
PR_NUMBER = os.environ.get("PR_NUMBER") or os.environ.get("GITHUB_PR_NUMBER", "")
# Get GitHub token from env, or from gh auth if available
GH_TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
if not GH_TOKEN:
    try:
        GH_TOKEN = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception:
        pass

SYSTEM_PROMPT = """Eres un code reviewer de alto nivel. Revisa el diff del PR y devuelve
feedback estructurado ÚNICAMENTE cuando encuentres issues reales.
Reglas:
  - Solo comenta líneas que aparecen en el diff (líneas añadidas/modificadas)
  - Sé conciso, directo, actionable
  - NO señales código que está bien — solo problemas
  - Usa español o inglés según el código

Categorías de severidad:
  - 🔴 error: bug, vulnerabilidad, crash potencial
  - ⚠️  warning: mala práctica, code smell, performance
  - 💡 suggestion: mejora opcional, estilo, legibilidad

Devuelve un JSON array EXACTO (sin markdown, sin explicaciones extra):

[
  {
    "path": "ruta/al/archivo.ts",
    "line": 42,
    "severity": "⚠️  warning",
    "body": "Usar `const` en vez de `let` — esta variable nunca se reasigna."
  }
]

Si no hay comentarios, devuelve un array vacío [].
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def run_gh(*args: str, input_data: str | None = None, use_repo: bool = True) -> str:
    """Run gh CLI with GITHUB_TOKEN auth. Returns stdout.
    use_repo=False omits the --repo flag (needed for `gh api`)."""
    cmd = ["gh", "--repo", REPO, *args] if use_repo else ["gh", *args]
    env = {**os.environ, "GH_TOKEN": GH_TOKEN}
    result = subprocess.run(
        cmd,
        input=input_data,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    if result.returncode != 0:
        print(f"::error::gh {' '.join(args)} failed: {result.stderr.strip()}", file=sys.stderr)
        result.check_returncode()
    return result.stdout


def extract_json(text: str) -> str:
    """Pull a JSON array from markdown code fences or raw text."""
    text = text.strip()
    # Try ```json ... ``` block
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        return text[start:end].strip()
    # Try ``` ... ``` block
    if text.startswith("```") and text.endswith("```"):
        return text.strip("` \n").strip()
    # Assume raw JSON
    return text


def summarize_comments(comments: list[dict]) -> str:
    """Build a human-readable markdown summary from comment list."""
    if not comments:
        return "✅ Sin observaciones. El código se ve bien."

    by_severity = {"🔴 error": [], "⚠️  warning": [], "💡 suggestion": []}
    for c in comments:
        sev = c.get("severity", "💡 suggestion")
        by_severity.setdefault(sev, []).append(c)

    lines = ["## 🤖 AI Code Review\n"]
    for sev in ("🔴 error", "⚠️  warning", "💡 suggestion"):
        items = by_severity.get(sev, [])
        if items:
            lines.append(f"### {sev} ({len(items)})")
            for c in items:
                lines.append(f"- **{c['path']}:{c['line']}** — {c['body']}")
            lines.append("")

    if comments:
        lines.append("---")
        lines.append(
            "_Los comentarios inline aparecen en las líneas correspondientes del PR._"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print(f"🔍 Reviewing PR #{PR_NUMBER} in {REPO}")

    # 1. Get diff
    print("  • Fetching diff...")
    diff = run_gh("pr", "diff", PR_NUMBER)
    if not diff.strip():
        print("  ⚠️  Empty diff — nothing to review.")
        return

    # Truncate if too large (model context window)
    if len(diff) > 80_000:
        print(f"  ⚠️  Diff is large ({len(diff)} chars), truncating to 80k...")
        diff = diff[:80_000] + "\n# ... (diff truncated due to size)"

    # 2. Call Claude
    print(f"  • Sending to Claude ({MODEL})...")
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Revisa este PR diff:\n\n{diff}"}],
        )
    except Exception as e:
        print(f"::error::Claude API call failed: {e}")
        sys.exit(1)

    raw = response.content[0].text

    # 3. Parse JSON response
    try:
        json_str = extract_json(raw)
        comments = json.loads(json_str)
        if not isinstance(comments, list):
            raise ValueError("Response is not a JSON array")
    except (json.JSONDecodeError, ValueError) as e:
        print(f"::error::Failed to parse Claude response as JSON: {e}")
        print(f"Raw response:\n{raw}")
        sys.exit(1)

    print(f"  • Claude returned {len(comments)} comment(s)")

    # 4. Build review payload
    review_body = summarize_comments(comments)

    # Map Claude output to GitHub API format
    api_comments = []
    for c in comments:
        api_comments.append({
            "path": c["path"],
            "line": c["line"],
            "side": "RIGHT",
            "body": f"{c.get('severity', '💡 suggestion')} {c['body']}",
        })

    payload = {
        "body": review_body,
        "event": "COMMENT",
        "comments": api_comments,
    }

    # 5. Post review via gh API
    print(f"  • Posting review ({len(api_comments)} inline comments)...")
    result = run_gh(
        "api",
        f"/repos/{REPO}/pulls/{PR_NUMBER}/reviews",
        "--method", "POST",
        "--input", "-",
        input_data=json.dumps(payload),
        use_repo=False,
    )
    review_data = json.loads(result)
    review_url = review_data.get("html_url", "N/A")
    print(f"\n✅ Review posted: {review_url}")


if __name__ == "__main__":
    # Import here so env vars are loaded first
    from anthropic import Anthropic
    main()
