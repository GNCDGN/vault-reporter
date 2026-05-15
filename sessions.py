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
"""

import os
import sqlite3
import json
import logging
from contextlib import contextmanager
from datetime import datetime, date
from pathlib import Path

log = logging.getLogger(__name__)

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

    else:
        print("Usage: sessions.py [init|list|active|close <id>]")

if __name__ == "__main__":
    _cli()
