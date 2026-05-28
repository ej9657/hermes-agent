import json

from hermes_cli.commands import GATEWAY_KNOWN_COMMANDS, resolve_command
from hermes_cli import telegram_context as tc


def _payload() -> str:
    return json.dumps(
        {
            "filters": {"chat": "ASON Video Studio", "category": "", "since": "", "limit": 2},
            "turns": [
                {
                    "chat_name": "ASON Video Studio",
                    "inbound_at_text": "2026-05-26 17:10:34",
                    "message_text": "The link I provided was a creative reference.",
                }
            ],
            "tasks": [
                {
                    "source_at_text": "2026-05-23 15:32:16",
                    "chat_name": "ASON Video Studio",
                    "category": "ason_video",
                    "status": "open",
                    "task_text": "Let's do one more audit on the video.",
                }
            ],
        }
    )


def test_tctx_command_registered_for_gateway():
    assert resolve_command("tctx").name == "tctx"
    assert resolve_command("telegram-context").name == "tctx"
    assert resolve_command("tgctx").name == "tctx"
    assert "tctx" in GATEWAY_KNOWN_COMMANDS
    assert "telegram-context" in GATEWAY_KNOWN_COMMANDS


def test_resolve_route_for_new_dedicated_lanes():
    video = tc.resolve_route("ASON Video Studio")
    assert video is not None
    assert video.obsidian_route == "05 Video Production/ASON Video Studio.md"

    prompts = tc.resolve_route("-5214343030")
    assert prompts is not None
    assert prompts.obsidian_route == "01 ASON/Visual Prompt Lab.md"

    artifacts = tc.resolve_route("artifact_repository")
    assert artifacts is not None
    assert artifacts.obsidian_route == "03 Products/Artifact Repository.md"


def test_brief_uses_default_telegram_chat(monkeypatch):
    calls = []

    def fake_run(args, *, timeout=30.0):
        calls.append(args)
        return _payload()

    monkeypatch.setattr(tc, "_run_organizer", fake_run)
    output = tc.run_tctx(
        "brief --limit 2",
        default_chat_id="-5282098406",
        default_chat_name="ASON Video Studio",
    )

    assert "--chat" in calls[0]
    assert "-5282098406" in calls[0]
    assert "05 Video Production/ASON Video Studio.md" in output
    assert "creative reference" in output


def test_tasks_can_filter_by_category(monkeypatch):
    calls = []

    def fake_run(args, *, timeout=30.0):
        calls.append(args)
        return _payload()

    monkeypatch.setattr(tc, "_run_organizer", fake_run)
    output = tc.run_tctx("tasks ason_video --limit 2")

    assert "--category" in calls[0]
    assert "ason_video" in calls[0]
    assert "Telegram Task Candidates" in output
    assert "one more audit" in output


def test_recent_renders_markdown_export_with_route(monkeypatch):
    def fake_run(args, *, timeout=30.0):
        assert "--chat" in args
        assert "Artifact Repository" in args
        return "# Telegram Context Export\n\n- Turns: 1\n"

    monkeypatch.setattr(tc, "_run_organizer", fake_run)
    output = tc.run_tctx("recent Artifact Repository --limit 1")

    assert "03 Products/Artifact Repository.md" in output
    assert "Telegram Context Export" in output
