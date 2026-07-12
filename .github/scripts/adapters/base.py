"""Abstract base class for AI agent CLI adapters.

Every agent adapter MUST subclass ``Adapter`` and implement the three
members below.  The orchestrator (``review.py``) depends only on this
contract — no agent-specific logic leaks upward.
"""

from __future__ import annotations

import abc


class Adapter(abc.ABC):
    """Contract every agent adapter must satisfy.

    Subclasses declare their ``transport`` as a class-level attribute and
    implement ``ensure_installed`` and ``run``.  The orchestrator is
    responsible for diff-fetching, AGENTS.md loading, prompt assembly,
    parse retries, comment filtering, and GitHub posting.
    """

    #: How the adapter communicates with the CLI process.
    #: ``"acp"``  — agent uses the Agent Communication Protocol (JSON-RPC);
    #:              the adapter MUST return ``list[dict]`` directly.
    #: ``"argv"`` — agent writes JSON (possibly fenced) to stdout;
    #:              the orchestrator calls ``extract_json`` on the raw output.
    transport: str

    @abc.abstractmethod
    def ensure_installed(self) -> None:
        """Install the CLI tool user-local and ensure it is on PATH.

        Always installs the *latest* published version.  Raises
        ``RuntimeError`` on failure — the orchestrator treats this as a
        fatal error (job exits red).
        """

    @abc.abstractmethod
    def run(self, *, prompt: str, diff_path: str, config: dict) -> list[dict]:
        """Run the agent and return structured code-review comments.

        Parameters
        ----------
        prompt:
            Full review prompt (AGENTS.md + English base instructions).
        diff_path:
            Absolute path to a temp file containing the PR diff text.
        config:
            Orchestrator-level configuration (``model``, ``debug``, etc.).

        Returns
        -------
        list[dict]
            Each dict MUST contain:

            ``path``     (str)  — file path relative to the repo root  
            ``line``     (int)  — 1-based line number within the file  
            ``severity`` (str)  — ``"🔴 error"`` | ``"⚠️ warning"`` | ``"💡 suggestion"``  
            ``body``     (str)  — human-readable comment text

        Notes
        -----
        - ACP adapters parse the protocol themselves and return the final
          ``list[dict]`` directly.
        - Argv adapters MAY return a raw string; the orchestrator will call
          ``extract_json`` on it.  Returning ``list[dict]`` is also accepted.
        - The orchestrator handles parse retries; do not retry internally.
        - Raise ``subprocess.TimeoutExpired`` or any ``Exception`` to signal
          a crash — the orchestrator posts a ⚠️ warning comment and exits
          green (timeout/crash UX).
        """
