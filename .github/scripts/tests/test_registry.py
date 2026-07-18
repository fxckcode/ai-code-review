"""Tests for the adapter registry.

Covers: unknown agent rejection, valid enum acceptance, unimplemented adapter
failure with the correct error message.
"""

from __future__ import annotations

import pytest

from adapters import registry
from adapters.base import Adapter


class TestRegistrySelect:
    # --- Unknown agent is rejected ------------------------------------------

    def test_unknown_agent_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown agent"):
            registry.select("gemini")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            registry.select("")

    def test_random_string_raises(self):
        with pytest.raises(ValueError):
            registry.select("gpt-4")

    # --- Normalisation (strip + lower) ----------------------------------------

    def test_uppercase_is_normalised(self):
        adapter = registry.select("CLAUDE")
        assert adapter is not None

    def test_whitespace_stripped(self):
        adapter = registry.select("  opencode  ")
        assert adapter is not None

    def test_mixed_case_normalised(self):
        adapter = registry.select("AntiGravity")
        assert adapter is not None

    # --- Valid agents return Adapter instances ---------------------------------

    @pytest.mark.parametrize("agent", ["claude", "opencode", "cursor", "antigravity"])
    def test_valid_agent_returns_adapter(self, agent: str):
        adapter = registry.select(agent)
        assert isinstance(adapter, Adapter)

    # --- Unimplemented adapters fail with correct message ---------------------

    @pytest.mark.parametrize("agent", ["cursor", "antigravity"])
    def test_unimplemented_ensure_installed_raises(self, agent: str):
        adapter = registry.select(agent)
        with pytest.raises(NotImplementedError, match="not implemented yet"):
            adapter.ensure_installed()

    @pytest.mark.parametrize("agent", ["cursor", "antigravity"])
    def test_unimplemented_run_raises(self, agent: str):
        adapter = registry.select(agent)
        with pytest.raises(NotImplementedError, match="not implemented yet"):
            adapter.run(prompt="p", diff_path="/tmp/x.diff", config={})

    def test_claude_real_ensure_installed_requires_token(self, monkeypatch):
        monkeypatch.delenv("AI_REVIEW_CLAUDE_TOKEN", raising=False)
        adapter = registry.select("claude")
        with pytest.raises(RuntimeError, match="AI_REVIEW_CLAUDE_TOKEN"):
            adapter.ensure_installed()

    def test_claude_real_run_requires_token(self, monkeypatch):
        monkeypatch.delenv("AI_REVIEW_CLAUDE_TOKEN", raising=False)
        adapter = registry.select("claude")
        with pytest.raises(RuntimeError, match="AI_REVIEW_CLAUDE_TOKEN"):
            adapter.run(prompt="p", diff_path="/tmp/x.diff", config={})

    def test_opencode_real_ensure_installed_requires_token(self, monkeypatch):
        monkeypatch.delenv("AI_REVIEW_OPENCODE_TOKEN", raising=False)
        adapter = registry.select("opencode")
        with pytest.raises(RuntimeError, match="AI_REVIEW_OPENCODE_TOKEN"):
            adapter.ensure_installed()

    def test_opencode_real_run_requires_token(self, monkeypatch):
        monkeypatch.delenv("AI_REVIEW_OPENCODE_TOKEN", raising=False)
        adapter = registry.select("opencode")
        with pytest.raises(RuntimeError, match="AI_REVIEW_OPENCODE_TOKEN"):
            adapter.run(prompt="p", diff_path="/tmp/x.diff", config={})

    # --- Transport attribute present ------------------------------------------

    @pytest.mark.parametrize("agent", ["claude", "opencode", "cursor", "antigravity"])
    def test_adapter_has_transport(self, agent: str):
        adapter = registry.select(agent)
        assert hasattr(adapter, "transport")
        assert adapter.transport in ("acp", "argv")
