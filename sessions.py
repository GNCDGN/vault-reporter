#!/usr/bin/env python3
"""
sessions.py

SQLite-backed conversation state for the vault reporter.

A session is created when a report is delivered with one or more questions.
The user answers questions one at a time via Telegram; the bot listener
records each answer and advances the session. When all questions are
answered (or the user types 'done'), the session is closed and the
write-back step is triggered.

Phase 6+ addition: each session now carries a `messages_json` blob — the
AI-generated conversational messages (greeting, completion lines,
sign-offs, etc.) that the bot uses while talking to the user. These are
stored at session-creation time so the bot doesn't need to regenerate them.

Schema migrates gracefully: if `messages_json` doesn't exist on an older DB,
the column is added via ALTER TABLE on first import.

v4 Phase 2 addition: a separate `chat_sessions` table holds Veronica's
within-day conversational memory — one row per UK calendar day, the day's
turns stored as a JSON list, plus a rolling summary and turn/exchange
counters. Created via CREATE TABLE IF NOT EXISTS like `sessions`. "Today"
is always computed at read-time in Europe/London, never stored at creation.
"""

import os
import sqlite3
import json
import logging
from contextlib import contextmanager
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# UK time governs the chat-session day boundary (00:00–00:00 Europe/London).
UK_TZ = ZoneInfo("Europe/London")

# ---------------------------------------------------------------------------
# Database location
# ---------------------------------------------------------------------------

DB_PATH = Path(os.environ.get(
    "SESSIONS_DB_PATH",
    "/home/vault-reporter/sessions.db",
))

# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

@contextmanager
def db():
    """Yield a sqlite connection with row_factory set and commits on exit."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type TEXT NOT NULL,
    report_date TEXT NOT NULL,
    report_path TEXT NOT NULL,
    questions_json TEXT NOT NULL,
    answers_json TEXT NOT NULL DEFAULT '[]',
    messages_json TEXT NOT NULL DEFAULT '{}',
    current_q_idx INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'awaiting',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_status ON sessions(status);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date TEXT NOT NULL UNIQUE,
    messages_json TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '',
    unsummarised_turn_count INTEGER NOT NULL DEFAULT 0,
    exchange_count INTEGER NOT NULL DEFAULT 0,
    last_compression_at TEXT NOT NULL DEFAULT '',
    compression_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

def init_db():
    """Create the table if it doesn't exist, and add messages_json column
    if migrating from an older schema."""
    with db() as conn:
        conn.executescript(CREATE_SQL)
        # Defensive migration: older databases predate messages_json.
        # PRAGMA returns rows of (cid, name, type, notnull, default, pk).
        cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        if "messages_json" not in cols:
            log.info("[sessions] migrating: adding messages_json column")
            conn.execute(
                "ALTER TABLE sessions ADD COLUMN messages_json TEXT NOT NULL DEFAULT '{}'"
            )

# Run on import — cheap, idempotent, ensures the DB is always ready
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
init_db()

# ---------------------------------------------------------------------------
# Row → dict helper
# ---------------------------------------------------------------------------

def _row_to_session(row: sqlite3.Row) -> dict:
    """Convert a sqlite row into a session dict, parsing JSON fields."""
    if row is None:
        return None
    try:
        questions = json.loads(row["questions_json"])
    except Exception:
        questions = []
    try:
        answers = json.loads(row["answers_json"])
    except Exception:
        answers = []
    try:
        messages = json.loads(row["messages_json"]) if "messages_json" in row.keys() else {}
    except Exception:
        messages = {}
    return {
        "id": row["id"],
        "report_type": row["report_type"],
        "report_date": row["report_date"],
        "report_path": row["report_path"],
        "questions": questions,
        "answers": answers,
        "messages": messages,
        "current_q_idx": row["current_q_idx"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }

# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def create_session(
    report_type: str,
    report_path: str,
    questions: list,
    messages: dict | None = None,
) -> int:
    """
    Create a new session. Marks any prior awaiting sessions as 'abandoned'
    first — only one active session at a time.
    """
    now = datetime.now().isoformat(timespec="seconds")
    today = date.today().isoformat()

    with db() as conn:
        # Abandon prior active sessions
        conn.execute(
            "UPDATE sessions SET status = 'abandoned', updated_at = ? "
            "WHERE status IN ('awaiting', 'in_progress')",
            (now,),
        )
        cur = conn.execute(
            "INSERT INTO sessions "
            "(report_type, report_date, report_path, questions_json, "
            "answers_json, messages_json, current_q_idx, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                report_type,
                today,
                report_path,
                json.dumps(questions),
                "[]",
                json.dumps(messages or {}),
                0,
                "awaiting",
                now,
                now,
            ),
        )
        return cur.lastrowid

def get_active_session() -> dict | None:
    """Return the current awaiting/in_progress session, or None."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE status IN ('awaiting', 'in_progress') "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return _row_to_session(row)

def get_current_question(session: dict) -> dict | None:
    """Return the question dict at session['current_q_idx'], or None if past end."""
    if not session:
        return None
    idx = session.get("current_q_idx", 0)
    questions = session.get("questions", [])
    if 0 <= idx < len(questions):
        return questions[idx]
    return None

def record_answer(session_id: int, answer_text: str) -> dict:
    """Record an answer for the current question and advance the index.
    Returns the updated session dict."""
    now = datetime.now().isoformat(timespec="seconds")
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Session {session_id} not found")

        answers = json.loads(row["answers_json"])
        questions = json.loads(row["questions_json"])
        idx = row["current_q_idx"]

        # Record the answer with its question metadata for write-back
        if 0 <= idx < len(questions):
            answers.append({
                "question_idx": idx,
                "question": questions[idx],
                "answer": answer_text,
                "answered_at": now,
                "skipped": False,
            })

        new_idx = idx + 1
        new_status = "complete" if new_idx >= len(questions) else "in_progress"

        conn.execute(
            "UPDATE sessions SET answers_json = ?, current_q_idx = ?, "
            "status = ?, updated_at = ? WHERE id = ?",
            (json.dumps(answers), new_idx, new_status, now, session_id),
        )

        updated = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return _row_to_session(updated)

def skip_current_question(session_id: int) -> dict:
    """Mark the current question skipped and advance. Returns updated session."""
    now = datetime.now().isoformat(timespec="seconds")
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Session {session_id} not found")

        answers = json.loads(row["answers_json"])
        questions = json.loads(row["questions_json"])
        idx = row["current_q_idx"]

        if 0 <= idx < len(questions):
            answers.append({
                "question_idx": idx,
                "question": questions[idx],
                "answer": None,
                "answered_at": now,
                "skipped": True,
            })

        new_idx = idx + 1
        new_status = "complete" if new_idx >= len(questions) else "in_progress"

        conn.execute(
            "UPDATE sessions SET answers_json = ?, current_q_idx = ?, "
            "status = ?, updated_at = ? WHERE id = ?",
            (json.dumps(answers), new_idx, new_status, now, session_id),
        )
        updated = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return _row_to_session(updated)

def close_session(session_id: int, final_status: str = "complete"):
    """Force-close a session with the given final status (cancelled, ended_early, complete)."""
    now = datetime.now().isoformat(timespec="seconds")
    with db() as conn:
        conn.execute(
            "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
            (final_status, now, session_id),
        )

def get_session(session_id: int) -> dict | None:
    """Fetch a session by id."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return _row_to_session(row)

# ---------------------------------------------------------------------------
# Chat sessions (v4 Phase 2 — within-day conversational memory)
# ---------------------------------------------------------------------------

def _uk_today() -> str:
    """Today's date in UK time as a YYYY-MM-DD string."""
    return datetime.now(UK_TZ).strftime("%Y-%m-%d")

def uk_today() -> str:
    """Public accessor for the UK chat-session date string. Callers outside
    this module (e.g. chat_handler) use this to resolve 'today'."""
    return _uk_today()

def _uk_now() -> str:
    """Current UK timestamp, seconds precision — matches the session style."""
    return datetime.now(UK_TZ).isoformat(timespec="seconds")

def _row_to_chat_session(row: sqlite3.Row) -> dict:
    """Convert a chat_sessions row into a dict, parsing the messages blob."""
    if row is None:
        return None
    try:
        messages = json.loads(row["messages_json"])
    except Exception:
        messages = []
    return {
        "id": row["id"],
        "session_date": row["session_date"],
        "messages": messages,
        "summary": row["summary"],
        "unsummarised_turn_count": row["unsummarised_turn_count"],
        "exchange_count": row["exchange_count"],
        # Defensive .keys() guard mirrors the messages_json handling above:
        # the columns ship via CREATE TABLE IF NOT EXISTS (no migration, no
        # populated table predates them), but stay tolerant of an old row.
        "last_compression_at": (
            row["last_compression_at"]
            if "last_compression_at" in row.keys() else ""
        ),
        "compression_count": (
            row["compression_count"]
            if "compression_count" in row.keys() else 0
        ),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }

def get_chat_session(session_date: str | None = None) -> dict:
    """Return the chat session for the given UK date (defaults to today).
    Creates the row with empty defaults if it doesn't exist yet."""
    session_date = session_date or _uk_today()
    now = _uk_now()
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM chat_sessions WHERE session_date = ?", (session_date,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO chat_sessions "
                "(session_date, messages_json, summary, unsummarised_turn_count, "
                "exchange_count, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_date, "[]", "", 0, 0, now, now),
            )
            row = conn.execute(
                "SELECT * FROM chat_sessions WHERE session_date = ?", (session_date,)
            ).fetchone()
    return _row_to_chat_session(row)

def append_chat_turn(
    session_date: str, role: str, content: str, ts: str
) -> dict:
    """Append a {role, content, ts} turn to the day's chat session.

    Increments unsummarised_turn_count on every turn. Increments
    exchange_count by 1 on assistant turns only — one exchange is one user
    message plus one assistant reply, so it's counted on the reply.
    Returns the updated chat session dict."""
    get_chat_session(session_date)  # ensure the row exists
    now = _uk_now()
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM chat_sessions WHERE session_date = ?", (session_date,)
        ).fetchone()
        messages = json.loads(row["messages_json"])
        messages.append({"role": role, "content": content, "ts": ts})
        unsummarised = row["unsummarised_turn_count"] + 1
        exchanges = row["exchange_count"] + (1 if role == "assistant" else 0)
        conn.execute(
            "UPDATE chat_sessions SET messages_json = ?, "
            "unsummarised_turn_count = ?, exchange_count = ?, updated_at = ? "
            "WHERE session_date = ?",
            (json.dumps(messages), unsummarised, exchanges, now, session_date),
        )
        updated = conn.execute(
            "SELECT * FROM chat_sessions WHERE session_date = ?", (session_date,)
        ).fetchone()
    return _row_to_chat_session(updated)

def _append_one(conn, session_date: str, role: str, content: str, ts: str):
    """Append a single {role, content, ts} turn to the day's row using the
    given open connection. No commit, no close — the caller owns the
    transaction. Mirrors append_chat_turn's counter rules: unsummarised +1
    every turn, exchange +1 on assistant turns only. Used to keep both turns
    of an exchange in one transaction (see append_chat_exchange)."""
    now = _uk_now()
    row = conn.execute(
        "SELECT * FROM chat_sessions WHERE session_date = ?", (session_date,)
    ).fetchone()
    messages = json.loads(row["messages_json"])
    messages.append({"role": role, "content": content, "ts": ts})
    unsummarised = row["unsummarised_turn_count"] + 1
    exchanges = row["exchange_count"] + (1 if role == "assistant" else 0)
    conn.execute(
        "UPDATE chat_sessions SET messages_json = ?, "
        "unsummarised_turn_count = ?, exchange_count = ?, updated_at = ? "
        "WHERE session_date = ?",
        (json.dumps(messages), unsummarised, exchanges, now, session_date),
    )

def append_chat_exchange(
    session_date: str, user_content: str, assistant_content: str, ts: str
) -> dict:
    """Append both turns of one exchange (user message + assistant reply) in
    a single transaction. Either both turns land or neither does.

    Both turns get the same `ts`. exchange_count increments by 1 total
    (counted on the assistant turn); unsummarised_turn_count increments by 2.
    Returns the updated chat session dict, same shape as the other helpers.

    Atomicity: a single db() connection wraps both writes. db() commits only
    on clean exit of the with-block; any exception skips the commit and the
    connection closes with the implicit transaction unrolled — so a failure
    between the two turns leaves the row untouched (no orphaned user turn)."""
    get_chat_session(session_date)  # ensure the row exists (own txn, committed)
    with db() as conn:
        _append_one(conn, session_date, "user", user_content, ts)
        _append_one(conn, session_date, "assistant", assistant_content, ts)
        updated = conn.execute(
            "SELECT * FROM chat_sessions WHERE session_date = ?", (session_date,)
        ).fetchone()
    return _row_to_chat_session(updated)

def clear_chat_session(session_date: str) -> dict:
    """Wipe the day's conversation in place — messages, summary, and both
    counters reset. The row itself is kept (not deleted) for inspection.
    Returns the updated chat session dict."""
    get_chat_session(session_date)  # ensure the row exists
    now = _uk_now()
    with db() as conn:
        conn.execute(
            "UPDATE chat_sessions SET messages_json = '[]', summary = '', "
            "unsummarised_turn_count = 0, exchange_count = 0, updated_at = ? "
            "WHERE session_date = ?",
            (now, session_date),
        )
        updated = conn.execute(
            "SELECT * FROM chat_sessions WHERE session_date = ?", (session_date,)
        ).fetchone()
    return _row_to_chat_session(updated)

def update_chat_summary(session_date: str, new_summary: str) -> dict:
    """Overwrite the rolling summary and reset unsummarised_turn_count to 0.
    Returns the updated chat session dict."""
    get_chat_session(session_date)  # ensure the row exists
    now = _uk_now()
    with db() as conn:
        conn.execute(
            "UPDATE chat_sessions SET summary = ?, unsummarised_turn_count = 0, "
            "updated_at = ? WHERE session_date = ?",
            (new_summary, now, session_date),
        )
        updated = conn.execute(
            "SELECT * FROM chat_sessions WHERE session_date = ?", (session_date,)
        ).fetchone()
    return _row_to_chat_session(updated)

def get_unsummarised_turns(session_date: str) -> list:
    """Return the last `unsummarised_turn_count` turns from the day's
    messages, in chronological order — the turns a compression pass would
    fold into the running summary. Returns a list of {role, content, ts}
    dicts (empty if there's nothing unsummarised)."""
    get_chat_session(session_date)  # ensure the row exists
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM chat_sessions WHERE session_date = ?", (session_date,)
        ).fetchone()
    try:
        messages = json.loads(row["messages_json"])
    except Exception:
        messages = []
    n = row["unsummarised_turn_count"]
    if n <= 0:
        return []
    return messages[-n:]

def apply_compression(
    session_date: str,
    new_summary: str,
    summarised_turn_count: int,
    ts: str,
) -> dict:
    """Write a fresh rolling summary after a compression pass.

    unsummarised_turn_count is decremented by summarised_turn_count rather
    than zeroed: turns that arrived while compression was running weren't in
    the summarised set, so they must stay in the unsummarised buffer for the
    next pass. Clamped at 0 defensively — turns are only ever appended
    between the read and this write, so current >= summarised in normal
    operation, but a /clear landing mid-compression could reset the count;
    the clamp keeps the buffer non-negative in that race.

    Also stamps last_compression_at, bumps compression_count, in one
    transaction. Returns the updated chat session dict."""
    get_chat_session(session_date)  # ensure the row exists
    now = _uk_now()
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM chat_sessions WHERE session_date = ?", (session_date,)
        ).fetchone()
        remaining = row["unsummarised_turn_count"] - summarised_turn_count
        if remaining < 0:
            remaining = 0
        new_count = row["compression_count"] + 1
        conn.execute(
            "UPDATE chat_sessions SET summary = ?, unsummarised_turn_count = ?, "
            "last_compression_at = ?, compression_count = ?, updated_at = ? "
            "WHERE session_date = ?",
            (new_summary, remaining, ts, new_count, now, session_date),
        )
        updated = conn.execute(
            "SELECT * FROM chat_sessions WHERE session_date = ?", (session_date,)
        ).fetchone()
    return _row_to_chat_session(updated)

# ---------------------------------------------------------------------------
# CLI inspection
# ---------------------------------------------------------------------------

def _cli():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = sys.argv[1:]
    cmd = args[0] if args else "list"

    if cmd == "init":
        init_db()
        print(f"DB ready at {DB_PATH}")

    elif cmd == "list":
        with db() as conn:
            rows = conn.execute(
                "SELECT id, report_type, report_date, status, current_q_idx, "
                "json_array_length(questions_json) AS n_q "
                "FROM sessions ORDER BY id DESC LIMIT 20"
            ).fetchall()
        for r in rows:
            print(f"#{r['id']:>3}  {r['report_type']:<8}  {r['report_date']}  "
                  f"{r['status']:<14}  Q{r['current_q_idx']}/{r['n_q']}")

    elif cmd == "active":
        s = get_active_session()
        if not s:
            print("No active session.")
        else:
            print(json.dumps({k: v for k, v in s.items() if k != "questions"}, indent=2, default=str))
            print(f"\n{len(s['questions'])} questions in this session.")
            for i, q in enumerate(s["questions"]):
                marker = ">" if i == s["current_q_idx"] else " "
                framing = (q.get("framing") or q.get("text") or "")[:80]
                print(f"{marker} Q{i+1}: {framing}")

    elif cmd == "close" and len(args) > 1:
        close_session(int(args[1]), final_status="cancelled")
        print(f"Closed session {args[1]}.")

    elif cmd == "chat_today":
        s = get_chat_session()
        print(f"Chat session — {s['session_date']}")
        print(f"Exchanges: {s['exchange_count']}")
        print(f"Unsummarised turns: {s['unsummarised_turn_count']}")
        print(f"Summary: {s['summary'] or '(no summary yet)'}")
        print()
        if not s["messages"]:
            print("(no turns yet)")
        else:
            for m in s["messages"]:
                ts = m.get("ts", "")
                role = m.get("role", "?")
                content = m.get("content", "")
                print(f"[{ts}] {role}: {content}")

    elif cmd == "chat_clear":
        clear_chat_session(_uk_today())
        print("cleared.")

    else:
        print("Usage: sessions.py [init|list|active|close <id>|"
              "chat_today|chat_clear]")

if __name__ == "__main__":
    _cli()
