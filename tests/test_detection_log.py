"""Phase 4a Step 1 — chat_detection_log table + detection-verdict logging.

unittest (matches the ANVIL build's test style; vault-reporter had no
tests/ before this — the folder is created here). SESSIONS_DB_PATH is set
to a tmp file BEFORE importing sessions: DB_PATH is derived from that env
var at module load and sessions runs DB_PATH.parent.mkdir + init_db on
import, so an attribute mock after import would be too late (and the
default path is the VPS-only /home/vault-reporter).

No real claude call, no real vault write: build_vault_index / select_files
/ _call_claude_chat / _write_checkpoint_file / _send_telegram_followup /
_extract_project_hints are patched where a test needs them.
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMPDIR = tempfile.mkdtemp(prefix="vr-detection-log-")
os.environ["SESSIONS_DB_PATH"] = str(Path(_TMPDIR) / "sessions.db")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sessions  # noqa: E402  (after env + path setup, by design)
import chat_handler  # noqa: E402


def _rows():
    with sqlite3.connect(os.environ["SESSIONS_DB_PATH"]) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(
            "SELECT * FROM chat_detection_log ORDER BY id"
        )]


def _table_exists() -> bool:
    with sqlite3.connect(os.environ["SESSIONS_DB_PATH"]) as c:
        return c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='chat_detection_log'"
        ).fetchone() is not None


def _reset_db():
    Path(os.environ["SESSIONS_DB_PATH"]).unlink(missing_ok=True)
    sessions.init_db()


class DetectionLogSchemaTests(unittest.TestCase):
    def test_init_db_creates_table_fresh(self):
        _reset_db()
        self.assertTrue(_table_exists())

    def test_init_db_idempotent_preexisting(self):
        _reset_db()
        sessions.append_detection_log(
            "2026-05-18", "2026-05-18T00:00:00+00:00", "skip", "gate:x",
            gate_fired="gate:x",
        )
        sessions.init_db()  # second run must not drop or error
        self.assertTrue(_table_exists())
        self.assertEqual(len(_rows()), 1)


class DetectionLogWriteTests(unittest.TestCase):
    def setUp(self):
        _reset_db()

    def test_skip_gate_writes_row(self):
        with mock.patch.object(chat_handler, "build_vault_index",
                               return_value=[{"path": "x"}]), \
             mock.patch.object(chat_handler, "select_files",
                               return_value=(["x.md"], "ok")):
            chat_handler.run_detection_check(
                "2026-05-18", "hi",
                "a sufficiently long assistant reply, well over forty chars",
            )
        rows = _rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["verdict"], "skip")
        self.assertEqual(rows[0]["reason"], "gate:short-user")
        self.assertEqual(rows[0]["gate_fired"], "gate:short-user")
        self.assertEqual(rows[0]["model_invoked"], 0)

    def test_checkpoint_via_model_writes_reason_model(self):
        claude_out = (
            "CHECKPOINT\nproject: proj\nwhat-changed: did the thing\n"
            "why: because it mattered\nwhats-next: next thing"
        )
        with mock.patch.object(chat_handler, "build_vault_index",
                               return_value=[{"path": "x"}]), \
             mock.patch.object(chat_handler, "select_files",
                               return_value=(["x.md"], "ok")), \
             mock.patch.object(chat_handler, "_extract_project_hints",
                               return_value=["proj"]), \
             mock.patch.object(chat_handler, "_call_claude_chat",
                               return_value=(True, claude_out)), \
             mock.patch.object(chat_handler, "_write_checkpoint_file",
                               return_value=("rel.md", "/abs.md")), \
             mock.patch.object(chat_handler, "_send_telegram_followup",
                               return_value=True):
            chat_handler.run_detection_check(
                "2026-05-18",
                "a real question about the build that is well over ten chars",
                "a substantive assistant reply that is well over forty "
                "characters and does structural work: because decided",
                chat_id=123,
            )
        rows = _rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["verdict"], "checkpoint")
        self.assertEqual(rows[0]["reason"], "model")
        self.assertEqual(rows[0]["model_invoked"], 1)
        self.assertEqual(rows[0]["model_verdict"], "checkpoint")

    def test_logging_failure_does_not_raise(self):
        # detect_checkpoint_worthy is untouched by Phase 4a — still returns
        # its tuple normally.
        self.assertEqual(
            chat_handler.detect_checkpoint_worthy("hi", "r", ["p"], "", []),
            ("skip", None, "gate:short-user"),
        )
        # A forced logging-write failure must not raise out of detection.
        with mock.patch.object(chat_handler, "build_vault_index",
                               return_value=[{"path": "x"}]), \
             mock.patch.object(chat_handler, "select_files",
                               return_value=(["x.md"], "ok")), \
             mock.patch.object(
                 sessions, "append_detection_log",
                 side_effect=RuntimeError("forced logging failure")):
            result = chat_handler.run_detection_check(
                "2026-05-18", "hi",
                "a sufficiently long assistant reply, well over forty chars",
            )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
