"""Tests for pure helper functions in review.py.

Covers: extract_json, parse_diff_anchors, filter_and_anchor,
        severity_to_event, summarize.
"""

from __future__ import annotations

import json
import pytest

from review import (
    extract_json,
    filter_and_anchor,
    parse_diff_anchors,
    severity_to_event,
    summarize,
)

# ---------------------------------------------------------------------------
# extract_json
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_plain_json_array(self):
        data = [{"path": "a.py", "line": 1, "severity": "🔴 error", "body": "oops"}]
        assert extract_json(json.dumps(data)) == data

    def test_fenced_json_block(self):
        text = '```json\n[{"path": "f.py", "line": 5, "severity": "⚠️ warning", "body": "x"}]\n```'
        result = extract_json(text)
        assert result[0]["path"] == "f.py"

    def test_generic_fence_block(self):
        text = '```\n[{"path": "g.py", "line": 3, "severity": "💡 suggestion", "body": "y"}]\n```'
        result = extract_json(text)
        assert result[0]["line"] == 3

    def test_empty_array(self):
        assert extract_json("[]") == []

    def test_no_json_raises(self):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            extract_json("This is plain text with no JSON.")

    def test_whitespace_stripped(self):
        assert extract_json("  []  ") == []

    def test_plain_json_takes_priority_over_fence(self):
        # Plain JSON is tried first; even if a fence marker appears in body text
        data = [{"path": "x.py", "line": 1, "severity": "💡 suggestion", "body": "```use this```"}]
        raw = json.dumps(data)
        assert extract_json(raw) == data


# ---------------------------------------------------------------------------
# parse_diff_anchors
# ---------------------------------------------------------------------------

SAMPLE_DIFF = """\
diff --git a/src/main.py b/src/main.py
index abc..def 100644
--- a/src/main.py
+++ b/src/main.py
@@ -10,4 +10,6 @@
 def foo():
+    x = 1
+    y = 2
     return x
 
diff --git a/README.md b/README.md
index 111..222 100644
--- a/README.md
+++ b/README.md
@@ -1,2 +1,3 @@
 # Title
+New line here
 existing
"""


class TestParseDiffAnchors:
    def test_added_lines_are_anchored(self):
        anchors = parse_diff_anchors(SAMPLE_DIFF)
        # @@ -10,4 +10,6 @@ — first added line is at new_line 11 (context "def foo():" is 10)
        assert ("src/main.py", 11) in anchors
        assert ("src/main.py", 12) in anchors

    def test_readme_added_line_anchored(self):
        anchors = parse_diff_anchors(SAMPLE_DIFF)
        # @@ -1,2 +1,3 @@ context "# Title" is line 1, added line is 2
        assert ("README.md", 2) in anchors

    def test_context_lines_not_anchored(self):
        anchors = parse_diff_anchors(SAMPLE_DIFF)
        # "def foo():" is a context line at new_line 10
        assert ("src/main.py", 10) not in anchors

    def test_empty_diff_returns_empty_set(self):
        assert parse_diff_anchors("") == set()

    def test_deletion_only_diff_no_anchors(self):
        diff = (
            "diff --git a/f.py b/f.py\n"
            "--- a/f.py\n"
            "+++ b/f.py\n"
            "@@ -1,2 +1,1 @@\n"
            "-removed line\n"
            " context\n"
        )
        anchors = parse_diff_anchors(diff)
        assert ("f.py", 1) not in anchors  # only deletion, no added lines

    def test_multiple_hunks(self):
        diff = (
            "diff --git a/app.py b/app.py\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -1,3 +1,4 @@\n"
            " line1\n"
            "+added_early\n"
            " line2\n"
            " line3\n"
            "@@ -20,2 +21,3 @@\n"
            " line20\n"
            "+added_late\n"
            " line21\n"
        )
        anchors = parse_diff_anchors(diff)
        assert ("app.py", 2) in anchors   # added_early at new line 2
        assert ("app.py", 22) in anchors  # added_late at new line 22


# ---------------------------------------------------------------------------
# filter_and_anchor
# ---------------------------------------------------------------------------

class TestFilterAndAnchor:
    def test_anchored_comment_kept(self):
        comments = [{"path": "src/main.py", "line": 11, "severity": "🔴 error", "body": "bad"}]
        anchored, unanchored = filter_and_anchor(comments, SAMPLE_DIFF)
        assert len(anchored) == 1
        assert len(unanchored) == 0

    def test_unanchored_comment_dropped(self):
        comments = [{"path": "src/main.py", "line": 999, "severity": "⚠️ warning", "body": "nope"}]
        anchored, unanchored = filter_and_anchor(comments, SAMPLE_DIFF)
        assert len(anchored) == 0
        assert len(unanchored) == 1

    def test_mixed_comments_split_correctly(self):
        comments = [
            {"path": "src/main.py", "line": 11, "severity": "🔴 error", "body": "bad"},
            {"path": "src/main.py", "line": 500, "severity": "💡 suggestion", "body": "nope"},
        ]
        anchored, unanchored = filter_and_anchor(comments, SAMPLE_DIFF)
        assert len(anchored) == 1
        assert len(unanchored) == 1

    def test_empty_comments_returns_empty_lists(self):
        a, u = filter_and_anchor([], SAMPLE_DIFF)
        assert a == []
        assert u == []

    def test_wrong_file_path_is_unanchored(self):
        comments = [{"path": "nonexistent.py", "line": 11, "severity": "⚠️ warning", "body": "x"}]
        anchored, unanchored = filter_and_anchor(comments, SAMPLE_DIFF)
        assert len(unanchored) == 1


# ---------------------------------------------------------------------------
# severity_to_event
# ---------------------------------------------------------------------------

class TestSeverityToEvent:
    def test_error_comment_requests_changes(self):
        comments = [{"severity": "🔴 error", "body": "bad"}]
        assert severity_to_event(comments) == "REQUEST_CHANGES"

    def test_warning_only_is_comment(self):
        comments = [{"severity": "⚠️ warning", "body": "meh"}]
        assert severity_to_event(comments) == "COMMENT"

    def test_suggestion_only_is_comment(self):
        comments = [{"severity": "💡 suggestion", "body": "maybe"}]
        assert severity_to_event(comments) == "COMMENT"

    def test_mixed_with_error_requests_changes(self):
        comments = [
            {"severity": "💡 suggestion", "body": "a"},
            {"severity": "🔴 error", "body": "b"},
        ]
        assert severity_to_event(comments) == "REQUEST_CHANGES"

    def test_empty_list_is_comment(self):
        assert severity_to_event([]) == "COMMENT"

    def test_missing_severity_key_is_comment(self):
        comments = [{"body": "no severity key"}]
        assert severity_to_event(comments) == "COMMENT"


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------

class TestSummarize:
    def test_prefix_includes_agent(self):
        body = summarize("claude", [], [])
        assert "AI Code Review (claude)" in body

    def test_no_issues_message(self):
        body = summarize("opencode", [], [])
        assert "No issues found" in body

    def test_anchored_comment_appears_in_body(self):
        comments = [{"path": "x.py", "line": 5, "severity": "🔴 error", "body": "crash risk"}]
        body = summarize("claude", comments)
        assert "x.py:5" in body
        assert "crash risk" in body

    def test_unanchored_section_appears(self):
        unanchored = [{"path": "z.py", "line": 99, "severity": "⚠️ warning", "body": "offscreen"}]
        body = summarize("claude", [], unanchored)
        assert "not anchored" in body.lower()
        assert "z.py:99" in body

    def test_severity_grouping(self):
        comments = [
            {"path": "a.py", "line": 1, "severity": "🔴 error", "body": "err"},
            {"path": "b.py", "line": 2, "severity": "💡 suggestion", "body": "sug"},
        ]
        body = summarize("cursor", comments)
        assert "🔴 error" in body
        assert "💡 suggestion" in body
        # error section should appear before suggestion
        assert body.index("🔴 error") < body.index("💡 suggestion")

    def test_none_unanchored_treated_as_empty(self):
        comments = [{"path": "x.py", "line": 1, "severity": "⚠️ warning", "body": "w"}]
        body_a = summarize("claude", comments, None)
        body_b = summarize("claude", comments, [])
        assert body_a == body_b
