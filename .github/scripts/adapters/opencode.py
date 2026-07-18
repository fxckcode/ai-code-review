"""OpenCode CLI adapter over ACP (Agent Client Protocol).

Transport: ``acp`` — spawns ``opencode acp`` and speaks JSON-RPC NDJSON on
stdio. Agent reply text is collected from ``session/update`` notifications
(``agent_message_chunk``) and parsed into the review comment array.

Install: ``npm install -g opencode-ai``
Auth:    ``AI_REVIEW_OPENCODE_TOKEN`` → ``OPENCODE_API_KEY`` (see
         https://opencode.ai/docs/acp/).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Optional

from .base import Adapter

_TOKEN_SRC = "AI_REVIEW_OPENCODE_TOKEN"
_TOKEN_DEST = "OPENCODE_API_KEY"
_LOG_TAIL = 8 * 1024

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
    """ACP-transport adapter for OpenCode (``opencode acp``)."""

    transport = "acp"

    def ensure_installed(self) -> None:
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
        """Run a one-shot ACP prompt turn; return agent message text for parse."""
        debug = bool(config.get("debug", False))
        timeout = int(config.get("timeout", 300))
        cwd = os.environ.get("GITHUB_WORKSPACE") or os.getcwd()

        return _acp_prompt_turn(
            prompt=prompt,
            cwd=cwd,
            env=_build_env(),
            timeout=timeout,
            debug=debug,
        )


# ---------------------------------------------------------------------------
# ACP client (stdio JSON-RPC NDJSON)
# ---------------------------------------------------------------------------

def _acp_prompt_turn(
    *,
    prompt: str,
    cwd: str,
    env: dict[str, str],
    timeout: int,
    debug: bool,
) -> str:
    """initialize → session/new → session/prompt; return agent message text."""
    cmd = [_opencode_bin(), "acp", "--pure", "--cwd", cwd]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        env=env,
        bufsize=1,
    )
    assert proc.stdin and proc.stdout and proc.stderr

    q: Queue[Optional[dict[str, Any]]] = Queue()
    message_chunks: list[str] = []
    thought_chunks: list[str] = []
    stderr_buf: list[str] = []

    def _stdout_reader() -> None:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                q.put(json.loads(line))
            except json.JSONDecodeError:
                if debug:
                    sys.stderr.write(f"[AI_REVIEW_DEBUG] non-json: {line[:200]}\n")
        q.put(None)

    def _stderr_reader() -> None:
        for line in proc.stderr:
            stderr_buf.append(line)
        # keep quiet unless debug / failure

    threading.Thread(target=_stdout_reader, daemon=True).start()
    threading.Thread(target=_stderr_reader, daemon=True).start()

    deadline = time.time() + timeout
    next_id = 1

    def send(msg: dict[str, Any]) -> None:
        proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        proc.stdin.flush()

    def rpc(method: str, params: dict[str, Any]) -> dict[str, Any]:
        nonlocal next_id
        req_id = next_id
        next_id += 1
        send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        return _wait_rpc(
            proc, q, req_id, deadline, message_chunks, thought_chunks, send
        )

    try:
        rpc(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": True, "writeTextFile": False},
                },
                "clientInfo": {"name": "ai-code-review", "version": "1.0.0"},
            },
        )
        session = rpc("session/new", {"cwd": cwd, "mcpServers": []})
        session_id = session.get("sessionId")
        if not session_id:
            raise RuntimeError("opencode acp session/new missing sessionId")

        # Prefixed instruction: CI review is text-only JSON (no tools).
        review_prompt = (
            "Do not use tools or shell commands. "
            "Respond with the review JSON array only.\n\n"
            + prompt
        )
        result = rpc(
            "session/prompt",
            {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": review_prompt}],
            },
        )
        stop = result.get("stopReason")
        if debug:
            sys.stderr.write(f"[AI_REVIEW_DEBUG] stopReason={stop!r}\n")

        text = "".join(message_chunks).strip()
        if not text:
            # Some OpenCode turns only stream agent_thought_chunk.
            text = "".join(thought_chunks).strip()
        if not text:
            raise RuntimeError("opencode acp returned empty agent message")
        if debug:
            sys.stderr.write(
                f"[AI_REVIEW_DEBUG] agent text ({len(text)} chars):\n{text}\n"
            )
        return text
    except Exception:
        tail = "".join(stderr_buf)[-_LOG_TAIL:]
        if tail.strip():
            sys.stderr.write(f"  [opencode-acp] stderr tail:\n{tail}\n")
        raise
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _wait_rpc(
    proc: subprocess.Popen[str],
    q: Queue[Optional[dict[str, Any]]],
    req_id: int,
    deadline: float,
    message_chunks: list[str],
    thought_chunks: list[str],
    send,
) -> dict[str, Any]:
    while time.time() < deadline:
        try:
            msg = q.get(timeout=0.5)
        except Empty:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"opencode acp exited early (code {proc.returncode})"
                )
            continue
        if msg is None:
            raise RuntimeError("opencode acp stdout closed")

        # Notifications
        if "method" in msg and "id" not in msg:
            if msg.get("method") == "session/update":
                _collect_chunk(
                    msg.get("params") or {}, message_chunks, thought_chunks
                )
            continue

        # Agent → client requests
        if "method" in msg and "id" in msg:
            _handle_agent_request(msg, send)
            continue

        if msg.get("id") == req_id:
            if "error" in msg:
                raise RuntimeError(f"opencode acp error: {msg['error']}")
            return msg.get("result") or {}

    raise subprocess.TimeoutExpired(cmd="opencode acp", timeout=int(deadline))


def _collect_chunk(
    params: dict[str, Any],
    message_chunks: list[str],
    thought_chunks: list[str],
) -> None:
    update = params.get("update") or {}
    kind = update.get("sessionUpdate")
    content = update.get("content") or {}
    if not (isinstance(content, dict) and content.get("type") == "text"):
        return
    text = content.get("text")
    if not text:
        return
    if kind == "agent_message_chunk":
        message_chunks.append(str(text))
    elif kind == "agent_thought_chunk":
        thought_chunks.append(str(text))


def _handle_agent_request(msg: dict[str, Any], send) -> None:
    """Auto-reply to agent→client requests.

    Permission requests are cancelled so the model stays text-only for review.
    File reads are served when possible (absolute paths).
    """
    method = msg.get("method")
    req_id = msg["id"]
    params = msg.get("params") or {}

    if method == "session/request_permission":
        send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"outcome": {"outcome": "cancelled"}},
            }
        )
        return

    if method == "fs/read_text_file":
        path = params.get("path")
        try:
            content = Path(path).read_text(encoding="utf-8") if path else ""
            send({"jsonrpc": "2.0", "id": req_id, "result": {"content": content}})
        except OSError as exc:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": str(exc)},
                }
            )
        return

    send(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not supported: {method}"},
        }
    )


# ---------------------------------------------------------------------------
# Env / binary helpers
# ---------------------------------------------------------------------------

def _opencode_bin() -> str:
    which = shutil.which("opencode")
    if which and not which.lower().endswith(".ps1"):
        return which
    # Linux/mac npm global or Windows .exe
    for candidate in (
        Path(os.environ.get("APPDATA", ""))
        / "npm"
        / "node_modules"
        / "opencode-ai"
        / "bin"
        / "opencode.exe",
        Path("/usr/local/bin/opencode"),
        Path.home() / ".local" / "bin" / "opencode",
    ):
        if candidate.is_file():
            return str(candidate)
    if which:
        return which
    return "opencode"


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
            "Set your OpenCode API key as that secret "
            f"(remapped to {_TOKEN_DEST})."
        )
    return token


def _build_env() -> dict[str, str]:
    token = _require_token()
    env = _minimal_base_env()
    env[_TOKEN_DEST] = token
    return env
