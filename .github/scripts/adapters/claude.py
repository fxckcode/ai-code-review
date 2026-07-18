"""Claude adapter — stub placeholder for Phase 3.

Phase 3 will replace this with the real implementation that:
- Installs the Claude CLI user-local at latest
- Remaps ``AI_REVIEW_CLAUDE_TOKEN`` to the native Claude env var
- Supports both ACP and argv (``-p``) transport modes
- Requires ``AI_REVIEW_GITHUB_TOKEN`` for posting
"""

from __future__ import annotations

from .base import Adapter

_NOT_IMPLEMENTED = "Claude adapter: not implemented yet"


class ClaudeAdapter(Adapter):
    transport = "argv"

    def ensure_installed(self) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def run(self, *, prompt: str, diff_path: str, config: dict) -> list[dict]:
        raise NotImplementedError(_NOT_IMPLEMENTED)
