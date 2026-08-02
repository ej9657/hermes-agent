from types import SimpleNamespace

import pytest

from hermes_cli import claude_review


def test_build_review_prompt_includes_real_diff_and_boundary():
    prompt = claude_review.build_review_prompt(
        user_prompt="Check the auth routing.",
        cwd="/tmp/project",
        diff_text="diff --git a/a.py b/a.py\n+print('hi')\n",
        diff_command=["git", "diff", "--no-ext-diff", "HEAD", "--"],
    )

    assert "read-only second-opinion reviewer" in prompt
    assert "Check the auth routing." in prompt
    assert "git diff --no-ext-diff HEAD --" in prompt
    assert "```diff" in prompt
    assert "+print('hi')" in prompt


def test_collect_git_diff_defaults_to_head(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0, stdout="diff text", stderr="")

    monkeypatch.setattr(claude_review.subprocess, "run", fake_run)

    diff, cmd = claude_review.collect_git_diff(tmp_path)

    assert diff == "diff text"
    assert cmd == ["git", "diff", "--no-ext-diff", "HEAD", "--"]
    assert calls[0][0] == cmd
    assert calls[0][1]["cwd"] == str(tmp_path)


def test_collect_git_diff_can_limit_to_staged_paths(monkeypatch, tmp_path):
    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout="staged diff", stderr="")

    monkeypatch.setattr(claude_review.subprocess, "run", fake_run)

    _diff, cmd = claude_review.collect_git_diff(
        tmp_path,
        staged=True,
        paths=["hermes_cli/claude_review.py"],
    )

    assert cmd == [
        "git",
        "diff",
        "--no-ext-diff",
        "--cached",
        "--",
        "hermes_cli/claude_review.py",
    ]


def test_collect_untracked_diff_includes_text_file(monkeypatch, tmp_path):
    new_file = tmp_path / "new_helper.py"
    new_file.write_text("print('new')\n", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout="new_helper.py\n", stderr="")

    monkeypatch.setattr(claude_review.subprocess, "run", fake_run)

    diff, cmd = claude_review.collect_untracked_diff(tmp_path)

    assert cmd == ["git", "ls-files", "--others", "--exclude-standard", "--"]
    assert "diff --git a/new_helper.py b/new_helper.py" in diff
    assert "--- /dev/null" in diff
    assert "+print('new')" in diff


def test_build_prompt_from_args_includes_untracked_files(monkeypatch, tmp_path):
    monkeypatch.setattr(
        claude_review,
        "collect_git_diff",
        lambda *args, **kwargs: (
            "diff --git a/tracked b/tracked\n+tracked\n",
            ["git", "diff", "HEAD", "--"],
        ),
    )
    monkeypatch.setattr(
        claude_review,
        "collect_untracked_diff",
        lambda *args, **kwargs: (
            "diff --git a/new b/new\n+untracked\n",
            ["git", "ls-files", "--others", "--exclude-standard", "--"],
        ),
    )
    args = SimpleNamespace(
        cwd=str(tmp_path),
        prompt_text=[],
        prompt=None,
        prompt_file=None,
        no_diff=False,
        no_untracked=False,
        diff_base="HEAD",
        staged=False,
        path=None,
    )

    prompt = claude_review.build_prompt_from_args(args)

    assert "Diff commands:" in prompt
    assert "git diff HEAD --" in prompt
    assert "git ls-files --others --exclude-standard --" in prompt
    assert "+tracked" in prompt
    assert "+untracked" in prompt


def test_run_claude_review_invokes_claude_cli_with_stdin(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_review, "resolve_claude_command", lambda command: "/bin/claude")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0, stdout="review ok", stderr="")

    monkeypatch.setattr(claude_review.subprocess, "run", fake_run)

    result = claude_review.run_claude_review(
        prompt="review prompt",
        cwd=tmp_path,
        model="claude-opus-4-8",
        permission_mode="plan",
        timeout=12,
    )

    assert result.stdout == "review ok"
    assert calls[0][0] == [
        "/bin/claude",
        "-p",
        "--model",
        "claude-opus-4-8",
        "--permission-mode",
        "plan",
        "--tools",
        "",
        "--output-format",
        "text",
        "--no-session-persistence",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--",
        "-",
    ]
    assert calls[0][1]["input"] == "review prompt"
    assert calls[0][1]["cwd"] == str(tmp_path)
    assert calls[0][1]["timeout"] == 12
    assert "env" in calls[0][1]


def test_claude_child_env_preserves_login_context_and_removes_api_shadowing():
    env = claude_review.claude_child_env(
        {
            "HOME": "/Users/example",
            "PATH": "/usr/local/bin:/usr/bin",
            "ANTHROPIC_API_KEY": "api-key",
            "ANTHROPIC_TOKEN": "token",
            "CLAUDE_CODE_OAUTH_TOKEN": "oauth-token",
            "HERMES_ACTIVE_PROVIDER": "anthropic",
            "HERMES_ANTHROPIC_API_KEY": "api-key",
            "HERMES_CLAUDE_MODEL": "old-model",
            "HERMES_MCP_CONFIG": "custom",
            "HERMES_HOME": "/Users/example/.hermes",
        }
    )

    assert env["HOME"] == "/Users/example"
    assert env["PATH"] == "/usr/local/bin:/usr/bin"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-token"
    assert env["HERMES_HOME"] == "/Users/example/.hermes"
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_TOKEN" not in env
    assert "HERMES_ACTIVE_PROVIDER" not in env
    assert "HERMES_ANTHROPIC_API_KEY" not in env
    assert "HERMES_CLAUDE_MODEL" not in env
    assert "HERMES_MCP_CONFIG" not in env


def test_cmd_claude_review_dry_run_prints_command_and_prompt(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        claude_review,
        "collect_git_diff",
        lambda *args, **kwargs: (
            "diff --git a/x b/x\n+changed\n",
            ["git", "diff", "HEAD", "--"],
        ),
    )
    args = SimpleNamespace(
        cwd=str(tmp_path),
        prompt_text=["extra", "context"],
        prompt=None,
        prompt_file=None,
        no_diff=False,
        diff_base="HEAD",
        staged=False,
        path=None,
        no_untracked=True,
        dry_run=True,
        command="claude",
        model="claude-opus-4-8",
        permission_mode="plan",
        allow_mcp=False,
    )

    claude_review.cmd_claude_review(args)

    out = capsys.readouterr().out
    assert "claude -p --model claude-opus-4-8 --permission-mode plan" in out
    assert "extra context" in out
    assert "+changed" in out


def test_cmd_claude_review_writes_output_file(monkeypatch, tmp_path):
    output_file = tmp_path / "review.txt"
    monkeypatch.setattr(claude_review, "build_prompt_from_args", lambda args: "prompt")
    monkeypatch.setattr(
        claude_review,
        "run_claude_review",
        lambda **kwargs: claude_review.ClaudeReviewResult(
            command=["claude"],
            prompt="prompt",
            returncode=0,
            stdout="review body",
            stderr="",
        ),
    )
    args = SimpleNamespace(
        cwd=str(tmp_path),
        command="claude",
        model="claude-opus-4-8",
        permission_mode="plan",
        timeout=30,
        output_file=str(output_file),
        dry_run=False,
        allow_mcp=False,
    )

    claude_review.cmd_claude_review(args)

    assert output_file.read_text(encoding="utf-8") == "review body"


def test_resolve_claude_command_errors_when_missing(monkeypatch):
    monkeypatch.setattr(claude_review.shutil, "which", lambda command: None)

    with pytest.raises(FileNotFoundError):
        claude_review.resolve_claude_command("claude-missing")
