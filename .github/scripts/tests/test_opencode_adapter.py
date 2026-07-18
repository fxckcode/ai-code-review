"""Unit tests for OpenCode ACP adapter helpers (no live CLI required)."""

from __future__ import annotations

import pytest

from adapters.opencode import OpencodeAdapter, _build_env, _collect_chunk, _require_token


class TestToken:
    def test_missing_token(self, monkeypatch):
        monkeypatch.delenv("AI_REVIEW_OPENCODE_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="AI_REVIEW_OPENCODE_TOKEN"):
            _require_token()

    def test_ensure_installed_requires_token(self, monkeypatch):
        monkeypatch.delenv("AI_REVIEW_OPENCODE_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="AI_REVIEW_OPENCODE_TOKEN"):
            OpencodeAdapter().ensure_installed()


class TestEnv:
    def test_remaps_to_opencode_api_key(self, monkeypatch):
        monkeypatch.setenv("AI_REVIEW_OPENCODE_TOKEN", "oc-key")
        env = _build_env()
        assert env.get("OPENCODE_API_KEY") == "oc-key"
        assert "AI_REVIEW_OPENCODE_TOKEN" not in env
        assert "OPENAI_API_KEY" not in env

    def test_no_job_secrets(self, monkeypatch):
        monkeypatch.setenv("AI_REVIEW_OPENCODE_TOKEN", "oc-key")
        monkeypatch.setenv("AI_REVIEW_GITHUB_TOKEN", "gh")
        monkeypatch.setenv("OPENAI_API_KEY", "sk")
        env = _build_env()
        assert "AI_REVIEW_GITHUB_TOKEN" not in env
        assert env.get("OPENCODE_API_KEY") == "oc-key"


class TestCollectChunk:
    def test_collects_agent_message_chunks(self):
        chunks: list[str] = []
        _collect_chunk(
            {
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "Hello"},
                }
            },
            chunks,
        )
        _collect_chunk(
            {
                "update": {
                    "sessionUpdate": "agent_thought_chunk",
                    "content": {"type": "text", "text": "thinking"},
                }
            },
            chunks,
        )
        assert "".join(chunks) == "Hello"

    def test_transport_is_acp(self):
        assert OpencodeAdapter.transport == "acp"
