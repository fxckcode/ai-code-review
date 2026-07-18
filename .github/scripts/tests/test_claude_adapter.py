"""Claude adapter — unit tests (always run) + optional CLI integration tests.

Unit tests verify:
- transport == "argv"
- Missing AI_REVIEW_CLAUDE_TOKEN → RuntimeError (from both ensure_installed and run)
- _build_env removes the source key and adds ANTHROPIC_API_KEY
- _tail helper produces correct length
- _log_output does not receive token values (structural: token only in env)
- no shell=True in subprocess invocation (code-path inspection)
- 5-minute timeout is forwarded from config

Integration tests (auto-skipped when Claude CLI absent or token unset):
- ensure_installed() completes without error
- run() returns a non-empty string
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


# ---------------------------------------------------------------------------
# Skip guard for integration tests
# ---------------------------------------------------------------------------

def _claude_cli_present() -> bool:
    """Return True only if ``claude --version`` succeeds."""
    try:
        subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return True
    except Exception:
        return False


def _token_present() -> bool:
    return bool(os.environ.get("AI_REVIEW_CLAUDE_TOKEN", "").strip())


_SKIP_INTEGRATION = pytest.mark.skipif(
    not (_claude_cli_present() and _token_present()),
    reason=(
        "Integration tests skipped — Claude CLI not installed or "
        "AI_REVIEW_CLAUDE_TOKEN not set"
    ),
)


# ---------------------------------------------------------------------------
# Unit tests — no CLI or token required
# ---------------------------------------------------------------------------

class TestClaudeAdapterContract:
    """Verify the adapter satisfies the base Adapter contract."""

    def test_transport_is_argv(self):
        from adapters.claude import ClaudeAdapter
        assert ClaudeAdapter.transport == "argv"

    def test_is_subclass_of_adapter(self):
        from adapters.base import Adapter
        from adapters.claude import ClaudeAdapter
        assert issubclass(ClaudeAdapter, Adapter)


class TestMissingToken:
    """Missing AI_REVIEW_CLAUDE_TOKEN must raise RuntimeError (job red)."""

    def test_ensure_installed_raises_on_missing_token(self, monkeypatch):
        monkeypatch.delenv("AI_REVIEW_CLAUDE_TOKEN", raising=False)
        from adapters.claude import ClaudeAdapter
        adapter = ClaudeAdapter()
        with pytest.raises(RuntimeError, match="AI_REVIEW_CLAUDE_TOKEN"):
            adapter.ensure_installed()

    def test_run_raises_on_missing_token(self, monkeypatch):
        monkeypatch.delenv("AI_REVIEW_CLAUDE_TOKEN", raising=False)
        from adapters.claude import ClaudeAdapter
        adapter = ClaudeAdapter()
        with pytest.raises(RuntimeError, match="AI_REVIEW_CLAUDE_TOKEN"):
            adapter.run(prompt="test", diff_path="/tmp/d.diff", config={})

    def test_require_token_raises_on_empty_string(self, monkeypatch):
        monkeypatch.setenv("AI_REVIEW_CLAUDE_TOKEN", "   ")
        from adapters.claude import _require_token
        with pytest.raises(RuntimeError, match="AI_REVIEW_CLAUDE_TOKEN"):
            _require_token()


class TestAuthEnvRemap:
    """Secrets are remapped correctly and source key is scrubbed (threat 3.4)."""

    def test_token_remapped_to_anthropic_key(self, monkeypatch):
        monkeypatch.setenv("AI_REVIEW_CLAUDE_TOKEN", "tok-abc123")
        from adapters.claude import _build_env
        env = _build_env()
        assert env.get("ANTHROPIC_API_KEY") == "tok-abc123"

    def test_source_key_absent_from_child_env(self, monkeypatch):
        monkeypatch.setenv("AI_REVIEW_CLAUDE_TOKEN", "tok-abc123")
        from adapters.claude import _build_env
        env = _build_env()
        assert "AI_REVIEW_CLAUDE_TOKEN" not in env

    def test_token_value_not_in_env_var_names(self, monkeypatch):
        """Token value must not accidentally leak into env-var names."""
        monkeypatch.setenv("AI_REVIEW_CLAUDE_TOKEN", "super-secret-value")
        from adapters.claude import _build_env
        env = _build_env()
        for key in env:
            assert "super-secret-value" not in key

    def test_nonessential_traffic_disabled(self, monkeypatch):
        monkeypatch.setenv("AI_REVIEW_CLAUDE_TOKEN", "tok")
        monkeypatch.delenv("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", raising=False)
        from adapters.claude import _build_env
        env = _build_env()
        assert env.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC") == "1"

    def test_existing_disable_flag_preserved(self, monkeypatch):
        monkeypatch.setenv("AI_REVIEW_CLAUDE_TOKEN", "tok")
        monkeypatch.setenv("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "0")
        from adapters.claude import _build_env
        env = _build_env()
        # setdefault must not overwrite an existing value
        assert env.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC") == "0"

    def test_job_secrets_not_inherited(self, monkeypatch):
        monkeypatch.setenv("AI_REVIEW_CLAUDE_TOKEN", "tok-abc")
        monkeypatch.setenv("AI_REVIEW_GITHUB_TOKEN", "gh-secret")
        monkeypatch.setenv("AI_REVIEW_OPENCODE_TOKEN", "oc-secret")
        monkeypatch.setenv("GH_TOKEN", "legacy-gh")
        from adapters.claude import _build_env
        env = _build_env()
        assert "AI_REVIEW_GITHUB_TOKEN" not in env
        assert "AI_REVIEW_OPENCODE_TOKEN" not in env
        assert "AI_REVIEW_CLAUDE_TOKEN" not in env
        assert "GH_TOKEN" not in env
        assert env.get("ANTHROPIC_API_KEY") == "tok-abc"


class TestTailHelper:
    """_tail truncates correctly (backing threat check: output log limited)."""

    def test_short_string_unchanged(self):
        from adapters.claude import _tail
        assert _tail("hello", 100) == "hello"

    def test_long_string_truncated_to_n_chars(self):
        from adapters.claude import _tail
        text = "x" * 10_000
        result = _tail(text, 8192)
        assert len(result) == 8192

    def test_exact_length_unchanged(self):
        from adapters.claude import _tail
        text = "a" * 8192
        assert _tail(text, 8192) == text

    def test_empty_string(self):
        from adapters.claude import _tail
        assert _tail("", 8192) == ""


class TestSubprocessSafety:
    """Ensure subprocess is called with a list (no shell=True — threat 3.4)."""

    def test_run_uses_list_argv_no_shell(self, monkeypatch, tmp_path):
        """Intercept subprocess.run and assert shell=True is never passed."""
        monkeypatch.setenv("AI_REVIEW_CLAUDE_TOKEN", "tok-test")

        captured: list[dict] = []

        def fake_run(cmd, **kwargs):
            captured.append({"cmd": cmd, "kwargs": kwargs})
            # Simulate a clean exit so run() doesn't raise on returncode
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout='[{"path":"a.py","line":1,"severity":"💡 suggestion","body":"ok"}]',
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        from adapters.claude import ClaudeAdapter
        adapter = ClaudeAdapter()
        diff_file = tmp_path / "t.diff"
        diff_file.write_text("diff content")

        adapter.run(prompt="test prompt", diff_path=str(diff_file), config={})

        assert captured, "subprocess.run was never called"
        call = captured[-1]
        # Must be a list, never a string (which would require shell=True to parse)
        assert isinstance(call["cmd"], list), "cmd must be a list, not a shell string"
        assert call["kwargs"].get("shell", False) is False, "shell=True must never be set"

    def test_ensure_installed_uses_list_argv_no_shell(self, monkeypatch):
        monkeypatch.setenv("AI_REVIEW_CLAUDE_TOKEN", "tok-test")

        captured: list[dict] = []

        def fake_run(cmd, **kwargs):
            captured.append({"cmd": cmd, "kwargs": kwargs})
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        from adapters.claude import ClaudeAdapter
        ClaudeAdapter().ensure_installed()

        assert captured
        call = captured[-1]
        assert isinstance(call["cmd"], list)
        assert call["kwargs"].get("shell", False) is False


class TestTimeoutForwarding:
    """5-minute timeout is forwarded from orchestrator config (task 3.2)."""

    def test_timeout_from_config_used(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AI_REVIEW_CLAUDE_TOKEN", "tok")

        captured: list[dict] = []

        def fake_run(cmd, **kwargs):
            captured.append(kwargs)
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="[]", stderr=""
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        from adapters.claude import ClaudeAdapter
        diff_file = tmp_path / "d.diff"
        diff_file.write_text("")
        ClaudeAdapter().run(prompt="p", diff_path=str(diff_file), config={"timeout": 42})

        assert captured
        assert captured[-1].get("timeout") == 42

    def test_default_timeout_is_300(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AI_REVIEW_CLAUDE_TOKEN", "tok")

        captured: list[dict] = []

        def fake_run(cmd, **kwargs):
            captured.append(kwargs)
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="[]", stderr=""
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        from adapters.claude import ClaudeAdapter
        diff_file = tmp_path / "d.diff"
        diff_file.write_text("")
        ClaudeAdapter().run(prompt="p", diff_path=str(diff_file), config={})

        assert captured[-1].get("timeout") == 300


class TestLogOutputBehavior:
    """_log_output writes tail-8KB by default, full output in debug mode."""

    def test_normal_mode_logs_at_most_8kb(self, capsys):
        from adapters.claude import _log_output
        # Use 'z' — absent from the format-string prefix so counting is exact.
        big = "z" * 20_000
        _log_output("", big, debug=False)
        captured = capsys.readouterr()
        z_count = captured.err.count("z")
        assert z_count <= 8192

    def test_debug_mode_logs_full_output(self, capsys):
        from adapters.claude import _log_output
        big = "b" * 20_000
        _log_output(big, "", debug=True)
        captured = capsys.readouterr()
        assert "b" * 100 in captured.err  # representative sample present

    def test_empty_output_produces_no_log(self, capsys):
        from adapters.claude import _log_output
        _log_output("", "", debug=False)
        captured = capsys.readouterr()
        assert captured.err == ""


# ---------------------------------------------------------------------------
# Integration tests — auto-skipped when CLI absent or token unset
# ---------------------------------------------------------------------------

class TestClaudeAdapterIntegration:
    @_SKIP_INTEGRATION
    def test_ensure_installed_completes(self):
        from adapters.claude import ClaudeAdapter
        ClaudeAdapter().ensure_installed()

    @_SKIP_INTEGRATION
    def test_run_returns_nonempty_string(self, tmp_path):
        from adapters.claude import ClaudeAdapter
        adapter = ClaudeAdapter()
        diff_file = tmp_path / "test.diff"
        diff_file.write_text(
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n+++ b/foo.py\n"
            "@@ -1 +1 @@\n-x = 0\n+x = 1\n"
        )
        result = adapter.run(
            prompt=(
                "Return exactly [] as a JSON array with absolutely no other text. "
                "This is an automated test."
            ),
            diff_path=str(diff_file),
            config={"debug": False},
        )
        assert isinstance(result, str)
        assert len(result.strip()) > 0
