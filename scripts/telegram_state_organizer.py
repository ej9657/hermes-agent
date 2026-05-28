#!/usr/bin/env python3
"""Build Telegram conversation/task indexes inside Hermes state.db.

This script is deliberately conservative:
- it creates side tables instead of changing Hermes' core session schema;
- it indexes durable Telegram rows from state.db first;
- it uses exact Telegram ingress events from logs as enrichment/fallback;
- it can backfill missing user rows only when the exact inbound text is known;
- it never invents assistant responses.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


TS_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d{3}")
INBOUND_RE = re.compile(
    r"inbound message: platform=telegram user=(?P<user>.*?) "
    r"chat=(?P<chat>-?\d+) msg=(?P<msg>.*)$"
)
TURN_RE = re.compile(
    r"\[(?P<sid>[^\]]+)\].*conversation turn: session=(?P=sid) "
    r"model=(?P<model>\S+) provider=(?P<provider>\S+) "
    r"platform=telegram history=(?P<history>\d+) msg=(?P<msg>.*)$"
)
RESPONSE_RE = re.compile(
    r"response ready: platform=telegram chat=(?P<chat>-?\d+) "
    r"time=(?P<seconds>[\d.]+)s api_calls=(?P<api_calls>\d+) "
    r"response=(?P<chars>\d+) chars"
)
PERSIST_RE = re.compile(
    r"Gateway transcript persistence: .*session (?P<sid>\S+).*"
)

TASK_RE = re.compile(
    r"\b("
    r"please|can you|could you|need|should i|what should|next steps|"
    r"continue|build|fix|clean|review|analy[sz]e|create|add|upload|"
    r"save|resolve|implement|verify|confirm|draft|generate|organize|"
    r"track|setup|set up|audit|inspect|sort"
    r")\b",
    re.IGNORECASE,
)


@dataclass
class Inbound:
    ts: float
    ts_text: str
    chat_id: str
    user_name: str
    text: str


@dataclass
class Turn:
    ts: float
    ts_text: str
    session_id: str
    model: str
    provider: str
    history: int
    text: str


@dataclass
class Response:
    ts: float
    ts_text: str
    chat_id: str
    seconds: float
    api_calls: int
    chars: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-home", default=str(Path.home() / ".hermes"))
    parser.add_argument("--apply", action="store_true", help="write indexes to state.db")
    parser.add_argument(
        "--no-db-first",
        action="store_true",
        help="skip state.db-derived turns and use log matching only",
    )
    parser.add_argument(
        "--backfill-users",
        action="store_true",
        help="insert exact missing Telegram user rows from logs into messages",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="copy state.db to state-snapshots before mutating",
    )
    parser.add_argument("--report", default="", help="optional JSON report path")
    parser.add_argument("--export", action="store_true", help="export read-only Telegram context")
    parser.add_argument("--output", default="", help="optional export path")
    parser.add_argument("--chat", default="", help="chat id or case-insensitive chat-name fragment")
    parser.add_argument("--category", default="", help="task category filter for exports")
    parser.add_argument("--since", default="", help="local date/time lower bound, e.g. 2026-05-25")
    parser.add_argument("--limit", type=int, default=40, help="maximum turns/tasks to export")
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="export format",
    )
    return parser.parse_args()


def parse_log_ts(line: str) -> tuple[float, str] | None:
    match = TS_RE.match(line)
    if not match:
        return None
    raw = match.group("ts")
    dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    return dt.timestamp(), raw


def parse_repr_text(value: str) -> str:
    value = value.strip()
    try:
        parsed = ast.literal_eval(value)
        if isinstance(parsed, str):
            return parsed
    except Exception:
        pass
    return value.strip("'\"")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def parse_logs(log_paths: list[Path]) -> tuple[list[Inbound], list[Turn], list[Response], set[str]]:
    inbounds: list[Inbound] = []
    turns: list[Turn] = []
    responses: list[Response] = []
    persisted_sessions: set[str] = set()

    for path in log_paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                ts_pair = parse_log_ts(line)
                if not ts_pair:
                    continue
                ts, ts_text = ts_pair

                inbound = INBOUND_RE.search(line)
                if inbound:
                    inbounds.append(
                        Inbound(
                            ts=ts,
                            ts_text=ts_text,
                            chat_id=inbound.group("chat"),
                            user_name=inbound.group("user"),
                            text=parse_repr_text(inbound.group("msg")),
                        )
                    )
                    continue

                turn = TURN_RE.search(line)
                if turn:
                    turns.append(
                        Turn(
                            ts=ts,
                            ts_text=ts_text,
                            session_id=turn.group("sid"),
                            model=turn.group("model"),
                            provider=turn.group("provider"),
                            history=int(turn.group("history")),
                            text=parse_repr_text(turn.group("msg")),
                        )
                    )
                    continue

                response = RESPONSE_RE.search(line)
                if response:
                    responses.append(
                        Response(
                            ts=ts,
                            ts_text=ts_text,
                            chat_id=response.group("chat"),
                            seconds=float(response.group("seconds")),
                            api_calls=int(response.group("api_calls")),
                            chars=int(response.group("chars")),
                        )
                    )
                    continue

                persisted = PERSIST_RE.search(line)
                if persisted:
                    persisted_sessions.add(persisted.group("sid").rstrip("."))

    return inbounds, turns, responses, persisted_sessions


def build_chat_directory(hermes_home: Path) -> dict[str, dict[str, Any]]:
    directory: dict[str, dict[str, Any]] = {}
    channel_dir = read_json(hermes_home / "channel_directory.json", {})
    for item in channel_dir.get("platforms", {}).get("telegram", []):
        chat_id = str(item.get("id", "")).strip()
        if chat_id:
            directory[chat_id] = {
                "chat_id": chat_id,
                "chat_name": item.get("name") or chat_id,
                "chat_type": item.get("type") or "",
                "thread_id": item.get("thread_id"),
            }

    sessions_json = read_json(hermes_home / "sessions" / "sessions.json", {})
    for session_key, entry in sessions_json.items():
        origin = entry.get("origin") or {}
        if entry.get("platform") != "telegram" and origin.get("platform") != "telegram":
            continue
        chat_id = str(origin.get("chat_id") or "").strip()
        if not chat_id:
            continue
        directory.setdefault(
            chat_id,
            {
                "chat_id": chat_id,
                "chat_name": origin.get("chat_name") or entry.get("display_name") or chat_id,
                "chat_type": origin.get("chat_type") or entry.get("chat_type") or "",
                "thread_id": origin.get("thread_id"),
            },
        )
        directory[chat_id].update(
            {
                "chat_name": origin.get("chat_name") or entry.get("display_name") or directory[chat_id]["chat_name"],
                "chat_type": origin.get("chat_type") or entry.get("chat_type") or directory[chat_id].get("chat_type", ""),
            }
        )
    return directory


def load_session_entries(hermes_home: Path) -> dict[str, dict[str, Any]]:
    sessions_json = read_json(hermes_home / "sessions" / "sessions.json", {})
    by_session: dict[str, dict[str, Any]] = {}
    for session_key, entry in sessions_json.items():
        sid = entry.get("session_id")
        if sid:
            by_session[sid] = {"session_key": session_key, **entry}
    return by_session


def build_session_chat_map(
    conn: sqlite3.Connection,
    sessions_by_id: dict[str, dict[str, Any]],
    chat_directory: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Map Telegram session ids to chat metadata from the strongest sources."""

    session_chats: dict[str, dict[str, Any]] = {}
    for sid, entry in sessions_by_id.items():
        origin = entry.get("origin") or {}
        if entry.get("platform") != "telegram" and origin.get("platform") != "telegram":
            continue
        chat_id = str(origin.get("chat_id") or "").strip()
        if not chat_id:
            continue
        chat = {
            "chat_id": chat_id,
            "chat_name": origin.get("chat_name") or entry.get("display_name") or chat_id,
            "chat_type": origin.get("chat_type") or entry.get("chat_type") or "",
            "thread_id": origin.get("thread_id"),
            "user_name": origin.get("user_name") or entry.get("user_name") or "",
        }
        session_chats[sid] = chat
        chat_directory.setdefault(chat_id, chat)

    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='telegram_turn_index'"
    ).fetchone()
    if table_exists:
        rows = conn.execute(
            """
            SELECT DISTINCT t.session_id, t.chat_id, c.chat_name, c.chat_type, c.thread_id
            FROM telegram_turn_index t
            LEFT JOIN telegram_chat_index c ON c.chat_id = t.chat_id
            WHERE t.session_id IS NOT NULL AND t.chat_id IS NOT NULL
            """
        ).fetchall()
        for sid, chat_id, chat_name, chat_type, thread_id in rows:
            chat = {
                "chat_id": str(chat_id),
                "chat_name": chat_name or chat_directory.get(str(chat_id), {}).get("chat_name") or str(chat_id),
                "chat_type": chat_type or chat_directory.get(str(chat_id), {}).get("chat_type") or "",
                "thread_id": thread_id,
                "user_name": "",
            }
            session_chats.setdefault(sid, chat)
            chat_directory.setdefault(str(chat_id), chat)
    return session_chats


def _message_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def load_db_turns(
    conn: sqlite3.Connection,
    session_chats: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build turn records from durable Telegram message rows in state.db."""

    if not session_chats:
        return []
    placeholders = ",".join("?" for _ in session_chats)
    rows = conn.execute(
        f"""
        SELECT s.id, s.model, s.billing_provider, s.started_at,
               m.role, m.content, m.timestamp, m.platform_message_id
        FROM sessions s
        JOIN messages m ON m.session_id = s.id
        WHERE s.source = 'telegram'
          AND s.id IN ({placeholders})
          AND m.role IN ('user', 'assistant')
        ORDER BY s.id, m.timestamp, m.id
        """,
        tuple(session_chats.keys()),
    ).fetchall()

    by_session: dict[str, list[dict[str, Any]]] = {}
    for sid, model, provider, started_at, role, content, ts, platform_message_id in rows:
        by_session.setdefault(sid, []).append(
            {
                "session_id": sid,
                "model": model or "",
                "provider": provider or "",
                "started_at": started_at,
                "role": role,
                "content": _message_text(content),
                "timestamp": float(ts or started_at or 0),
                "platform_message_id": platform_message_id,
            }
        )

    matched: list[dict[str, Any]] = []
    for sid, messages in by_session.items():
        chat = session_chats[sid]
        for idx, msg in enumerate(messages):
            if msg["role"] != "user" or not msg["content"]:
                continue
            response = None
            for candidate in messages[idx + 1:]:
                if candidate["role"] == "user":
                    break
                if candidate["role"] == "assistant" and candidate["content"]:
                    response = candidate
                    break
            matched.append(
                {
                    "session_id": sid,
                    "chat_id": chat["chat_id"],
                    "user_name": chat.get("user_name") or "",
                    "inbound_at": msg["timestamp"],
                    "inbound_at_text": datetime.fromtimestamp(msg["timestamp"]).strftime("%Y-%m-%d %H:%M:%S"),
                    "turn_at": msg["timestamp"],
                    "turn_at_text": datetime.fromtimestamp(msg["timestamp"]).strftime("%Y-%m-%d %H:%M:%S"),
                    "message_text": msg["content"],
                    "model": msg["model"],
                    "provider": msg["provider"],
                    "history": idx,
                    "response_at": response["timestamp"] if response else None,
                    "response_at_text": (
                        datetime.fromtimestamp(response["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
                        if response else None
                    ),
                    "response_seconds": None,
                    "response_api_calls": None,
                    "response_chars": len(response["content"]) if response else None,
                    "source_kind": "state_db",
                    "platform_message_id": msg.get("platform_message_id"),
                }
            )
    return matched


def merge_matched_turns(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for group in groups:
        for item in group:
            key = (
                item["session_id"],
                f"{float(item['inbound_at']):.6f}",
                item["message_text"],
            )
            if key not in merged or merged[key].get("source_kind") != "state_db":
                merged[key] = item
    return sorted(merged.values(), key=lambda item: (item["inbound_at"], item["session_id"]))


def parse_since(value: str) -> float | None:
    if not value:
        return None
    value = value.strip()
    formats = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).timestamp()
        except ValueError:
            continue
    raise SystemExit(f"Could not parse --since value: {value!r}")


def text_matches(a: str, b: str) -> bool:
    if not a or not b:
        return False
    a_clean = a.strip()
    b_clean = b.strip()
    return a_clean == b_clean or a_clean.startswith(b_clean[:70]) or b_clean.startswith(a_clean[:70])


def match_turns(inbounds: list[Inbound], turns: list[Turn], responses: list[Response]) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    used_turns: set[int] = set()
    response_idx = 0
    responses_sorted = sorted(responses, key=lambda item: item.ts)

    for inbound in sorted(inbounds, key=lambda item: item.ts):
        best_idx = None
        best_score = 10_000.0
        for idx, turn in enumerate(turns):
            if idx in used_turns:
                continue
            delta = turn.ts - inbound.ts
            if delta < -2 or delta > 180:
                continue
            if not text_matches(inbound.text, turn.text):
                continue
            if delta < best_score:
                best_score = delta
                best_idx = idx
        if best_idx is None:
            continue
        used_turns.add(best_idx)
        turn = turns[best_idx]

        while response_idx < len(responses_sorted) and responses_sorted[response_idx].ts < turn.ts:
            response_idx += 1
        response = None
        for candidate in responses_sorted[response_idx:response_idx + 20]:
            if candidate.chat_id == inbound.chat_id and candidate.ts >= turn.ts:
                response = candidate
                break

        matched.append(
            {
                "session_id": turn.session_id,
                "chat_id": inbound.chat_id,
                "user_name": inbound.user_name,
                "inbound_at": inbound.ts,
                "inbound_at_text": inbound.ts_text,
                "turn_at": turn.ts,
                "turn_at_text": turn.ts_text,
                "message_text": inbound.text,
                "model": turn.model,
                "provider": turn.provider,
                "history": turn.history,
                "response_at": response.ts if response else None,
                "response_at_text": response.ts_text if response else None,
                "response_seconds": response.seconds if response else None,
                "response_api_calls": response.api_calls if response else None,
                "response_chars": response.chars if response else None,
            }
        )
    return matched


def category_for(chat_name: str, title: str, text: str) -> str:
    blob = f"{chat_name} {title} {text}".lower()
    if "mnfst" in blob or "playlist" in blob or "suno" in blob:
        return "mnfst"
    if "dresito" in blob or "otra vez" in blob:
        return "dresito"
    if "6lack" in blob or "video studio" in blob or "remotion" in blob:
        return "ason_video"
    if "artifact" in blob:
        return "artifact_repository"
    if "prompt" in blob:
        return "prompt_lab"
    if "gateway" in blob or "hermes" in blob or "codex" in blob:
        return "hermes_runtime"
    if "business" in blob or "brand" in blob:
        return "brand_ops"
    return "general"


def task_status_from_text(text: str) -> str:
    lower = text.lower()
    if any(word in lower for word in ("done", "resolved", "complete", "shipped", "passed")):
        return "maybe_done"
    if any(word in lower for word in ("blocked", "stuck", "failed", "can't", "cannot")):
        return "blocked_or_question"
    if "?" in text:
        return "question"
    return "open"


def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS telegram_chat_index (
            chat_id TEXT PRIMARY KEY,
            chat_name TEXT,
            chat_type TEXT,
            thread_id TEXT,
            session_count INTEGER DEFAULT 0,
            turn_count INTEGER DEFAULT 0,
            task_count INTEGER DEFAULT 0,
            first_seen REAL,
            last_seen REAL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS telegram_turn_index (
            session_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            user_name TEXT,
            inbound_at REAL,
            turn_at REAL,
            response_at REAL,
            message_text TEXT,
            model TEXT,
            provider TEXT,
            response_seconds REAL,
            response_api_calls INTEGER,
            response_chars INTEGER,
            persisted_by_gateway INTEGER DEFAULT 0,
            PRIMARY KEY (session_id, inbound_at, message_text)
        );

        CREATE TABLE IF NOT EXISTS telegram_task_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            chat_name TEXT,
            category TEXT,
            task_text TEXT NOT NULL,
            status TEXT,
            confidence REAL,
            source TEXT,
            source_at REAL,
            created_at REAL NOT NULL,
            UNIQUE(session_id, chat_id, task_text, source_at)
        );

        CREATE INDEX IF NOT EXISTS idx_telegram_turn_chat_time
            ON telegram_turn_index(chat_id, inbound_at);
        CREATE INDEX IF NOT EXISTS idx_telegram_task_chat_status
            ON telegram_task_index(chat_id, status);
        """
    )


def existing_user_texts(conn: sqlite3.Connection, session_id: str) -> set[str]:
    rows = conn.execute(
        "SELECT content FROM messages WHERE session_id = ? AND role = 'user'",
        (session_id,),
    ).fetchall()
    return {row[0] for row in rows if row[0]}


def session_message_counts(conn: sqlite3.Connection, session_id: str) -> tuple[int, int, int]:
    row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN role = 'user' THEN 1 ELSE 0 END),
            SUM(CASE WHEN role = 'assistant' THEN 1 ELSE 0 END),
            COUNT(*)
        FROM messages WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()
    return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)


def upsert_indexes(
    conn: sqlite3.Connection,
    matched: list[dict[str, Any]],
    chat_directory: dict[str, dict[str, Any]],
    sessions_by_id: dict[str, dict[str, Any]],
    persisted_sessions: set[str],
    *,
    backfill_users: bool,
) -> dict[str, Any]:
    create_tables(conn)
    conn.execute("DELETE FROM telegram_task_index")
    conn.execute("DELETE FROM telegram_turn_index")
    conn.execute("DELETE FROM telegram_chat_index")
    now = datetime.now().timestamp()
    stats = {
        "matched_turns": len(matched),
        "backfilled_user_rows": 0,
        "indexed_tasks": 0,
        "indexed_chats": 0,
    }

    by_chat: dict[str, list[dict[str, Any]]] = {}
    for item in matched:
        by_chat.setdefault(item["chat_id"], []).append(item)

    for chat_id, items in by_chat.items():
        chat = chat_directory.get(chat_id, {"chat_name": chat_id, "chat_type": ""})
        session_count = len({item["session_id"] for item in items})
        task_count = sum(1 for item in items if TASK_RE.search(item["message_text"]))
        conn.execute(
            """
            INSERT INTO telegram_chat_index
                (chat_id, chat_name, chat_type, thread_id, session_count, turn_count,
                 task_count, first_seen, last_seen, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                chat_name = excluded.chat_name,
                chat_type = excluded.chat_type,
                thread_id = excluded.thread_id,
                session_count = excluded.session_count,
                turn_count = excluded.turn_count,
                task_count = excluded.task_count,
                first_seen = excluded.first_seen,
                last_seen = excluded.last_seen,
                updated_at = excluded.updated_at
            """,
            (
                chat_id,
                chat.get("chat_name"),
                chat.get("chat_type"),
                chat.get("thread_id"),
                session_count,
                len(items),
                task_count,
                min(item["inbound_at"] for item in items),
                max(item["inbound_at"] for item in items),
                now,
            ),
        )
        stats["indexed_chats"] += 1

    for item in matched:
        chat = chat_directory.get(item["chat_id"], {"chat_name": item["chat_id"], "chat_type": ""})
        conn.execute(
            """
            INSERT OR REPLACE INTO telegram_turn_index
                (session_id, chat_id, user_name, inbound_at, turn_at, response_at,
                 message_text, model, provider, response_seconds, response_api_calls,
                 response_chars, persisted_by_gateway)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["session_id"],
                item["chat_id"],
                item["user_name"],
                item["inbound_at"],
                item["turn_at"],
                item["response_at"],
                item["message_text"],
                item["model"],
                item["provider"],
                item["response_seconds"],
                item["response_api_calls"],
                item["response_chars"],
                1 if item["session_id"] in persisted_sessions else 0,
            ),
        )

        if backfill_users and item["message_text"] not in existing_user_texts(conn, item["session_id"]):
            conn.execute(
                """
                INSERT INTO messages
                    (session_id, role, content, timestamp, platform_message_id, observed)
                VALUES (?, 'user', ?, ?, NULL, 0)
                """,
                (item["session_id"], item["message_text"], item["inbound_at"]),
            )
            stats["backfilled_user_rows"] += 1

        if TASK_RE.search(item["message_text"]):
            session_entry = sessions_by_id.get(item["session_id"], {})
            title = session_title(conn, item["session_id"])
            category = category_for(chat.get("chat_name") or "", title or "", item["message_text"])
            status = task_status_from_text(item["message_text"])
            conn.execute(
                """
                INSERT OR IGNORE INTO telegram_task_index
                    (session_id, chat_id, chat_name, category, task_text, status,
                     confidence, source, source_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["session_id"],
                    item["chat_id"],
                    chat.get("chat_name"),
                    category,
                    item["message_text"],
                    status,
                    0.78,
                    session_entry.get("session_key") or "gateway_log",
                    item["inbound_at"],
                    now,
                ),
            )
            stats["indexed_tasks"] += 1

    for row in conn.execute("SELECT id FROM sessions WHERE source = 'telegram'").fetchall():
        sid = row[0]
        user_count, assistant_count, total = session_message_counts(conn, sid)
        if total:
            conn.execute(
                "UPDATE sessions SET message_count = ? WHERE id = ?",
                (total, sid),
            )

    conn.execute(
        "INSERT OR REPLACE INTO state_meta (key, value) VALUES (?, ?)",
        (
            "telegram_state_organizer.last_run",
            json.dumps({"ran_at": datetime.now().isoformat(), **stats}, sort_keys=True),
        ),
    )
    return stats


def session_title(conn: sqlite3.Connection, session_id: str) -> str:
    row = conn.execute("SELECT title FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return row[0] if row and row[0] else ""


def audit_db(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT COUNT(*),
               SUM(CASE WHEN EXISTS (
                    SELECT 1 FROM messages m WHERE m.session_id = s.id AND m.role = 'user'
               ) THEN 1 ELSE 0 END),
               SUM(CASE WHEN NOT EXISTS (
                    SELECT 1 FROM messages m WHERE m.session_id = s.id AND m.role = 'user'
               ) THEN 1 ELSE 0 END)
        FROM sessions s WHERE source = 'telegram'
        """
    ).fetchone()
    return {
        "telegram_sessions": int(row[0] or 0),
        "sessions_with_user_rows": int(row[1] or 0),
        "sessions_missing_user_rows": int(row[2] or 0),
    }


def backup_db(db_path: Path, hermes_home: Path) -> Path:
    backup_dir = hermes_home / "state-snapshots"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"state-before-telegram-organizer-{stamp}.db"
    shutil.copy2(db_path, backup_path)
    return backup_path


def resolve_chat_filter(conn: sqlite3.Connection, value: str) -> list[str]:
    if not value:
        return []
    needle = value.casefold()
    rows = conn.execute(
        "SELECT chat_id, chat_name FROM telegram_chat_index ORDER BY chat_name"
    ).fetchall()
    matches = [
        chat_id for chat_id, chat_name in rows
        if needle == str(chat_id).casefold() or needle in str(chat_name or "").casefold()
    ]
    if not matches:
        raise SystemExit(f"No indexed Telegram chat matched --chat {value!r}")
    return matches


def export_context(
    conn: sqlite3.Connection,
    *,
    chat: str = "",
    category: str = "",
    since: str = "",
    limit: int = 40,
    output_format: str = "markdown",
) -> str:
    since_ts = parse_since(since)
    chat_ids = resolve_chat_filter(conn, chat)
    turn_where = []
    task_where = []
    params: list[Any] = []
    task_params: list[Any] = []
    if chat_ids:
        placeholders = ",".join("?" for _ in chat_ids)
        turn_where.append(f"t.chat_id IN ({placeholders})")
        task_where.append(f"k.chat_id IN ({placeholders})")
        params.extend(chat_ids)
        task_params.extend(chat_ids)
    if since_ts is not None:
        turn_where.append("t.inbound_at >= ?")
        task_where.append("k.source_at >= ?")
        params.append(since_ts)
        task_params.append(since_ts)
    if category:
        task_where.append("k.category = ?")
        task_params.append(category)

    turn_sql = """
        SELECT t.chat_id, COALESCE(c.chat_name, t.chat_id) AS chat_name,
               t.session_id, t.message_text, t.model, t.provider,
               t.response_chars, t.persisted_by_gateway, t.inbound_at
        FROM telegram_turn_index t
        LEFT JOIN telegram_chat_index c ON c.chat_id = t.chat_id
    """
    if turn_where:
        turn_sql += " WHERE " + " AND ".join(turn_where)
    turn_sql += " ORDER BY t.inbound_at DESC LIMIT ?"
    turn_rows = conn.execute(turn_sql, (*params, max(limit, 1))).fetchall()

    task_sql = """
        SELECT k.chat_id, k.chat_name, k.category, k.status, k.task_text,
               k.source, k.source_at
        FROM telegram_task_index k
    """
    if task_where:
        task_sql += " WHERE " + " AND ".join(task_where)
    task_sql += " ORDER BY k.source_at DESC LIMIT ?"
    task_rows = conn.execute(task_sql, (*task_params, max(limit, 1))).fetchall()

    payload = {
        "filters": {
            "chat": chat,
            "category": category,
            "since": since,
            "limit": limit,
        },
        "generated_at": datetime.now().isoformat(),
        "turns": [
            {
                "chat_id": row[0],
                "chat_name": row[1],
                "session_id": row[2],
                "message_text": row[3],
                "model": row[4],
                "provider": row[5],
                "response_chars": row[6],
                "persisted_by_gateway": bool(row[7]),
                "inbound_at": row[8],
                "inbound_at_text": datetime.fromtimestamp(row[8]).strftime("%Y-%m-%d %H:%M:%S"),
            }
            for row in turn_rows
        ],
        "tasks": [
            {
                "chat_id": row[0],
                "chat_name": row[1],
                "category": row[2],
                "status": row[3],
                "task_text": row[4],
                "source": row[5],
                "source_at": row[6],
                "source_at_text": datetime.fromtimestamp(row[6]).strftime("%Y-%m-%d %H:%M:%S"),
            }
            for row in task_rows
        ],
    }
    if output_format == "json":
        return json.dumps(payload, indent=2, sort_keys=True)

    lines = [
        "# Telegram Context Export",
        "",
        f"- Generated: {payload['generated_at']}",
        f"- Filters: chat={chat or 'any'}, category={category or 'any'}, since={since or 'any'}, limit={limit}",
        f"- Turns: {len(payload['turns'])}",
        f"- Task candidates: {len(payload['tasks'])}",
        "",
        "## Recent Turns",
        "",
    ]
    for item in payload["turns"]:
        text = item["message_text"].replace("\n", " ").strip()
        if len(text) > 500:
            text = text[:497] + "..."
        lines.extend(
            [
                f"### {item['inbound_at_text']} - {item['chat_name']}",
                f"- Session: `{item['session_id']}`",
                f"- Model/provider: `{item['model'] or 'unknown'}` / `{item['provider'] or 'unknown'}`",
                f"- Gateway persisted: `{str(item['persisted_by_gateway']).lower()}`",
                f"- User: {text}",
                "",
            ]
        )
    lines.extend(["## Task Candidates", ""])
    for item in payload["tasks"]:
        text = item["task_text"].replace("\n", " ").strip()
        if len(text) > 500:
            text = text[:497] + "..."
        lines.extend(
            [
                f"- `{item['source_at_text']}` [{item['chat_name']} / {item['category']} / {item['status']}] {text}",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    hermes_home = Path(args.hermes_home).expanduser()
    db_path = hermes_home / "state.db"
    log_paths = [
        hermes_home / "logs" / "gateway.log",
        hermes_home / "logs" / "agent.log",
    ]
    log_paths.extend(sorted((hermes_home / "logs" / "archive").glob("**/gateway.log")))
    log_paths.extend(sorted((hermes_home / "logs" / "archive").glob("**/agent.log")))
    conn = sqlite3.connect(db_path)
    try:
        chat_directory = build_chat_directory(hermes_home)
        sessions_by_id = load_session_entries(hermes_home)
        session_chats = build_session_chat_map(conn, sessions_by_id, chat_directory)
        if args.export:
            create_tables(conn)
            rendered = export_context(
                conn,
                chat=args.chat,
                category=args.category,
                since=args.since,
                limit=args.limit,
                output_format=args.format,
            )
            if args.output:
                Path(args.output).expanduser().write_text(rendered, encoding="utf-8")
            print(rendered, end="" if rendered.endswith("\n") else "\n")
            return 0

        inbounds, turns, responses, persisted_sessions = parse_logs(log_paths)
        log_matched = match_turns(inbounds, turns, responses)
        for item in log_matched:
            item.setdefault("source_kind", "logs")
        db_matched = [] if args.no_db_first else load_db_turns(conn, session_chats)
        matched = merge_matched_turns(db_matched, log_matched)
        before = audit_db(conn)
        report = {
            "mode": "apply" if args.apply else "dry_run",
            "db_path": str(db_path),
            "log_inbounds": len(inbounds),
            "log_turns": len(turns),
            "log_matched_turns": len(log_matched),
            "db_mapped_sessions": len(session_chats),
            "db_matched_turns": len(db_matched),
            "matched_turns": len(matched),
            "known_telegram_chats": len(chat_directory),
            "persisted_sessions_from_logs": len(persisted_sessions),
            "before": before,
            "chat_turns": {},
            "task_candidates": 0,
            "backup_path": None,
            "apply_stats": None,
        }
        for item in matched:
            chat = chat_directory.get(item["chat_id"], {"chat_name": item["chat_id"]})
            key = f"{item['chat_id']}::{chat.get('chat_name')}"
            report["chat_turns"][key] = report["chat_turns"].get(key, 0) + 1
            if TASK_RE.search(item["message_text"]):
                report["task_candidates"] += 1

        if args.apply:
            if args.backup:
                report["backup_path"] = str(backup_db(db_path, hermes_home))
            with conn:
                report["apply_stats"] = upsert_indexes(
                    conn,
                    matched,
                    chat_directory,
                    sessions_by_id,
                    persisted_sessions,
                    backfill_users=args.backfill_users,
                )
            report["after"] = audit_db(conn)

        if args.report:
            Path(args.report).expanduser().write_text(
                json.dumps(report, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        print(json.dumps(report, indent=2, sort_keys=True))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
