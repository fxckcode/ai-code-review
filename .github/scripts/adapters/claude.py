"""Claude Code CLI adapter.

Transport: argv — ``claude --print`` receives the full review prompt via
stdin and writes Claude's response to stdout.  The orchestrator's
``extract_json`` then extracts the JSON array of comments from that string.

Install: ``npm install -g @anthropic-ai/claude-code``
Auth:    ``AI_REVIEW_CLAUDE_TOKEN`` is remapped to ``ANTHROPIC_API_KEY``
         (the native env var read by the Claude CLI).  The original key is
         removed from the child-process environment so it cannot appear in
         any subprocess env dump.

Threat mitigations (3.4):
- subprocess invoked with a list (no ``shell=True``)
- token value is never written to any log
- stderr/stdout logged as last 8 KB unless ``AI_REVIEW_DEBUG=1``
"""

from __future__ import annotations

import os
import subprocess
import sys

from .base import Adapter

# Environment-variable names
_TOKEN_SRC: str = "AI_REVIEW_CLAUDE_TOKEN"
_TOKEN_DEST: str = "ANTHROPIC_API_KEY"

# Output-log limit (threat check 3.4)
_LOG_TAIL: int = 8 * 1024  # 8 KB

# Minimal env keys passed to npm / claude (no AI_REVIEW_* / GH_TOKEN).
_BASE_ENV_KEYS: tuple[str, ...] = (
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
    "APPDATA",
    "LOCALAPPDATA",
    "NODE_PATH",
    "npm_config_prefix",
    "npm_config_cache",
)


class ClaudeAdapter(Adapter):
    """Argv-transport adapter for the Anthropic Claude Code CLI."""

    transport = "argv"

    # ------------------------------------------------------------------
    # Adapter contract
    # ------------------------------------------------------------------

    def ensure_installed(self) -> None:
        """Verify the auth token and install ``@anthropic-ai/claude-code``.

        The token check is done here so a missing secret fails the job red
        via the orchestrator's ``sys.exit(1)`` handler for install errors,
        rather than falling into the crash-handler that exits green.

        Raises
        ------
        RuntimeError
            On missing token, npm absence, or install failure.
        """
        _require_token()  # fail-fast: missing token → orchestrator exits red
        try:
            subprocess.run(
                ["npm", "install", "-g", "@anthropic-ai/claude-code"],  # no shell=True
                capture_output=True,
                text=True,
                timeout=120,
                check=True,
                env=_minimal_base_env(),  # no secrets for npm install scripts
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"Claude CLI install failed (exit {exc.returncode}): "
                f"{exc.stderr.strip()}"
            ) from exc
        except FileNotFoundError as exc:
            raise RuntimeError(
                "npm not found — Node.js must be available in the runner"
            ) from exc

    def run(self, *, prompt: str, diff_path: str, config: dict) -> str:
        """Invoke ``claude --print`` with *prompt* on stdin.

        Returns raw stdout (a string).  The orchestrator's ``extract_json``
        extracts the JSON array of review comments from this string.

        Parameters
        ----------
        prompt:
            Full review prompt (AGENTS.md + base instructions + PR diff).
        diff_path:
            Unused by this adapter — the diff is already embedded in *prompt*.
        config:
            Orchestrator config; keys used:
            ``"debug"`` (bool) — full output log when truthy;
            ``"timeout"`` (int, default 300) — subprocess timeout in seconds.

        Raises
        ------
        subprocess.TimeoutExpired
            Propagates to the orchestrator which posts a ⚠️ comment and
            exits green (infrastructure failure, not a code problem).
        RuntimeError
            On non-zero exit from the Claude CLI or missing token.
        """
        debug: bool = bool(config.get("debug", False))
        timeout: int = int(config.get("timeout", 300))
        env = _build_env()

        # list-argv subprocess — no shell=True (threat check 3.4)
        result = subprocess.run(
            ["claude", "--print"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,  # TimeoutExpired propagates → orchestrator green handler
            env=env,
        )

        _log_output(result.stdout, result.stderr, debug=debug)

        if result.returncode != 0:
            # Do not embed stderr in the exception — orchestrator may post
            # str(exc) on the PR. Details stay in Actions logs via _log_output.
            raise RuntimeError(f"claude --print exited {result.returncode}")

        return result.stdout


# ---------------------------------------------------------------------------
# Module-level helpers — pure, testable without instantiation
# ---------------------------------------------------------------------------

def _minimal_base_env() -> dict[str, str]:
    """Return a least-privilege env for subprocesses (no AI_REVIEW_* / tokens)."""
    env: dict[str, str] = {}
    for key in _BASE_ENV_KEYS:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    return env


def _require_token() -> str:
    """Return the Claude token value or raise ``RuntimeError``.

    The token VALUE is never written to any log; only the env-var NAME is
    mentioned in the error message (threat check 3.4).
    """
    token = os.environ.get(_TOKEN_SRC, "").strip()
    if not token:
        raise RuntimeError(
            f"Missing required secret {_TOKEN_SRC!r}. "
            "Set it as a repository secret and pass it to the action via "
            f"``secrets.{_TOKEN_SRC}``."
        )
    return token


def _build_env() -> dict[str, str]:
    """Build a least-privilege env for the Claude CLI child process.

    Remaps ``AI_REVIEW_CLAUDE_TOKEN`` → ``ANTHROPIC_API_KEY``. Does **not**
    inherit ``AI_REVIEW_*``, ``GH_TOKEN``, or other job secrets.
    """
    token = _require_token()
    env = _minimal_base_env()
    env[_TOKEN_DEST] = token
    # Suppress telemetry / auto-update network calls in CI unless already set
    if "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC" in os.environ:
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = os.environ[
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"
        ]
    else:
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return env


def _tail(text: str, n: int) -> str:
    """Return the last *n* characters of *text*."""
    return text[-n:] if len(text) > n else text


def _log_output(stdout: str, stderr: str, *, debug: bool) -> None:
    """Write adapter output to stderr.

    Full output when ``debug`` is True (``AI_REVIEW_DEBUG=1``).
    Last 8 KB only otherwise (threat check 3.4).

    Token values are never passed to this function — they live only in the
    child-process environment and are never captured into these strings.
    """
    if debug:
        sys.stderr.write(
            f"[AI_REVIEW_DEBUG] claude stdout ({len(stdout)} chars):\n{stdout}\n"
        )
        if stderr.strip():
            sys.stderr.write(
                f"[AI_REVIEW_DEBUG] claude stderr ({len(stderr)} chars):\n{stderr}\n"
            )
    else:
        combined = stderr if stderr.strip() else stdout
        tail = _tail(combined, _LOG_TAIL)
        if tail.strip():
            sys.stderr.write(f"  [claude] output tail:\n{tail}\n")
