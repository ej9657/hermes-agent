"""Read-only Telegram context helpers for Hermes slash commands."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TelegramRoute:
    chat_id: str
    chat_name: str
    category: str
    obsidian_route: str
    confidence: str
    notes: str


TELEGRAM_ROUTES: tuple[TelegramRoute, ...] = (
    TelegramRoute(
        "8027196461",
        "Agentic Army",
        "hermes_runtime",
        "03 Products/Hermes Agent Dashboard.md",
        "High",
        "Hermes runtime, agent operations, gateway, routing, and local automation work.",
    ),
    TelegramRoute(
        "-5235848692",
        "MNFST OS.",
        "mnfst",
        "01 ASON/MNFST Project.md",
        "High",
        "MNFST label, catalog, music channel, Suno, playlist, and sync/licensing work.",
    ),
    TelegramRoute(
        "-5115347960",
        "Erik Brand OS",
        "brand_ops",
        "00 Home/Erik Johnson — Full Business Ecosystem.md",
        "Medium",
        "Personal brand, executive identity, and cross-venture positioning.",
    ),
    TelegramRoute(
        "-5282098406",
        "ASON Video Studio",
        "ason_video",
        "05 Video Production/ASON Video Studio.md",
        "High",
        "Durable ASON video production lane for Remotion, rerenders, audits, references, and production direction.",
    ),
    TelegramRoute(
        "-5214343030",
        "Visual Prompt Lab",
        "prompt_lab",
        "01 ASON/Visual Prompt Lab.md",
        "Medium",
        "Reusable learning/intelligence lane for prompt sources, hook libraries, video analysis, and reference ingestion.",
    ),
    TelegramRoute(
        "-5277591288",
        "i2 Channel",
        "i2",
        "01 ASON/i2 Cultural Performance Brief.md",
        "High",
        "i2 cultural-performance report work and reusable creator/brand activation analysis.",
    ),
    TelegramRoute(
        "-5186373520",
        "Artifact Repository",
        "artifact_repository",
        "03 Products/Artifact Repository.md",
        "High",
        "Durable product/library lane for local artifacts, visual/code examples, retrieval, and catalog UX.",
    ),
    TelegramRoute(
        "-5238323387",
        "Dresito",
        "dresito",
        "02 Clients/Dresito/Dresito Project.md",
        "High",
        "Dresito client/project work, music releases, pitch decks, visualizers, Linkfire, YouTube, and release ops.",
    ),
    TelegramRoute(
        "-5159072251",
        "NuOrganic TikTok Ad",
        "nuorganic",
        "02 Clients/NUORGANIC/NUORGANIC Project.md",
        "High",
        "NuOrganic skincare, TikTok ad, KLOUT, creator commerce, and related client work.",
    ),
    TelegramRoute(
        "-5133995312",
        "Random Prompts",
        "prompt_lab",
        "00 Home/Home.md",
        "High",
        "Capture and triage lane; route to a stronger project when the message names one.",
    ),
    TelegramRoute(
        "-5191166352",
        "ASON Business Building",
        "brand_ops",
        "01 ASON/ASON Business Context v3.5.md",
        "High",
        "ASON operating model, business strategy, service architecture, and CULTURE_OS strategy.",
    ),
)

KNOWN_CATEGORIES = {
    "artifact_repository",
    "ason_video",
    "brand_ops",
    "dresito",
    "general",
    "hermes_runtime",
    "mnfst",
    "prompt_lab",
}


def _script_path() -> Path:
    return Path(__file__).resolve().parents[1] / "scripts" / "telegram_state_organizer.py"


def _reports_dir() -> Path:
    path = Path.home() / ".hermes" / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_route(value: str | None) -> TelegramRoute | None:
    needle = (value or "").strip().casefold()
    if not needle:
        return None
    for route in TELEGRAM_ROUTES:
        if needle == route.chat_id.casefold():
            return route
    for route in TELEGRAM_ROUTES:
        if needle == route.chat_name.casefold() or needle in route.chat_name.casefold():
            return route
    for route in TELEGRAM_ROUTES:
        if needle == route.category.casefold():
            return route
    return None


def _run_organizer(args: list[str], *, timeout: float = 30.0) -> str:
    script = _script_path()
    if not script.exists():
        raise RuntimeError(f"Telegram organizer script not found: {script}")
    result = subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail or f"telegram organizer exited {result.returncode}")
    return result.stdout


def _parse_options(tokens: list[str]) -> tuple[str, str, str, int]:
    target_parts: list[str] = []
    chat = ""
    category = ""
    since = ""
    limit = 10
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--chat":
            chat = tokens[i + 1] if i + 1 < len(tokens) else ""
            i += 2
            continue
        if tok.startswith("--chat="):
            chat = tok.split("=", 1)[1]
            i += 1
            continue
        if tok == "--category":
            category = tokens[i + 1] if i + 1 < len(tokens) else ""
            i += 2
            continue
        if tok.startswith("--category="):
            category = tok.split("=", 1)[1]
            i += 1
            continue
        if tok == "--since":
            since = tokens[i + 1] if i + 1 < len(tokens) else ""
            i += 2
            continue
        if tok.startswith("--since="):
            since = tok.split("=", 1)[1]
            i += 1
            continue
        if tok == "--limit":
            raw_limit = tokens[i + 1] if i + 1 < len(tokens) else ""
            try:
                limit = max(1, min(int(raw_limit), 80))
            except ValueError:
                limit = 10
            i += 2
            continue
        if tok.startswith("--limit="):
            try:
                limit = max(1, min(int(tok.split("=", 1)[1]), 80))
            except ValueError:
                limit = 10
            i += 1
            continue
        target_parts.append(tok)
        i += 1
    return " ".join(target_parts).strip(), chat, category, since, limit


def _usage() -> str:
    return (
        "Usage:\n"
        "`/tctx recent [chat|category] [--since YYYY-MM-DD] [--limit N]`\n"
        "`/tctx tasks [chat|category] [--since YYYY-MM-DD] [--limit N]`\n"
        "`/tctx brief [chat|category] [--since YYYY-MM-DD] [--limit N]`\n"
        "`/tctx route [chat]`\n"
        "`/tctx refresh`\n\n"
        "Aliases: `/telegram-context`, `/tgctx`."
    )


def _resolve_filters(
    target: str,
    *,
    chat: str = "",
    category: str = "",
    default_chat_id: str = "",
    default_chat_name: str = "",
) -> tuple[str, str, TelegramRoute | None]:
    route = resolve_route(chat or target or default_chat_id or default_chat_name)
    if chat:
        return chat, category, route
    if category:
        return "", category, route
    if target:
        if route and target.strip().casefold() not in KNOWN_CATEGORIES:
            return route.chat_name, "", route
        normalized = target.strip().casefold().replace("-", "_").replace(" ", "_")
        if normalized in KNOWN_CATEGORIES:
            return "", normalized, route
        if route:
            return route.chat_name, "", route
        return target, "", None
    if default_chat_id:
        return default_chat_id, "", route
    if default_chat_name:
        return default_chat_name, "", route
    return "", "", None


def _export_json(*, chat: str, category: str, since: str, limit: int) -> dict[str, Any]:
    args = ["--export", "--format", "json", "--limit", str(limit)]
    if chat:
        args.extend(["--chat", chat])
    if category:
        args.extend(["--category", category])
    if since:
        args.extend(["--since", since])
    return json.loads(_run_organizer(args))


def _render_route(route: TelegramRoute | None, target: str = "") -> str:
    if route is None:
        if target:
            return f"No Telegram project route matched `{target}`."
        lines = ["Telegram project routes:"]
        for item in TELEGRAM_ROUTES:
            lines.append(
                f"- `{item.chat_name}` -> `{item.obsidian_route}` ({item.confidence})"
            )
        return "\n".join(lines)
    return "\n".join(
        [
            f"Route: **{route.chat_name}**",
            f"- Chat ID: `{route.chat_id}`",
            f"- Category: `{route.category}`",
            f"- Obsidian: `{route.obsidian_route}`",
            f"- Confidence: `{route.confidence}`",
            f"- Notes: {route.notes}",
        ]
    )


def _truncate(text: str, limit: int = 220) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _render_tasks(payload: dict[str, Any], route: TelegramRoute | None) -> str:
    lines = ["# Telegram Task Candidates", ""]
    if route:
        lines.append(f"Route: `{route.obsidian_route}` ({route.chat_name})")
    lines.append(f"Tasks: {len(payload.get('tasks') or [])}")
    lines.append("")
    for task in payload.get("tasks") or []:
        lines.append(
            f"- `{task.get('source_at_text')}` [{task.get('chat_name')} / "
            f"{task.get('category')} / {task.get('status')}] "
            f"{_truncate(task.get('task_text') or '', 280)}"
        )
    return "\n".join(lines).rstrip()


def _render_brief(payload: dict[str, Any], route: TelegramRoute | None) -> str:
    turns = payload.get("turns") or []
    tasks = payload.get("tasks") or []
    lines = ["# Telegram Context Brief", ""]
    if route:
        lines.extend(
            [
                f"Route: `{route.obsidian_route}`",
                f"Chat: `{route.chat_name}` (`{route.chat_id}`)",
                f"Category: `{route.category}`",
                "",
            ]
        )
    filters = payload.get("filters") or {}
    lines.extend(
        [
            f"Filters: chat={filters.get('chat') or 'any'}, category={filters.get('category') or 'any'}, since={filters.get('since') or 'any'}",
            f"Recent turns: {len(turns)}",
            f"Task candidates: {len(tasks)}",
            "",
            "## Recent Turns",
            "",
        ]
    )
    for turn in turns[:8]:
        lines.append(
            f"- `{turn.get('inbound_at_text')}` [{turn.get('chat_name')}] "
            f"{_truncate(turn.get('message_text') or '')}"
        )
    lines.extend(["", "## Open Signals", ""])
    for task in tasks[:8]:
        lines.append(
            f"- `{task.get('source_at_text')}` [{task.get('category')} / {task.get('status')}] "
            f"{_truncate(task.get('task_text') or '', 240)}"
        )
    return "\n".join(lines).rstrip()


def _refresh_indexes() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = _reports_dir() / f"telegram-context-refresh-{stamp}.json"
    output = _run_organizer(
        ["--apply", "--backup", "--report", str(report_path)],
        timeout=180.0,
    )
    try:
        report = json.loads(output)
    except json.JSONDecodeError:
        return output.strip()
    after = report.get("after") or {}
    apply_stats = report.get("apply_stats") or {}
    return "\n".join(
        [
            "Telegram context indexes refreshed.",
            f"- Matched turns: `{report.get('matched_turns', 0)}`",
            f"- Known chats: `{report.get('known_telegram_chats', 0)}`",
            f"- Indexed turns: `{apply_stats.get('turns', 0)}`",
            f"- Indexed tasks: `{apply_stats.get('tasks', 0)}`",
            f"- Telegram sessions: `{after.get('telegram_sessions', 0)}`",
            f"- Backup: `{report.get('backup_path') or 'none'}`",
            f"- Report: `{report_path}`",
        ]
    )


def run_tctx(
    raw_args: str,
    *,
    default_chat_id: str = "",
    default_chat_name: str = "",
) -> str:
    """Run the read-only Telegram context slash command."""

    try:
        tokens = shlex.split(raw_args or "")
    except ValueError as exc:
        return f"Could not parse /tctx arguments: {exc}"

    if not tokens:
        tokens = ["brief"]
    if tokens[0] in {"help", "-h", "--help"}:
        return _usage()

    subcommands = {"recent", "tasks", "brief", "route", "refresh"}
    subcommand = tokens[0] if tokens[0] in subcommands else "recent"
    rest = tokens[1:] if tokens[0] in subcommands else tokens

    if subcommand == "refresh":
        return _refresh_indexes()

    target, chat, category, since, limit = _parse_options(rest)
    filter_chat, filter_category, route = _resolve_filters(
        target,
        chat=chat,
        category=category,
        default_chat_id=default_chat_id,
        default_chat_name=default_chat_name,
    )

    if subcommand == "route":
        return _render_route(route or resolve_route(target), target or filter_chat or filter_category)

    try:
        if subcommand == "recent":
            args = ["--export", "--format", "markdown", "--limit", str(limit)]
            if filter_chat:
                args.extend(["--chat", filter_chat])
            if filter_category:
                args.extend(["--category", filter_category])
            if since:
                args.extend(["--since", since])
            output = _run_organizer(args)
            if route:
                return f"Route: `{route.obsidian_route}` ({route.chat_name})\n\n{output.strip()}"
            return output.strip()

        payload = _export_json(
            chat=filter_chat,
            category=filter_category,
            since=since,
            limit=limit,
        )
    except Exception as exc:
        return f"Could not load Telegram context: {exc}"

    if subcommand == "tasks":
        return _render_tasks(payload, route)
    if subcommand == "brief":
        return _render_brief(payload, route)
    return _usage()
