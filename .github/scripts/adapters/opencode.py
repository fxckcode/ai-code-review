"""OpenCode adapter — stub placeholder for a future phase."""

from __future__ import annotations

from .base import Adapter

_NOT_IMPLEMENTED = "OpenCode adapter: not implemented yet"


class OpencodeAdapter(Adapter):
    transport = "argv"

    def ensure_installed(self) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def run(self, *, prompt: str, diff_path: str, config: dict) -> list[dict]:
        raise NotImplementedError(_NOT_IMPLEMENTED)
