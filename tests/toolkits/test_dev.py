"""Tests for DevToolkit and code_intel detection helpers."""

from __future__ import annotations

import json

from ash.clients.code_intel import detect_commit_convention, detect_test_command, read_pr_template
from ash.toolkits.dev import DevToolkit

# ── DevToolkit tool wiring ────────────────────────────────────────────────────


def test_dev_toolkit_exposes_four_tools(tmp_path):
    toolkit = DevToolkit(worktree=tmp_path)
    tools = toolkit.get_tools()
    names = {t.name for t in tools}
    assert names == {"read_file", "list_files", "search_code", "run_command"}


def test_read_file_returns_content(tmp_path):
    (tmp_path / "hello.py").write_text("print('hello')")
    toolkit = DevToolkit(worktree=tmp_path)
    read = next(t for t in toolkit.get_tools() if t.name == "read_file")
    result = read.invoke({"path": "hello.py"})
    assert "print('hello')" in result


def test_read_file_missing_returns_placeholder(tmp_path):
    toolkit = DevToolkit(worktree=tmp_path)
    read = next(t for t in toolkit.get_tools() if t.name == "read_file")
    result = read.invoke({"path": "nonexistent.py"})
    assert "does not exist" in result


def test_read_file_line_range_returns_only_span(tmp_path):
    # F7: line-range reads return only the requested span, line-numbered.
    (tmp_path / "f.py").write_text("\n".join(f"line{i}" for i in range(1, 11)))
    toolkit = DevToolkit(worktree=tmp_path)
    read = next(t for t in toolkit.get_tools() if t.name == "read_file")
    result = read.invoke({"path": "f.py", "start_line": 3, "end_line": 5})
    assert "3: line3" in result and "5: line5" in result
    assert "line1" not in result and "line8" not in result


def test_list_files_matches_glob(tmp_path):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")
    (tmp_path / "c.txt").write_text("")
    toolkit = DevToolkit(worktree=tmp_path)
    lst = next(t for t in toolkit.get_tools() if t.name == "list_files")
    result = lst.invoke({"pattern": "*.py"})
    assert "a.py" in result
    assert "b.py" in result
    assert "c.txt" not in result


def test_run_command_blocked_for_disallowed_cmd(tmp_path):
    toolkit = DevToolkit(worktree=tmp_path, allowed_cmd="python -m pytest")
    run = next(t for t in toolkit.get_tools() if t.name == "run_command")
    result = run.invoke({"cmd": "rm -rf /"})
    assert "[blocked]" in result


def test_run_command_allowed_prefix_executes(tmp_path):
    toolkit = DevToolkit(worktree=tmp_path)
    run = next(t for t in toolkit.get_tools() if t.name == "run_command")
    # ruff is in _ALLOWED_PREFIXES; on CI it's installed
    result = run.invoke({"cmd": "ruff --version"})
    # Should not be blocked — may succeed or fail depending on env, but never "[blocked]"
    assert "[blocked]" not in result


# ── detect_test_command ───────────────────────────────────────────────────────


def test_detect_pytest_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    assert detect_test_command(tmp_path) == "python -m pytest"


def test_detect_npm_test_from_package_json(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    assert detect_test_command(tmp_path) == "npm test"


def test_detect_go_test_from_go_mod(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/foo\n")
    assert detect_test_command(tmp_path) == "go test ./..."


def test_detect_no_test_command_returns_none(tmp_path):
    assert detect_test_command(tmp_path) is None


# ── detect_commit_convention ─────────────────────────────────────────────────


def test_detect_commitlint_config(tmp_path):
    (tmp_path / ".commitlintrc").write_text("{}")
    result = detect_commit_convention(tmp_path)
    assert "Conventional Commits" in result


def test_detect_contributing_mention(tmp_path):
    (tmp_path / "CONTRIBUTING.md").write_text("# Contributing\nUse conventional commits please.")
    result = detect_commit_convention(tmp_path)
    assert result  # non-empty, mentions convention


def test_detect_fallback_is_readable(tmp_path):
    # No config at all → falls back to a helpful generic message
    result = detect_commit_convention(tmp_path)
    assert "imperative" in result.lower() or "commit" in result.lower()


# ── read_pr_template ─────────────────────────────────────────────────────────


def test_read_pr_template_github(tmp_path):
    gh = tmp_path / ".github"
    gh.mkdir()
    tmpl = gh / "PULL_REQUEST_TEMPLATE.md"
    tmpl.write_text("## Description\n## Tests\n")
    result = read_pr_template(tmp_path)
    assert result is not None
    assert "Description" in result


def test_read_pr_template_absent_returns_none(tmp_path):
    assert read_pr_template(tmp_path) is None
