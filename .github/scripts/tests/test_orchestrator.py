"""Tests for the orchestrator's adapter-driven code paths.

Uses a fake adapter to exercise:
- Successful run returning pre-parsed list[dict] (ACP-style)
- Successful run returning raw JSON string (argv-style)
- Retry path: first attempt returns bad output, second returns valid JSON
- Retry exhausted: both attempts fail → ValueError raised
- Filter/anchor integration via run_with_retry
- severity_to_event integration (REQUEST_CHANGES vs COMMENT)
- summarize integration with fake comments

No real CLI, GitHub API, or file I/O is required.
"""

from __future__ import annotations

import json
import pytest

from review import (
    extract_json,
    filter_and_anchor,
    run_with_retry,
    severity_to_event,
    summarize,
)


# ---------------------------------------------------------------------------
# Fake adapter helpers
# ---------------------------------------------------------------------------

class FakeAdapter:
    """Configurable fake adapter for testing orchestrator logic."""

    transport = "argv"

    def __init__(self, responses: list):
        """*responses* is a list of per-call return values (or exceptions).

        Each element is either:
        - A ``list[dict]`` → returned directly (ACP-style).
        - A ``str``        → returned as raw argv output (JSON or fenced).
        - An ``Exception`` instance → raised on that call.
        """
        self._responses = list(responses)
        self._call_count = 0

    def ensure_installed(self) -> None:
        pass  # always succeeds in tests

    def run(self, *, prompt: str, diff_path: str, config: dict) -> object:
        if self._call_count >= len(self._responses):
            raise IndexError("FakeAdapter: no more configured responses")
        response = self._responses[self._call_count]
        self._call_count += 1
        if isinstance(response, Exception):
            raise response
        return response

    @property
    def call_count(self) -> int:
        return self._call_count


VALID_COMMENTS = [
    {"path": "src/app.py", "line": 10, "severity": "🔴 error", "body": "Null deref"},
    {"path": "src/app.py", "line": 20, "severity": "⚠️ warning", "body": "Use const"},
]


# ---------------------------------------------------------------------------
# run_with_retry — ACP path (list[dict] returned directly)
# ---------------------------------------------------------------------------

class TestRunWithRetryAcp:
    def test_acp_list_returned_directly(self):
        adapter = FakeAdapter([VALID_COMMENTS])
        result = run_with_retry(adapter, "prompt", "/tmp/diff", {})
        assert result == VALID_COMMENTS
        assert adapter.call_count == 1

    def test_acp_empty_list_accepted(self):
        adapter = FakeAdapter([[]])
        result = run_with_retry(adapter, "prompt", "/tmp/diff", {})
        assert result == []

    def test_acp_does_not_retry_on_success(self):
        adapter = FakeAdapter([VALID_COMMENTS, VALID_COMMENTS])
        run_with_retry(adapter, "prompt", "/tmp/diff", {})
        assert adapter.call_count == 1  # only one call needed


# ---------------------------------------------------------------------------
# run_with_retry — argv path (string returned, parsed by orchestrator)
# ---------------------------------------------------------------------------

class TestRunWithRetryArgv:
    def test_plain_json_string_parsed(self):
        adapter = FakeAdapter([json.dumps(VALID_COMMENTS)])
        result = run_with_retry(adapter, "prompt", "/tmp/diff", {})
        assert result == VALID_COMMENTS

    def test_fenced_json_string_parsed(self):
        fenced = f"```json\n{json.dumps(VALID_COMMENTS)}\n```"
        adapter = FakeAdapter([fenced])
        result = run_with_retry(adapter, "prompt", "/tmp/diff", {})
        assert result == VALID_COMMENTS

    def test_empty_json_array_string_parsed(self):
        adapter = FakeAdapter(["[]"])
        result = run_with_retry(adapter, "prompt", "/tmp/diff", {})
        assert result == []


# ---------------------------------------------------------------------------
# run_with_retry — retry path
# ---------------------------------------------------------------------------

class TestRunWithRetryRetry:
    def test_bad_first_then_good_uses_retry(self):
        adapter = FakeAdapter(["not valid json!", json.dumps(VALID_COMMENTS)])
        result = run_with_retry(adapter, "prompt", "/tmp/diff", {}, max_attempts=2)
        assert result == VALID_COMMENTS
        assert adapter.call_count == 2

    def test_both_attempts_fail_raises_value_error(self):
        adapter = FakeAdapter(["garbage1", "garbage2"])
        with pytest.raises(ValueError, match="Failed to parse"):
            run_with_retry(adapter, "prompt", "/tmp/diff", {}, max_attempts=2)
        assert adapter.call_count == 2

    def test_single_attempt_no_retry_on_parse_fail(self):
        adapter = FakeAdapter(["garbage"])
        with pytest.raises(ValueError):
            run_with_retry(adapter, "prompt", "/tmp/diff", {}, max_attempts=1)
        assert adapter.call_count == 1

    def test_exception_in_adapter_propagates(self):
        err = RuntimeError("CLI crashed")
        adapter = FakeAdapter([err])
        with pytest.raises(RuntimeError, match="CLI crashed"):
            run_with_retry(adapter, "prompt", "/tmp/diff", {})

    def test_exception_on_first_not_retried(self):
        err = RuntimeError("crash")
        adapter = FakeAdapter([err, VALID_COMMENTS])
        with pytest.raises(RuntimeError):
            run_with_retry(adapter, "prompt", "/tmp/diff", {})
        assert adapter.call_count == 1


# ---------------------------------------------------------------------------
# Full fake-adapter pipeline: run → filter → severity → summarize
# ---------------------------------------------------------------------------

SAMPLE_DIFF = """\
diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -9,4 +9,4 @@
 context_line_9
+added_line_10
+added_line_20
 context_line_12
"""


class TestFakeAdapterPipeline:
    def _run(self, responses, diff=SAMPLE_DIFF):
        adapter = FakeAdapter(responses)
        comments = run_with_retry(adapter, "prompt", "/tmp/diff", {})
        anchored, unanchored = filter_and_anchor(comments, diff)
        event = severity_to_event(anchored) if anchored else "COMMENT"
        body = summarize("fake", anchored, unanchored)
        return comments, anchored, unanchored, event, body

    def test_all_anchored_error_requests_changes(self):
        comments = [
            {"path": "src/app.py", "line": 10, "severity": "🔴 error", "body": "bad"},
        ]
        _, anchored, unanchored, event, body = self._run([comments])
        assert len(anchored) == 1
        assert event == "REQUEST_CHANGES"
        assert "AI Code Review (fake)" in body

    def test_all_unanchored_posts_comment(self):
        comments = [
            {"path": "src/app.py", "line": 999, "severity": "🔴 error", "body": "offscreen"},
        ]
        _, anchored, unanchored, event, body = self._run([comments])
        assert len(anchored) == 0
        assert len(unanchored) == 1
        assert event == "COMMENT"
        assert "not anchored" in body.lower()

    def test_empty_adapter_output_clean_summary(self):
        _, anchored, unanchored, event, body = self._run([[]])
        assert anchored == []
        assert unanchored == []
        assert event == "COMMENT"
        assert "No issues found" in body

    def test_warning_only_stays_comment(self):
        comments = [
            {"path": "src/app.py", "line": 10, "severity": "⚠️ warning", "body": "meh"},
        ]
        _, anchored, _, event, _ = self._run([comments])
        assert event == "COMMENT"

    def test_retry_path_delivers_correct_results(self):
        good = [{"path": "src/app.py", "line": 10, "severity": "💡 suggestion", "body": "ok"}]
        _, anchored, _, event, body = self._run(["bad json", json.dumps(good)])
        assert len(anchored) == 1
        assert "💡 suggestion" in body

    def test_body_prefix_matches_agent(self):
        _, _, _, _, body = self._run([[]])
        assert body.startswith("## AI Code Review (fake)")
