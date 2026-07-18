"""OpenCode CLI adapter.

Transport: argv — ``opencode run`` with the review prompt attached as a file;
stdout is parsed by the orchestrator's ``extract_json``.

Install: ``npm install -g opencode-ai``
Auth:    ``AI_REVIEW_OPENCODE_TOKEN`` is remapped to ``OPENAI_API_KEY``
         (OpenCode reads standard provider env keys).  The source key is not
         present in the child environment.

Threat mitigations:
- subprocess argv list (no ``shell=True``)
- least-privilege env (no other ``AI_REVIEW_*`` / ``GH_TOKEN``)
- token never logged; crash messages stay generic
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from .base import Adapter

_TOKEN_SRC: str = "AI_REVIEW_OPENCODE_TOKEN"
_TOKEN_DEST: str = "OPENAI_API_KEY"
_LOG_TAIL: int = 8 * 1024

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
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
)


class OpencodeAdapter(Adapter):
    """Argv-transport adapter for the OpenCode CLI."""

    transport = "argv"

    def ensure_installed(self) -> None:
        """Verify auth token and install ``opencode-ai`` globally (latest)."""
        _require_token()
        try:
            subprocess.run(
                ["npm", "install", "-g", "opencode-ai"],
                capture_output=True,
                text=True,
                timeout=180,
                check=True,
                env=_minimal_base_env(),
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"OpenCode CLI install failed (exit {exc.returncode}): "
                f"{exc.stderr.strip()}"
            ) from exc
        except FileNotFoundError as exc:
            raise RuntimeError(
                "npm not found — Node.js must be available in the runner"
            ) from exc

    def run(self, *, prompt: str, diff_path: str, config: dict) -> str:
        """Invoke ``opencode run`` with the prompt attached as a file.

        Returns raw stdout for the orchestrator to parse.
        """
        debug: bool = bool(config.get("debug", False))
        timeout: int = int(config.get("timeout", 300))
        env = _build_env()

        prompt_file = _write_prompt_file(prompt)
        try:
            cmd = [
                "opencode",
                "run",
                "--pure",
                "--format",
                "default",
                "--auto",
                "-f",
                prompt_file,
                "Follow the attached review instructions exactly. "
                "Reply with ONLY the JSON array of review comments "
                "(no markdown fences required if the whole reply is the array).",
            ]
            if config.get("model"):
                cmd.extend(["-m", str(config["model"])])

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        finally:
            try:
                os.unlink(prompt_file)
            except OSError:
                pass

        _log_output(result.stdout, result.stderr, debug=debug)

        if result.returncode != 0:
            raise RuntimeError(f"opencode run exited {result.returncode}")

        return result.stdout


def _write_prompt_file(prompt: str) -> str:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(prompt)
        tmp.flush()
        return tmp.name
    finally:
        tmp.close()


def _minimal_base_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key in _BASE_ENV_KEYS:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    return env


def _require_token() -> str:
    token = os.environ.get(_TOKEN_SRC, "").strip()
    if not token:
        raise RuntimeError(
            f"Missing required secret {_TOKEN_SRC!r}. "
            "Set it as a repository secret (OpenAI/compatible API key for OpenCode) "
            f"and pass it via ``secrets.{_TOKEN_SRC}``."
        )
    return token


def _build_env() -> dict[str, str]:
    """Least-privilege env: remap token → ``OPENAI_API_KEY`` only."""
    token = _require_token()
    env = _minimal_base_env()
    env[_TOKEN_DEST] = token
    return env


def _tail(text: str, n: int) -> str:
    return text[-n:] if len(text) > n else text


def _log_output(stdout: str, stderr: str, *, debug: bool) -> None:
    if debug:
        sys.stderr.write(
            f"[AI_REVIEW_DEBUG] opencode stdout ({len(stdout)} chars):\n{stdout}\n"
        )
        if stderr.strip():
            sys.stderr.write(
                f"[AI_REVIEW_DEBUG] opencode stderr ({len(stderr)} chars):\n{stderr}\n"
            )
    else:
        combined = stderr if stderr.strip() else stdout
        tail = _tail(combined, _LOG_TAIL)
        if tail.strip():
            sys.stderr.write(f"  [opencode] output tail:\n{tail}\n")
