#!/usr/bin/env python3
"""calibration.py — v4 Phase 4a Step 2.

A read-only report over chat_detection_log (the table Phase 4a Step 1
added to sessions.db) and the checkpoints/active/ folder, for calibrating
the Phase 3 checkpoint-promotion detection.

Reads only. No INSERT/UPDATE/DELETE, no file writes, moves, or deletes.
The database is opened read-only. sessions.py is deliberately not imported
— importing it runs init_db() and a mkdir at module load, which a
read-only tool must not trigger; the path resolution is replicated here
instead.

The 60-second-window cancellation cross-check is approximate. A
chat_detection_log row stores a UTC microsecond ts; a checkpoint filename
stores Europe/London minute resolution and no source row id. The check
converts the row ts to Europe/London and matches on session date plus
minute within a two-minute tolerance. A same-minute collision is reported
as ambiguous, not guessed. Exact attribution would need a checkpoint_path
column added in a later step.
"""

import argparse
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_LONDON = ZoneInfo("Europe/London")
_ACTIVE_REL = "01-Projects/second-brain/checkpoints/active"
_CKPT_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})-.+\.md$")
_MATCH_TOLERANCE_MIN = 2


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _resolve_db(args) -> Path:
    raw = (
        args.db
        or os.environ.get("SESSIONS_DB_PATH")
        or "~/Downloads/vault-reporter/sessions.db"
    )
    return Path(os.path.expanduser(raw))


def _resolve_vault(args) -> Path:
    # Veronica convention: VAULT_PATH env, else ~/vaults/second-brain
    # (chat_handler.py:51, main.py:91 set this precedent — there is no
    # dotenv loader; systemd supplies the env on the VPS). Decision #16.
    raw = (
        args.vault
        or os.environ.get("VAULT_PATH")
        or "~/vaults/second-brain"
    )
    return Path(os.path.expanduser(raw))


def _open_ro(db_path: Path):
    """Read-only connection, or None if the file is absent."""
    if not db_path.is_file():
        return None
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _has_table(conn) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='chat_detection_log'"
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Report sections (pure: each returns a list of lines)
# ---------------------------------------------------------------------------

def section_header(window, cutoff_iso, rows, db_path, db_ok, table_ok,
                    vault_path, active_dir, active_ok) -> list[str]:
    if rows:
        first = rows[0]["ts"]
        last = rows[-1]["ts"]
        rng = f"{first} .. {last}"
    else:
        rng = "(no rows in window)"
    db_note = "" if db_ok else "  — not found"
    tbl_note = "" if (not db_ok or table_ok) else "  — chat_detection_log absent (has Step 1 shipped and init_db run?)"
    act_note = "" if active_ok else "  — not found"
    return [
        f"Veronica detection-log calibration — window {window} days",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Database: {db_path}{db_note}{tbl_note}",
        f"Vault: {vault_path}",
        f"Checkpoints: {active_dir}{act_note}",
        f"Cutoff: rows with ts after {cutoff_iso}",
        f"Rows in window: {len(rows)}    range: {rng}",
        "",
        "The cancellation cross-check is approximate. The row ts is UTC at "
        "microsecond resolution; the checkpoint filename is Europe/London "
        "at minute resolution with no stored row id. Rows are converted to "
        "Europe/London and matched on session date plus minute within a "
        "two-minute tolerance. A same-minute collision is reported as "
        "ambiguous, not guessed. Exact attribution needs a checkpoint_path "
        "column in a later step.",
    ]


def section_verdict_totals(rows) -> list[str]:
    c = Counter(r["verdict"] for r in rows)
    s, ck, e = c.get("skip", 0), c.get("checkpoint", 0), c.get("error", 0)
    out = [
        "Verdict totals",
        f"  skip:       {s}",
        f"  checkpoint: {ck}",
        f"  error:      {e}",
    ]
    denom = s + ck
    if denom == 0:
        out.append("  skip-rate:  n/a (no skip or checkpoint rows in window)")
    else:
        pct = 100.0 * s / denom
        out.append(
            f"  skip-rate:  {s}/({s}+{ck}) = {pct:.1f}% "
            f"(error rows excluded from the denominator)"
        )
    return out


def section_gate_breakdown(rows) -> list[str]:
    skips = [r for r in rows if r["verdict"] == "skip"]
    out = ["Gate breakdown (skip verdicts by reason)"]
    if not skips:
        out.append("  (no skip rows in window)")
        return out
    c = Counter(r["reason"] for r in skips)
    for reason, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0])):
        out.append(f"  {reason}: {n}")
    if "gate:error-reply" not in c:
        out.append(
            "  gate:error-reply: 0 (no model-unavailable burst in window)"
        )
    return out


def section_model_invocations(rows) -> list[str]:
    invoked = [r for r in rows if r["model_invoked"] == 1]
    out = ["Model invocations"]
    if not invoked:
        out.append(
            "  the model was not invoked in this window — every row was a "
            "pre-check gate skip or carried no model verdict"
        )
        return out
    out.append(f"  invoked: {len(invoked)} of {len(rows)} rows")
    c = Counter((r["model_verdict"] or "(empty)") for r in invoked)
    for verdict, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0])):
        out.append(f"  model_verdict={verdict}: {n}")
    return out


def _parse_active(active_dir: Path) -> list[tuple[str, int]]:
    """[(YYYY-MM-DD, minute-of-day)] for each well-named active checkpoint."""
    out = []
    for p in sorted(active_dir.iterdir()):
        m = _CKPT_NAME_RE.match(p.name)
        if m:
            out.append((m.group(1), int(m.group(2)) * 60 + int(m.group(3))))
    return out


def _match_cancellations(checkpoint_rows, active_index) -> dict:
    matched = ambiguous = unmatched = 0
    for r in checkpoint_rows:
        try:
            dt = datetime.fromisoformat(r["ts"]).astimezone(_LONDON)
        except (TypeError, ValueError):
            unmatched += 1
            continue
        day = dt.strftime("%Y-%m-%d")
        mins = dt.hour * 60 + dt.minute
        cands = [
            1 for (d, mm) in active_index
            if d == day and abs(mm - mins) <= _MATCH_TOLERANCE_MIN
        ]
        if len(cands) == 0:
            unmatched += 1
        elif len(cands) == 1:
            matched += 1
        else:
            ambiguous += 1
    return {
        "total": len(checkpoint_rows),
        "matched": matched,
        "ambiguous": ambiguous,
        "unmatched": unmatched,
    }


def section_cancellations(rows, active_dir, active_ok) -> list[str]:
    out = ["60-second-window cancellations (approximate)"]
    ckpts = [r for r in rows if r["verdict"] == "checkpoint"]
    if not active_ok:
        out.append(
            f"  skipped: checkpoints/active not found at {active_dir}; "
            f"pass --vault or set VAULT_PATH"
        )
        out.append(f"  model checkpoint rows in window: {len(ckpts)}")
        return out
    if not ckpts:
        out.append("  (no model checkpoint rows in window)")
        return out
    res = _match_cancellations(ckpts, _parse_active(active_dir))
    out += [
        f"  model checkpoints: {res['total']}",
        f"  surviving in active/: {res['matched']}",
        f"  dropped inside window (no surviving file): {res['unmatched']}",
        f"  ambiguous (same-minute collision or multiple candidates): "
        f"{res['ambiguous']}",
    ]
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_report(args) -> tuple[list[str], int]:
    db_path = _resolve_db(args)
    vault_path = _resolve_vault(args)
    active_dir = vault_path / _ACTIVE_REL

    conn = _open_ro(db_path)
    db_ok = conn is not None
    table_ok = bool(conn) and _has_table(conn)
    active_ok = active_dir.is_dir()

    window = args.window
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window)).isoformat()

    rows: list = []
    if db_ok and table_ok:
        conn.row_factory = sqlite3.Row
        rows = list(conn.execute(
            "SELECT session_date, ts, verdict, reason, gate_fired, "
            "model_invoked, model_verdict FROM chat_detection_log "
            "WHERE ts > ? ORDER BY ts",
            (cutoff,),
        ))
    if conn is not None:
        conn.close()

    lines: list[str] = []
    lines += section_header(window, cutoff, rows, db_path, db_ok,
                            table_ok, vault_path, active_dir, active_ok)
    lines.append("")
    lines += section_verdict_totals(rows)
    lines.append("")
    lines += section_gate_breakdown(rows)
    lines.append("")
    lines += section_model_invocations(rows)
    lines.append("")
    lines += section_cancellations(rows, active_dir, active_ok)

    # A real (non-dry-run) invocation against a missing DB/table is a
    # failure. --dry-run is the side-effect-free wiring assertion and an
    # empty corpus is a valid state, so it exits 0 regardless (the corpus
    # lives on the VPS, not the build machine).
    if args.dry_run:
        code = 0
    else:
        code = 0 if (db_ok and table_ok) else 1
    return lines, code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="calibration",
        description="Read-only detection-log calibration report.",
    )
    parser.add_argument("--window", type=int, default=7,
                        help="day window, default 7")
    parser.add_argument("--dry-run", action="store_true",
                        help="side-effect-free wiring assertion; exits 0")
    parser.add_argument("--db", default=None,
                        help="override sessions.db path")
    parser.add_argument("--vault", default=None,
                        help="override vault root (for checkpoints/active)")
    args = parser.parse_args(argv)

    try:
        lines, code = build_report(args)
    except Exception as e:  # never traceback; a real crash fails the smoke
        print(f"calibration: {e}", file=sys.stderr)
        return 1

    print("\n".join(lines))
    if args.dry_run:
        print(
            "\nno side effects: reads only — no INSERT/UPDATE/DELETE, "
            "no file writes/moves/deletes"
        )
        return 0
    return code


if __name__ == "__main__":
    sys.exit(main())
