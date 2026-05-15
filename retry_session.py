#!/usr/bin/env python3
"""
retry_session.py
One-off helper to re-apply answers from the most recent completed session.
Useful when vault_writeback was updated and a failed answer should now succeed.

Usage:
    python3 retry_session.py            # most recent completed session
    python3 retry_session.py <id>       # specific session id
"""

import sys
import logging
from sessions import list_recent, db
from vault_writeback import write_session_to_vault
import json

logging.basicConfig(level=logging.INFO)

def main():
    if len(sys.argv) > 1:
        session_id = int(sys.argv[1])
        with db() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if not row:
                print(f"Session {session_id} not found.")
                sys.exit(1)
            session = {
                "id": row["id"],
                "report_type": row["report_type"],
                "report_date": row["report_date"],
                "answers": json.loads(row["answers_json"]),
                "status": row["status"],
            }
    else:
        sessions = list_recent(limit=10)
        completed = [s for s in sessions if s["status"] in ("complete", "ended_early")]
        if not completed:
            print("No completed sessions found.")
            sys.exit(0)
        session = completed[0]

    print(f"Retrying session #{session['id']} ({session['report_type']} {session.get('report_date', '')}) "
          f"with {len(session['answers'])} answers")
    for i, a in enumerate(session['answers'], 1):
        vw = a.get('vault_write') or {}
        print(f"  A{i}: mode={vw.get('mode')} file={vw.get('file')} section={vw.get('section')}")

    summary = write_session_to_vault(session)
    print(f"\nDone: {summary}")


if __name__ == "__main__":
    main()
