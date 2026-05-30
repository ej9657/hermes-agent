"""Claude Code CLI review helper.

This module intentionally shells out to the local ``claude`` CLI instead of
using Hermes' ``provider: anthropic`` adapter. That keeps Claude review work on
the user's Claude Code / Claude Max subscription lane rather than requiring an
Anthropic API key.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


DEFAULT_MODEL = "claude-opus-5"
DEFAULT_PERMISSION_MODE = "plan"
DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_UNTRACKED_FILE_LIMIT_BYTES = 200_000

DEFAULT_REVIEW_INSTRUCTIONS = """Review the attached Codex/Hermes work as a read-only second-opinion reviewer.

Focus on:
- correctness bugs and behavioral regressions
- source-of-truth or architecture boundary problems
- missing or weak tests
- risky assumptions, especially around provider/auth routing

Do not edit files. Return concise findings first, ordered by severity, with file/function references when possible. If there are no blocking issues, say that clearly and call out any residual test gaps."""

_CLAUDE_CHILD_ENV_DROP_EXACT = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_TOKEN",
    "HERMES_ACTIVE_PROVIDER",
    "HERMES_ACTIVE_MODEL",
    "PYTHONHOME",
    "PYTHONPATH",
}

_CLAUDE_CHILD_ENV_DROP_PREFIXES = (
    "HERMES_ANTHROPIC",
    "HERMES_CLAUDE",
    "HERMES_MCP",
)


@dataclass(frozen=True)
class ClaudeReviewResult:
    command: list[str]
    prompt: str
    returncode: int
    stdout: str
    stderr: str


def claude_child_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Return an env for the Claude CLI that preserves login but avoids API shadowing."""
    child_env = dict(os.environ if environ is None else environ)
    for key in list(child_env):
        if key in _CLAUDE_CHILD_ENV_DROP_EXACT or key.startswith(
            _CLAUDE_CHILD_ENV_DROP_PREFIXES
        ):
            child_env.pop(key, None)
    return child_env


def resolve_claude_command(command: str = "claude") -> str:
    """Resolve the Claude Code CLI command or raise a clear error."""
    raw = (command or "claude").strip()
    if not raw:
        raw = "claude"
    if "/" in raw:
        path = Path(raw).expanduser()
        if path.exists():
            return str(path)
        raise FileNotFoundError(f"Claude CLI command not found: {raw}")
    resolved = shutil.which(raw)
    if not resolved:
        raise FileNotFoundError(
            f"Claude CLI command '{raw}' was not found. Install/login Claude Code, "
            "or pass --command /path/to/claude."
        )
    return resolved


def collect_git_diff(
    cwd: str | Path,
    *,
    diff_base: str = "HEAD",
    staged: bool = False,
    paths: Sequence[str] | None = None,
) -> tuple[str, list[str]]:
    """Return the current git diff and the command used to collect it."""
    cwd_path = Path(cwd).expanduser()
    cmd = ["git", "diff", "--no-ext-diff"]
    if staged:
        cmd.append("--cached")
    elif diff_base:
        cmd.append(diff_base)
    cmd.append("--")
    cmd.extend(paths or [])

    proc = subprocess.run(
        cmd,
        cwd=str(cwd_path),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "git diff failed").strip())
    return proc.stdout, cmd


def collect_untracked_diff(
    cwd: str | Path,
    *,
    paths: Sequence[str] | None = None,
    max_file_bytes: int = DEFAULT_UNTRACKED_FILE_LIMIT_BYTES,
) -> tuple[str, list[str]]:
    """Return a review-friendly pseudo diff for untracked text files."""
    cwd_path = Path(cwd).expanduser()
    cmd = ["git", "ls-files", "--others", "--exclude-standard", "--"]
    cmd.extend(paths or [])
    proc = subprocess.run(
        cmd,
        cwd=str(cwd_path),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "git ls-files failed").strip())

    chunks: list[str] = []
    for rel_path in [line for line in proc.stdout.splitlines() if line.strip()]:
        file_path = cwd_path / rel_path
        if not file_path.is_file():
            continue
        size = file_path.stat().st_size
        header = [
            f"diff --git a/{rel_path} b/{rel_path}",
            "new file mode 100644",
            "--- /dev/null",
            f"+++ b/{rel_path}",
        ]
        if size > max_file_bytes:
            chunks.append(
                "\n".join(header + [f"@@ skipped: file is {size} bytes @@"])
            )
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            chunks.append(
                "\n".join(header + ["@@ skipped: binary or non-UTF-8 file @@"])
            )
            continue
        added_lines = [f"+{line}" for line in text.splitlines()]
        if text.endswith("\n") or added_lines:
            chunks.append("\n".join(header + ["@@"] + added_lines))
        else:
            chunks.append("\n".join(header + ["@@"]))
    return ("\n\n".join(chunks) + ("\n" if chunks else "")), cmd


def _read_prompt_file(path: str | None) -> str:
    if not path:
        return ""
    return Path(path).expanduser().read_text(encoding="utf-8")


def build_review_prompt(
    *,
    instructions: str = DEFAULT_REVIEW_INSTRUCTIONS,
    user_prompt: str = "",
    cwd: str | Path,
    diff_text: str = "",
    diff_command: Sequence[str] | None = None,
    extra_diff_commands: Sequence[Sequence[str]] | None = None,
) -> str:
    """Build the prompt passed to Claude Code."""
    cwd_text = str(Path(cwd).expanduser())
    sections = [
        instructions.strip(),
        "",
        f"Workspace: {cwd_text}",
    ]
    if user_prompt.strip():
        sections.extend(["", "Additional review request:", user_prompt.strip()])
    diff_commands = []
    if diff_command is not None:
        diff_commands.append(diff_command)
    diff_commands.extend(extra_diff_commands or [])
    if diff_commands:
        sections.extend(
            ["", "Diff command:" if len(diff_commands) == 1 else "Diff commands:"]
        )
        sections.extend(
            " ".join(shlex.quote(part) for part in command)
            for command in diff_commands
        )
    if diff_text.strip():
        sections.extend(["", "Diff to review:", "```diff", diff_text.rstrip(), "```"])
    else:
        sections.extend(["", "Diff to review: (none found or --no-diff was used)"])
    return "\n".join(sections).rstrip() + "\n"


def run_claude_review(
    *,
    prompt: str,
    cwd: str | Path,
    command: str = "claude",
    model: str = DEFAULT_MODEL,
    permission_mode: str = DEFAULT_PERMISSION_MODE,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    strict_mcp: bool = True,
) -> ClaudeReviewResult:
    """Run Claude Code in non-interactive print mode using stdin for the prompt."""
    claude = resolve_claude_command(command)
    cmd = [
        claude,
        "-p",
        "--model",
        model,
        "--permission-mode",
        permission_mode,
        "--output-format",
        "text",
        "--no-session-persistence",
    ]
    if strict_mcp:
        cmd.extend(["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'])
    cmd.extend([
        "--",
        "-",
    ])
    proc = subprocess.run(
        cmd,
        input=prompt,
        cwd=str(Path(cwd).expanduser()),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        env=claude_child_env(),
    )
    return ClaudeReviewResult(
        command=cmd,
        prompt=prompt,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def build_prompt_from_args(args) -> str:
    """Compose a Claude review prompt from argparse args."""
    cwd = Path(getattr(args, "cwd", None) or ".").expanduser()
    positional_prompt = " ".join(getattr(args, "prompt_text", []) or [])
    explicit_prompt = getattr(args, "prompt", "") or ""
    prompt_file_text = _read_prompt_file(getattr(args, "prompt_file", None))
    user_prompt = "\n\n".join(
        part.strip()
        for part in [prompt_file_text, explicit_prompt, positional_prompt]
        if part and part.strip()
    )

    diff_text = ""
    diff_command = None
    extra_diff_commands: list[list[str]] = []
    if not getattr(args, "no_diff", False):
        diff_text, diff_command = collect_git_diff(
            cwd,
            diff_base=getattr(args, "diff_base", "HEAD"),
            staged=bool(getattr(args, "staged", False)),
            paths=getattr(args, "path", None) or None,
        )
        if not bool(getattr(args, "staged", False)) and not bool(
            getattr(args, "no_untracked", False)
        ):
            untracked_diff, untracked_command = collect_untracked_diff(
                cwd,
                paths=getattr(args, "path", None) or None,
            )
            if untracked_diff.strip():
                diff_text = (
                    "\n\n".join(
                        part.rstrip()
                        for part in [diff_text, untracked_diff]
                        if part.strip()
                    )
                    + "\n"
                )
                extra_diff_commands.append(untracked_command)
    return build_review_prompt(
        user_prompt=user_prompt,
        cwd=cwd,
        diff_text=diff_text,
        diff_command=diff_command,
        extra_diff_commands=extra_diff_commands,
    )


def cmd_claude_review(args) -> None:
    """CLI entrypoint for ``hermes claude-review``."""
    cwd = Path(getattr(args, "cwd", None) or ".").expanduser()
    prompt = build_prompt_from_args(args)

    if getattr(args, "dry_run", False):
        command = [
            getattr(args, "command", "claude"),
            "-p",
            "--model",
            getattr(args, "model", DEFAULT_MODEL),
            "--permission-mode",
            getattr(args, "permission_mode", DEFAULT_PERMISSION_MODE),
            "--output-format",
            "text",
            "--no-session-persistence",
        ]
        if not getattr(args, "allow_mcp", False):
            command.extend(
                ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
            )
        command.extend(["--", "-"])
        print("Command:")
        print(" ".join(shlex.quote(part) for part in command))
        print("\nPrompt:")
        print(prompt, end="")
        return

    try:
        result = run_claude_review(
            prompt=prompt,
            cwd=cwd,
            command=getattr(args, "command", "claude"),
            model=getattr(args, "model", DEFAULT_MODEL),
            permission_mode=getattr(args, "permission_mode", DEFAULT_PERMISSION_MODE),
            timeout=int(getattr(args, "timeout", DEFAULT_TIMEOUT_SECONDS)),
            strict_mcp=not bool(getattr(args, "allow_mcp", False)),
        )
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(127)
    except subprocess.TimeoutExpired:
        print("Error: Claude review timed out.", file=sys.stderr)
        raise SystemExit(124)

    output_file = getattr(args, "output_file", None)
    if output_file:
        Path(output_file).expanduser().write_text(result.stdout, encoding="utf-8")
    else:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode:
        raise SystemExit(result.returncode)


def register_parser(subparsers) -> None:
    """Register the Claude CLI review helper under the top-level parser."""
    parser = subparsers.add_parser(
        "claude-review",
        aliases=["codex-review"],
        help="Run a read-only Claude Code CLI review of the current diff",
        description=(
            "Run Claude Code as a local subscription-backed review helper. "
            "This shells out to `claude -p` and does not use Hermes' "
            "Anthropic API provider."
        ),
    )
    parser.add_argument(
        "prompt_text",
        nargs="*",
        help="Optional extra review instructions.",
    )
    parser.add_argument("--prompt", help="Additional review instructions.")
    parser.add_argument(
        "--prompt-file",
        help="Read additional instructions from a file.",
    )
    parser.add_argument(
        "--cwd",
        default=".",
        help="Workspace to review (default: current directory).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Claude model to use (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--permission-mode",
        default=DEFAULT_PERMISSION_MODE,
        help=f"Claude permission mode (default: {DEFAULT_PERMISSION_MODE}).",
    )
    parser.add_argument(
        "--command",
        default="claude",
        help="Claude CLI command or absolute path (default: claude).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    parser.add_argument(
        "--diff-base",
        default="HEAD",
        help="Base ref for git diff (default: HEAD). Ignored with --staged.",
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Review only staged changes using git diff --cached.",
    )
    parser.add_argument(
        "--path",
        action="append",
        help="Restrict git diff to a path. Repeat for multiple paths.",
    )
    parser.add_argument(
        "--no-diff",
        action="store_true",
        help="Do not attach git diff; review only the supplied prompt.",
    )
    parser.add_argument(
        "--no-untracked",
        action="store_true",
        help="Do not include untracked text files in the review diff.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the Claude command and generated prompt without running it.",
    )
    parser.add_argument(
        "--allow-mcp",
        action="store_true",
        help=(
            "Allow Claude Code to load configured MCP servers. By default this "
            "helper passes an empty strict MCP config so diff reviews stay fast "
            "and subscription-backed without project MCP startup side effects."
        ),
    )
    parser.add_argument(
        "--output-file",
        help="Write Claude's review output to a file.",
    )
    parser.set_defaults(func=cmd_claude_review)
