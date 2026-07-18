"""Unit tests for the OpenCode adapter (no live CLI required)."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from adapters.opencode import OpencodeAdapter, _build_env, _require_token


class TestTokenRequired:
    def test_missing_token_raises(self, monkeypatch):
        monkeypatch.delenv("AI_REVIEW_OPENCODE_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="AI_REVIEW_OPENCODE_TOKEN"):
            _require_token()

    def test_ensure_installed_requires_token(self, monkeypatch):
        monkeypatch.delenv("AI_REVIEW_OPENCODE_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="AI_REVIEW_OPENCODE_TOKEN"):
            OpencodeAdapter().ensure_installed()


class TestBuildEnv:
    def test_remaps_to_openai_key(self, monkeypatch):
        monkeypatch.setenv("AI_REVIEW_OPENCODE_TOKEN", "sk-test")
        env = _build_env()
        assert env.get("OPENAI_API_KEY") == "sk-test"
        assert "AI_REVIEW_OPENCODE_TOKEN" not in env

    def test_job_secrets_not_inherited(self, monkeypatch):
        monkeypatch.setenv("AI_REVIEW_OPENCODE_TOKEN", "sk-test")
        monkeypatch.setenv("AI_REVIEW_GITHUB_TOKEN", "gh-secret")
        monkeypatch.setenv("AI_REVIEW_CLAUDE_TOKEN", "claude-secret")
        monkeypatch.setenv("GH_TOKEN", "legacy")
        env = _build_env()
        assert "AI_REVIEW_GITHUB_TOKEN" not in env
        assert "AI_REVIEW_CLAUDE_TOKEN" not in env
        assert "GH_TOKEN" not in env


class TestRunArgv:
    def test_run_uses_list_argv_no_shell(self, monkeypatch):
        monkeypatch.setenv("AI_REVIEW_OPENCODE_TOKEN", "sk-test")
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            assert kwargs.get("shell") in (None, False)
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="[]", stderr=""
            )

        monkeypatch.setattr("adapters.opencode.subprocess.run", fake_run)
        out = OpencodeAdapter().run(
            prompt="review please", diff_path="/tmp/x.diff", config={"timeout": 30}
        )
        assert out == "[]"
        assert calls
        assert calls[0][0] == "opencode"
        assert "run" in calls[0]
        assert "--pure" in calls[0]
        assert "--auto" in calls[0]

    def test_nonzero_exit_raises_without_stderr_in_message(self, monkeypatch):
        monkeypatch.setenv("AI_REVIEW_OPENCODE_TOKEN", "sk-test")

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                args=cmd, returncode=2, stdout="", stderr="SECRET_LEAK_SHOULD_NOT_SURFACE"
            )

        monkeypatch.setattr("adapters.opencode.subprocess.run", fake_run)
        with pytest.raises(RuntimeError, match="exited 2") as ei:
            OpencodeAdapter().run(prompt="p", diff_path="/tmp/x", config={})
        assert "SECRET_LEAK" not in str(ei.value)
