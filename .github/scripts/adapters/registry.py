"""Adapter registry: enum validation and single-adapter selection.

Supported agent names (after ``strip().lower()``):
    claude | opencode | cursor | antigravity

Any other value raises ``ValueError`` so the orchestrator can fail fast.
Exactly one adapter is instantiated per invocation.
"""

from __future__ import annotations

from .base import Adapter

_VALID_AGENTS: frozenset[str] = frozenset({"claude", "opencode", "cursor", "antigravity"})


def select(agent_raw: str) -> Adapter:
    """Validate *agent_raw* and return the corresponding ``Adapter`` instance.

    Normalises via ``strip().lower()``.  Unknown values raise ``ValueError``
    so the orchestrator can exit red immediately.

    Parameters
    ----------
    agent_raw:
        Raw value of the ``agent`` action input / ``AI_AGENT`` env var.

    Returns
    -------
    Adapter
        A ready-to-use adapter instance (``ensure_installed`` not yet called).

    Raises
    ------
    ValueError
        When *agent_raw* is not in the supported enum.
    """
    agent = agent_raw.strip().lower()
    if agent not in _VALID_AGENTS:
        raise ValueError(
            f"Unknown agent {agent!r}. Valid values: {sorted(_VALID_AGENTS)}"
        )

    if agent == "claude":
        from .claude import ClaudeAdapter
        return ClaudeAdapter()
    if agent == "opencode":
        from .opencode import OpencodeAdapter
        return OpencodeAdapter()
    if agent == "cursor":
        from .cursor import CursorAdapter
        return CursorAdapter()
    if agent == "antigravity":
        from .antigravity import AntigravityAdapter
        return AntigravityAdapter()

    # Unreachable — kept for static-analysis exhaustiveness
    raise ValueError(f"Unhandled agent: {agent!r}")  # pragma: no cover
