#!/usr/bin/env python3
"""
AI Code Review — vendor-neutral orchestrator.

Fetches the PR diff, reads AGENTS.md, builds an English review prompt,
selects the configured agent adapter, runs it with one parse retry,
filters/anchors comments against the diff, then posts a GitHub PR review.

Agent selection is driven by the ``AI_AGENT`` environment variable (set by
the composite ``action.yml`` from the ``agent`` input).  No vendor-specific
SDK is imported here — all vendor logic lives in ``adapters/``.

Exit codes
----------
0   Review posted successfully, OR adapter timed out / crashed (⚠️ comment posted).
1   Fatal error: unknown agent, missing secrets, empty AGENTS.md, or parse
    failure after the allowed retry.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Runtime configuration (all from environment)
# ---------------------------------------------------------------------------
AGENT: str = os.environ.get("AI_AGENT", "").strip().lower()
REPO: str = os.environ.get("REPO") or os.environ.get("GITHUB_REPOSITORY", "")
PR_NUMBER: str = os.environ.get("PR_NUMBER") or os.environ.get("GITHUB_PR_NUMBER", "")
GH_TOKEN: str = (
    os.environ.get("AI_REVIEW_GITHUB_TOKEN")
    or os.environ.get("GH_TOKEN")
    or ""
)
DEBUG: bool = os.environ.get("AI_REVIEW_DEBUG", "") == "1"

MAX_DIFF_CHARS: int = 80_000
ADAPTER_TIMEOUT: int = 300  # 5 minutes; enforced at adapter call sites

# ---------------------------------------------------------------------------
# Helpers — pure functions (importable and testable)
# ---------------------------------------------------------------------------

def extract_json(text: str) -> list:
    """Extract a JSON array from *text*, trying plain JSON then code fences.

    Parse order
    -----------
    1. Plain ``json.loads`` (no fences).
    2. ````json … ```` fenced block.
    3. Generic ```` ``` … ``` ```` fenced block.

    Raises
    ------
    ValueError
        When no parseable JSON array is found after all strategies.
    json.JSONDecodeError
        When a fence is found but its content is not valid JSON.
    """
    text = text.strip()

    def _as_array(raw: str) -> list:
        result = json.loads(raw)
        if not isinstance(result, list):
            raise ValueError("Expected a JSON array of review comments")
        return result

    # Strategy 1: plain JSON
    try:
        return _as_array(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 2: ```json … ``` fence
    m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        return _as_array(m.group(1))

    # Strategy 3: generic ``` … ``` fence
    m = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        return _as_array(m.group(1))

    raise ValueError("No parseable JSON array found in adapter output")


def parse_diff_anchors(diff_text: str) -> set[tuple[str, int]]:
    """Return the set of ``(path, new_line)`` pairs present in *diff_text*.

    Only *added* lines (``+`` prefix) are considered valid anchor targets,
    matching what the GitHub Pulls Review API accepts for inline comments.

    Context lines are counted to keep the line-number cursor accurate but
    are not included in the returned set.
    """
    anchors: set[tuple[str, int]] = set()
    current_file: Optional[str] = None
    new_line: int = 0

    for raw in diff_text.splitlines():
        if raw.startswith("+++ b/"):
            current_file = raw[6:]
            new_line = 0
        elif raw.startswith("@@ "):
            m = re.search(r"\+(\d+)", raw)
            if m:
                new_line = int(m.group(1)) - 1  # pre-decremented; first line increments
        elif raw.startswith("+++") or raw.startswith("---"):
            pass  # header lines — ignore
        elif raw.startswith("\\"):
            pass  # "No newline at end of file" marker
        elif raw.startswith("diff ") or raw.startswith("index "):
            current_file = None
            new_line = 0
        elif raw.startswith("+") and current_file:
            new_line += 1
            anchors.add((current_file, new_line))
        elif raw.startswith("-"):
            pass  # deletion — does not advance new_line counter
        elif current_file:
            new_line += 1  # context line — advance counter but don't anchor

    return anchors


def filter_and_anchor(
    comments: list[dict], diff_text: str
) -> tuple[list[dict], list[dict]]:
    """Split *comments* into ``(anchored, unanchored)`` based on the diff.

    Comments whose ``(path, line)`` pair is not in the diff are dropped and
    returned in *unanchored*.  Callers should log the dropped items.

    Parameters
    ----------
    comments:
        Raw comment dicts from the adapter (each with ``path`` and ``line``).
    diff_text:
        Full PR diff text (used to compute valid anchor positions).

    Returns
    -------
    tuple[list[dict], list[dict]]
        ``(anchored, unanchored)`` — neither list is mutated; items are the
        original dicts.
    """
    valid_anchors = parse_diff_anchors(diff_text)
    anchored: list[dict] = []
    unanchored: list[dict] = []

    for c in comments:
        key = (c.get("path", ""), int(c.get("line", 0)))
        if key in valid_anchors:
            anchored.append(c)
        else:
            unanchored.append(c)

    return anchored, unanchored


def severity_to_event(comments: list[dict]) -> str:
    """Return the GitHub review event for *comments*.

    Returns ``"REQUEST_CHANGES"`` when at least one comment carries a 🔴 error
    severity; ``"COMMENT"`` otherwise.  *comments* MUST already be filtered to
    anchored-only before calling this function.
    """
    for c in comments:
        if "\U0001f534" in c.get("severity", ""):  # 🔴
            return "REQUEST_CHANGES"
    return "COMMENT"


def summarize(
    agent: str,
    anchored: list[dict],
    unanchored: Optional[list[dict]] = None,
) -> str:
    """Build an English markdown review summary body.

    The summary is always prefixed ``AI Code Review ({agent})`` per spec.

    Parameters
    ----------
    agent:
        Normalised agent name (e.g. ``"claude"``).
    anchored:
        Comments successfully placed as inline annotations.
    unanchored:
        Comments that could not be anchored to the diff (listed in the body).
    """
    if unanchored is None:
        unanchored = []

    lines: list[str] = [f"## AI Code Review ({agent})\n"]

    if not anchored and not unanchored:
        lines.append("✅ No issues found. The code looks good.")
        return "\n".join(lines)

    if anchored:
        by_sev: dict[str, list[dict]] = {
            "🔴 error": [],
            "⚠️ warning": [],
            "💡 suggestion": [],
        }
        for c in anchored:
            sev = c.get("severity", "💡 suggestion")
            by_sev.setdefault(sev, []).append(c)

        for sev in ("🔴 error", "⚠️ warning", "💡 suggestion"):
            items = by_sev.get(sev, [])
            if items:
                lines.append(f"### {sev} ({len(items)})")
                for c in items:
                    lines.append(f"- **{c['path']}:{c['line']}** — {c['body']}")
                lines.append("")

    if unanchored:
        lines.append("### ⚠️ Comments not anchored to diff")
        lines.append(
            "_These comments could not be placed as inline annotations "
            "because the referenced lines are not part of this diff:_\n"
        )
        for c in unanchored:
            path = c.get("path", "?")
            line = c.get("line", "?")
            body = c.get("body", "")
            lines.append(f"- **{path}:{line}** — {body}")
        lines.append("")

    lines.append("---")
    lines.append("_Inline comments appear on the relevant lines of the PR._")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def _gh_env() -> dict[str, str]:
    """Least-privilege env for ``gh`` — no AI_REVIEW_* / Anthropic secrets."""
    keys = (
        "PATH",
        "HOME",
        "USER",
        "USERPROFILE",
        "USERNAME",
        "TEMP",
        "TMP",
        "TMPDIR",
        "SystemRoot",
        "SYSTEMROOT",
        "ComSpec",
        "COMSPEC",
        "LANG",
        "LC_ALL",
        "TZ",
        "GH_HOST",
        "GH_ENTERPRISE_TOKEN",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
    )
    env: dict[str, str] = {}
    for key in keys:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    env["GH_TOKEN"] = GH_TOKEN
    return env


def run_gh(
    *args: str,
    input_data: Optional[str] = None,
    use_repo: bool = True,
) -> str:
    """Run ``gh`` CLI with ``GH_TOKEN`` auth.  Returns stdout on success."""
    cmd = ["gh", "--repo", REPO, *args] if use_repo else ["gh", *args]
    result = subprocess.run(
        cmd,
        input=input_data,
        capture_output=True,
        text=True,
        timeout=120,
        env=_gh_env(),
    )
    if result.returncode != 0:
        print(
            f"::error::gh {' '.join(str(a) for a in args)} failed: "
            f"{result.stderr.strip()}",
            file=sys.stderr,
        )
        result.check_returncode()
    return result.stdout


def post_review(
    agent: str,
    event: str,
    body: str,
    inline: list[dict],
) -> str:
    """Post a new GitHub PR review.  Returns the HTML URL of the review."""
    api_comments = [
        {
            "path": c["path"],
            "line": c["line"],
            "side": "RIGHT",
            "body": f"{c.get('severity', '💡 suggestion')} {c['body']}",
        }
        for c in inline
    ]
    payload = {"body": body, "event": event, "comments": api_comments}
    result = run_gh(
        "api",
        f"/repos/{REPO}/pulls/{PR_NUMBER}/reviews",
        "--method", "POST",
        "--input", "-",
        input_data=json.dumps(payload),
        use_repo=False,
    )
    return json.loads(result).get("html_url", "N/A")


def post_failure_comment(agent: str, reason: str) -> None:
    """Post a ⚠️ failed-review COMMENT, then exit 0 (job stays green).

    Called on adapter timeout or crash — per spec the job must remain green
    in these cases so as not to block PRs due to agent infrastructure issues.
    """
    body = (
        f"## ⚠️ AI Code Review ({agent}) — Review Failed\n\n"
        f"{reason}\n\n"
        "_The review could not be completed. Please retry or check the "
        "agent configuration._"
    )
    try:
        post_review(agent, "COMMENT", body, [])
    except Exception as exc:
        print(f"::warning::Could not post failure comment: {exc}", file=sys.stderr)
    sys.exit(0)


# ---------------------------------------------------------------------------
# Orchestrator core — extracted for testability
# ---------------------------------------------------------------------------

def run_with_retry(
    adapter,
    prompt: str,
    diff_path: str,
    config: dict,
    max_attempts: int = 2,
) -> list[dict]:
    """Run *adapter* and parse the result, retrying once on parse failure.

    Parameters
    ----------
    adapter:
        Any object with a ``run(*, prompt, diff_path, config)`` method.
    max_attempts:
        Maximum number of parse attempts (default 2, i.e. one retry).

    Returns
    -------
    list[dict]
        Parsed comment list.

    Raises
    ------
    ValueError
        After *max_attempts* failed parse attempts.
    Exception
        Propagates adapter runtime errors (crash/timeout) to the caller.
    """
    last_error: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        print(f"  • Running adapter (attempt {attempt}/{max_attempts})...")
        raw = adapter.run(prompt=prompt, diff_path=diff_path, config=config)

        # ACP adapters already return list[dict]
        if isinstance(raw, list):
            return raw

        # Argv adapters return a string — parse it
        try:
            return extract_json(str(raw))
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            print(f"  ✗ Parse attempt {attempt} failed: {exc}", file=sys.stderr)
            if attempt < max_attempts:
                print("  • Retrying...", file=sys.stderr)

    raise ValueError(
        f"Failed to parse adapter output after {max_attempts} attempts: {last_error}"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # --- Preflight checks ---------------------------------------------------
    if not AGENT:
        print("::error::AI_AGENT environment variable is required", file=sys.stderr)
        sys.exit(1)

    if not REPO or not PR_NUMBER:
        print(
            "::error::REPO/GITHUB_REPOSITORY and PR_NUMBER/GITHUB_PR_NUMBER are required",
            file=sys.stderr,
        )
        sys.exit(1)

    if not GH_TOKEN:
        print(
            "::error::AI_REVIEW_GITHUB_TOKEN (or GH_TOKEN) is required",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"🔍 AI Code Review ({AGENT}) — PR #{PR_NUMBER} in {REPO}")

    # --- Select adapter (fails fast on unknown agent) -----------------------
    from adapters import registry  # noqa: PLC0415 — deferred to allow test mocking

    try:
        adapter = registry.select(AGENT)
    except ValueError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        sys.exit(1)

    # --- Mandatory AGENTS.md ------------------------------------------------
    agents_md_path = Path("AGENTS.md")
    if not agents_md_path.exists():
        print(
            "::error::AGENTS.md is missing at repo root. "
            "Create it before running the AI Code Review action.",
            file=sys.stderr,
        )
        sys.exit(1)

    agents_md = agents_md_path.read_text(encoding="utf-8").strip()
    if not agents_md:
        print(
            "::error::AGENTS.md is empty. "
            "Populate it with review guidelines before running.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("  • AGENTS.md loaded")

    # --- Fetch diff ---------------------------------------------------------
    print("  • Fetching PR diff...")
    diff = run_gh("pr", "diff", PR_NUMBER)

    if not diff.strip():
        print("  ⚠️  Empty diff — nothing to review.")
        url = post_review(
            AGENT,
            "COMMENT",
            f"## AI Code Review ({AGENT})\n\nNo changes to review.",
            [],
        )
        print(f"\n✅ Posted empty-diff notice: {url}")
        return

    truncated = False
    if len(diff) > MAX_DIFF_CHARS:
        print(
            f"  ⚠️  Diff is {len(diff):,} chars — truncating to {MAX_DIFF_CHARS:,}..."
        )
        diff = diff[:MAX_DIFF_CHARS]
        truncated = True

    # --- Build prompt -------------------------------------------------------
    truncation_note = (
        "\n\n**Note:** The diff was truncated to 80,000 characters due to its size. "
        "Review what is visible and note the truncation in your summary if relevant."
        if truncated
        else ""
    )

    base_instructions = (
        "You are a senior code reviewer. Review the PR diff below and return "
        "structured feedback ONLY for real issues you find.\n\n"
        "Rules:\n"
        "- Only comment on lines present in the diff (added or modified lines)\n"
        "- Be concise, direct, and actionable\n"
        "- Do NOT comment on code that is correct — only flag real problems\n"
        "- Write all comments in English\n\n"
        "Severity categories:\n"
        "- 🔴 error: bug, vulnerability, or potential crash\n"
        "- ⚠️ warning: bad practice, code smell, or performance issue\n"
        "- 💡 suggestion: optional improvement, style, or readability\n\n"
        "Return an EXACT JSON array (no markdown, no extra explanation):\n\n"
        '[\n  {\n    "path": "path/to/file.ts",\n    "line": 42,\n'
        '    "severity": "⚠️ warning",\n'
        '    "body": "Use `const` instead of `let` — this variable is never reassigned."\n'
        "  }\n]\n\n"
        "If there are no issues, return an empty array []."
    )

    prompt = (
        f"{agents_md}\n\n"
        "---\n\n"
        f"{base_instructions}"
        f"{truncation_note}\n\n"
        f"Review this PR diff:\n\n{diff}"
    )

    # --- Write diff to temp file (adapter may read it directly) -------------
    diff_tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".diff", delete=False, encoding="utf-8"
    )
    try:
        diff_tmp.write(diff)
        diff_tmp.flush()
        diff_path = diff_tmp.name
    finally:
        diff_tmp.close()

    try:
        # --- Ensure adapter is installed ----------------------------------------
        print("  • Ensuring adapter is installed...")
        try:
            adapter.ensure_installed()
        except Exception as exc:
            print(f"::error::Adapter install failed: {exc}", file=sys.stderr)
            sys.exit(1)

        # --- Run adapter with retry on parse failure ----------------------------
        config = {
            "model": os.environ.get("AI_REVIEW_MODEL", ""),
            "debug": DEBUG,
            "timeout": ADAPTER_TIMEOUT,
        }
        comments: list[dict]

        try:
            comments = run_with_retry(adapter, prompt, diff_path, config)
        except subprocess.TimeoutExpired:
            post_failure_comment(AGENT, "The agent timed out after 5 minutes.")
            return  # post_failure_comment calls sys.exit(0)
        except (ValueError, json.JSONDecodeError) as exc:
            # Parse failure exhausted — job exits red
            print(f"::error::Parse failure after retry: {exc}", file=sys.stderr)
            sys.exit(1)
        except Exception as exc:
            # Adapter crash — post generic ⚠️ comment (no stderr/exc on PR), stay green
            print(f"::error::Adapter crash: {exc}", file=sys.stderr)
            post_failure_comment(
                AGENT,
                "The agent encountered an unexpected error. "
                "Check the Actions log for details.",
            )
            return

        print(f"  • Adapter returned {len(comments)} comment(s)")

        # --- Filter and anchor comments against the diff ------------------------
        anchored, unanchored = filter_and_anchor(comments, diff)
        if unanchored:
            print(
                f"  ↷ {len(unanchored)} comment(s) dropped (not in diff):",
                file=sys.stderr,
            )
            for c in unanchored:
                print(
                    f"      {c.get('path', '?')}:{c.get('line', '?')}",
                    file=sys.stderr,
                )

        # --- Determine review event ---------------------------------------------
        if anchored:
            event = severity_to_event(anchored)
        else:
            event = "COMMENT"

        # --- Build body and post review -----------------------------------------
        body = summarize(AGENT, anchored, unanchored)
        print(f"  • Posting review (event={event}, inline={len(anchored)})...")
        url = post_review(AGENT, event, body, anchored)
        print(f"\n✅ Review posted ({event}): {url}")
    finally:
        try:
            os.unlink(diff_path)
        except OSError:
            pass


if __name__ == "__main__":
    # When invoked as a script, ensure the scripts directory is on sys.path
    # so that ``from adapters import registry`` resolves correctly.
    _scripts_dir = str(Path(__file__).parent)
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)
    main()
