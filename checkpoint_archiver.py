#!/usr/bin/env python3
"""
checkpoint_archiver.py

Manages the lifecycle of checkpoint notes:

- read_active_checkpoints(window_start, window_end)
    Read checkpoints in `active/` whose date falls in the window.
    Returns structured dicts for context_builder to pass into the prompt.

- mark_for_archive(checkpoint_paths, daily_report_path)
    Set the `archive_after` and `archived_by` frontmatter fields on each
    checkpoint after a daily report has integrated them. Buffer is
    ARCHIVE_BUFFER_DAYS from now.

- sweep_archive()
    First step of every daily run. Walk active/, find any checkpoint
    whose archive_after timestamp is in the past, move it to
    archive/YYYY-MM/.

The lifecycle (daily run perspective):

    1. sweep_archive()          ← runs first; moves eligible checkpoints out
    2. build_context()          ← reads active/ (post-sweep); integrates the rest
    3. claude --print           ← generates report referencing those checkpoints
    4. write + commit + push    ← report goes to vault
    5. mark_for_archive()       ← marks the read checkpoints as eligible for sweep
                                  next time (in ARCHIVE_BUFFER_DAYS)

Failure isolation: if any of steps 2-4 fail, step 5 never runs, so the
checkpoints stay un-marked. The next successful daily picks them up again.
No data loss from transient failure.
"""

import os
import re
import shutil
import logging
import subprocess
from pathlib import Path
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VAULT_PATH = Path(os.path.expanduser(
    os.environ.get("VAULT_PATH", "~/vaults/second-brain")
))

CHECKPOINTS_ROOT = VAULT_PATH / "01-Projects" / "second-brain" / "checkpoints"
ACTIVE_DIR = CHECKPOINTS_ROOT / "active"
ARCHIVE_DIR = CHECKPOINTS_ROOT / "archive"

# Phase 3 safety: 7-day buffer for the first two weeks. Tighten to 2 after
# the daily prompt is confirmed producing good narrative consistently.
ARCHIVE_BUFFER_DAYS = int(os.environ.get("CHECKPOINT_ARCHIVE_BUFFER_DAYS", "7"))

# Cap how much of each checkpoint body the AI sees, to keep prompt size sane
# even if a single checkpoint is unusually long
MAX_CHECKPOINT_CHARS = 2000


# ---------------------------------------------------------------------------
# Frontmatter parsing — yaml-light, no external deps
# ---------------------------------------------------------------------------

def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """
    Split a markdown file into (frontmatter_dict, body).
    Frontmatter must be at the top, between two '---' delimiters.
    Returns ({}, content) if no frontmatter present.

    Handles:
    - Scalar strings, ints, booleans, nulls
    - YAML-style lists (- item per line, indented)
    - Quoted strings (single or double)
    """
    if not content.startswith("---"):
        return {}, content

    end = content.find("\n---", 3)
    if end == -1:
        return {}, content

    yaml_block = content[3:end].strip()
    body = content[end + 4:].lstrip("\n")

    fm = {}
    current_list_key = None
    for raw_line in yaml_block.splitlines():
        # List continuation: "  - item" (with leading whitespace + dash)
        list_match = re.match(r"^\s+-\s+(.*)$", raw_line)
        if list_match and current_list_key is not None:
            item = list_match.group(1).strip().strip('"').strip("'")
            fm[current_list_key].append(item)
            continue

        # Regular key: value line
        kv_match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", raw_line)
        if kv_match:
            key = kv_match.group(1).strip()
            raw_value = kv_match.group(2).strip()

            if raw_value == "":
                # Could be a list header or an empty scalar — assume list,
                # convert later if no list items follow
                fm[key] = []
                current_list_key = key
            else:
                current_list_key = None
                fm[key] = _coerce_scalar(raw_value)
        else:
            current_list_key = None

    # Any key that ended up as an empty list AND has no items is actually null
    for k, v in list(fm.items()):
        if v == [] and k not in ("files_touched", "tags"):
            fm[k] = None

    return fm, body


def _coerce_scalar(raw: str):
    """Convert a YAML scalar string to its Python equivalent."""
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1]
    if raw.lower() in ("null", "~", ""):
        return None
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    # Try int
    try:
        return int(raw)
    except ValueError:
        pass
    return raw


def _render_frontmatter(fm: dict) -> str:
    """Render a frontmatter dict back to YAML-style block (no `---` wrappers).
    Preserves list formatting for known list fields."""
    lines = []
    for key, value in fm.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        elif value is None:
            lines.append(f"{key}: null")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        else:
            # Strings — only quote if needed (contains special chars)
            sval = str(value)
            if any(c in sval for c in ":#") or sval.strip() != sval:
                lines.append(f'{key}: "{sval}"')
            else:
                lines.append(f"{key}: {sval}")
    return "\n".join(lines)


def _write_with_frontmatter(path: Path, fm: dict, body: str):
    """Write a markdown file with frontmatter at top + body."""
    content = "---\n" + _render_frontmatter(fm) + "\n---\n" + body
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Body parsing — extract the three sections (What changed / Why / What's next)
# ---------------------------------------------------------------------------

def _parse_checkpoint_body(body: str) -> dict:
    """Extract the three required sections from a checkpoint body.
    Returns a dict with keys: title, what_changed, why, whats_next."""
    out = {"title": "", "what_changed": "", "why": "", "whats_next": ""}

    # H1 title
    h1 = re.search(r"^# (.+)$", body, re.MULTILINE)
    if h1:
        out["title"] = h1.group(1).strip()

    # H2 sections — capture each chunk until the next H2 or end-of-string
    section_pattern = re.compile(
        r"^## (What changed|Why|What's next)\s*\n(.*?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    section_key_map = {
        "What changed": "what_changed",
        "Why": "why",
        "What's next": "whats_next",
    }
    for match in section_pattern.finditer(body):
        section_name = match.group(1)
        section_content = match.group(2).strip()
        out[section_key_map[section_name]] = section_content

    return out


# ---------------------------------------------------------------------------
# Public API — read_active_checkpoints
# ---------------------------------------------------------------------------

def read_active_checkpoints(window_start: str, window_end: str = None) -> list[dict]:
    """
    Read all checkpoint notes in active/ whose `date` frontmatter falls
    within the window [window_start, window_end].

    Window bounds are ISO datetime strings (matches what context_builder uses).

    Returns a list of dicts, sorted by date+time ascending:
        [{path, date, time, project, source, files_touched,
          title, what_changed, why, whats_next}, ...]
    """
    if not ACTIVE_DIR.exists():
        log.info(f"[archiver] active/ directory does not exist yet: {ACTIVE_DIR}")
        return []

    since_date = datetime.fromisoformat(window_start[:10]).date()
    until_date = (
        datetime.fromisoformat(window_end[:10]).date()
        if window_end else datetime.now().date()
    )

    checkpoints = []
    for cp_file in sorted(ACTIVE_DIR.iterdir()):
        if not cp_file.suffix == ".md":
            continue
        # Skip the .gitkeep.md placeholder
        if cp_file.name.startswith("."):
            continue

        try:
            content = cp_file.read_text(encoding="utf-8")
        except Exception as e:
            log.warning(f"[archiver] failed to read {cp_file.name}: {e}")
            continue

        fm, body = _parse_frontmatter(content)

        # Check type — only process actual checkpoint files
        if fm.get("type") != "checkpoint":
            continue

        # Window filter — checkpoint's date must fall within [since, until]
        cp_date_str = fm.get("date")
        if not cp_date_str:
            log.warning(f"[archiver] {cp_file.name} has no date in frontmatter; skipping")
            continue
        try:
            cp_date = datetime.strptime(str(cp_date_str)[:10], "%Y-%m-%d").date()
        except ValueError:
            log.warning(f"[archiver] {cp_file.name} has invalid date {cp_date_str}; skipping")
            continue

        if not (since_date <= cp_date <= until_date):
            continue

        sections = _parse_checkpoint_body(body)

        # Cap the body sections at MAX_CHECKPOINT_CHARS in case a checkpoint
        # is unusually long
        def cap(text):
            if len(text) > MAX_CHECKPOINT_CHARS:
                return text[:MAX_CHECKPOINT_CHARS] + "...[truncated]"
            return text

        checkpoints.append({
            "path": str(cp_file.relative_to(VAULT_PATH)),
            "date": str(cp_date),
            "time": str(fm.get("time", "")),
            "project": str(fm.get("project", "")),
            "source": str(fm.get("source", "")),
            "files_touched": fm.get("files_touched", []) or [],
            "title": sections["title"],
            "what_changed": cap(sections["what_changed"]),
            "why": cap(sections["why"]),
            "whats_next": cap(sections["whats_next"]),
        })

    # Sort: by date+time ascending so the daily reads chronologically
    checkpoints.sort(key=lambda c: (c["date"], c["time"]))

    log.info(f"[archiver] read {len(checkpoints)} active checkpoints in window")
    return checkpoints


# ---------------------------------------------------------------------------
# Public API — mark_for_archive
# ---------------------------------------------------------------------------

def mark_for_archive(checkpoint_paths: list[str], daily_report_path: str):
    """
    For each checkpoint integrated into a daily report, set:
        archive_after: <now + ARCHIVE_BUFFER_DAYS>
        archived_by: <daily_report_path>

    Called as the LAST step of a successful daily run.

    `checkpoint_paths` are vault-relative paths (the same ones returned by
    read_active_checkpoints).
    """
    if not checkpoint_paths:
        log.info("[archiver] mark_for_archive: no checkpoints to mark")
        return

    archive_at = (datetime.now() + timedelta(days=ARCHIVE_BUFFER_DAYS)).isoformat(timespec="seconds")

    marked = 0
    for rel_path in checkpoint_paths:
        cp_file = VAULT_PATH / rel_path
        if not cp_file.exists():
            log.warning(f"[archiver] mark_for_archive: {rel_path} not found, skipping")
            continue

        try:
            content = cp_file.read_text(encoding="utf-8")
            fm, body = _parse_frontmatter(content)

            # Skip if already marked — shouldn't happen but defensive
            if fm.get("archive_after"):
                log.info(f"[archiver] {rel_path} already marked; skipping")
                continue

            fm["archive_after"] = archive_at
            fm["archived_by"] = daily_report_path
            _write_with_frontmatter(cp_file, fm, body)
            marked += 1
        except Exception as e:
            log.error(f"[archiver] failed to mark {rel_path}: {e}")

    log.info(
        f"[archiver] marked {marked}/{len(checkpoint_paths)} checkpoints; "
        f"will be archived after {archive_at}"
    )

    # Git commit the metadata changes so they survive across runs and
    # propagate to GitHub
    if marked > 0:
        _git_commit_archive_marks(marked, daily_report_path)


def _git_commit_archive_marks(count: int, daily_report_path: str):
    """Commit the metadata-only changes from mark_for_archive."""
    try:
        subprocess.run(
            ["git", "-C", str(VAULT_PATH), "add",
             "01-Projects/second-brain/checkpoints/active/"],
            check=True, capture_output=True,
        )
        msg = f"vault: marked {count} checkpoint(s) for archive after {daily_report_path}"
        result = subprocess.run(
            ["git", "-C", str(VAULT_PATH), "commit", "-m", msg],
            capture_output=True, text=True,
        )
        if result.returncode != 0 and "nothing to commit" not in result.stdout:
            log.warning(f"[archiver] commit returned {result.returncode}: {result.stdout}")
            return
        push = subprocess.run(
            ["git", "-C", str(VAULT_PATH), "push"],
            capture_output=True, text=True,
        )
        if push.returncode != 0:
            log.error(f"[archiver] git push failed: {push.stderr}")
        else:
            log.info(f"[archiver] pushed mark-for-archive commit")
    except subprocess.CalledProcessError as e:
        log.error(f"[archiver] git op failed during mark-for-archive commit: {e}")


# ---------------------------------------------------------------------------
# Public API — sweep_archive
# ---------------------------------------------------------------------------

def sweep_archive() -> dict:
    """
    First step of every daily run.

    Walk active/, find any checkpoint whose archive_after timestamp is in
    the past, move it to archive/YYYY-MM/. Commit the moves.

    Returns a summary dict for logging:
        {moved: int, errors: int, files: [list of moved paths]}
    """
    summary = {"moved": 0, "errors": 0, "files": []}

    if not ACTIVE_DIR.exists():
        log.info("[archiver] sweep_archive: active/ does not exist; nothing to sweep")
        return summary

    now = datetime.now()
    for cp_file in sorted(ACTIVE_DIR.iterdir()):
        if not cp_file.suffix == ".md":
            continue
        if cp_file.name.startswith("."):
            continue

        try:
            content = cp_file.read_text(encoding="utf-8")
            fm, body = _parse_frontmatter(content)

            if fm.get("type") != "checkpoint":
                continue

            archive_after_str = fm.get("archive_after")
            if not archive_after_str:
                # Not yet marked; leave alone
                continue

            try:
                archive_after = datetime.fromisoformat(str(archive_after_str))
            except ValueError:
                log.warning(
                    f"[archiver] {cp_file.name} has invalid archive_after "
                    f"{archive_after_str}; leaving in active/"
                )
                continue

            if archive_after > now:
                # Not yet eligible; leave alone
                continue

            # Eligible — move to archive/YYYY-MM/
            cp_date_str = str(fm.get("date", ""))[:7]  # YYYY-MM
            if not re.match(r"^\d{4}-\d{2}$", cp_date_str):
                log.warning(
                    f"[archiver] {cp_file.name} has unparseable date "
                    f"{fm.get('date')}; archiving to fallback bucket"
                )
                cp_date_str = now.strftime("%Y-%m")

            target_month_dir = ARCHIVE_DIR / cp_date_str
            target_month_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_month_dir / cp_file.name

            shutil.move(str(cp_file), str(target_path))
            summary["moved"] += 1
            summary["files"].append(str(target_path.relative_to(VAULT_PATH)))
            log.info(f"[archiver] moved {cp_file.name} -> archive/{cp_date_str}/")
        except Exception as e:
            log.error(f"[archiver] failed to sweep {cp_file.name}: {e}")
            summary["errors"] += 1

    # Git commit the moves
    if summary["moved"] > 0:
        _git_commit_sweep(summary)

    return summary


def _git_commit_sweep(summary: dict):
    """Commit the file moves from sweep_archive."""
    try:
        subprocess.run(
            ["git", "-C", str(VAULT_PATH), "add",
             "01-Projects/second-brain/checkpoints/"],
            check=True, capture_output=True,
        )
        msg = f"vault: archived {summary['moved']} checkpoint(s) past their buffer"
        result = subprocess.run(
            ["git", "-C", str(VAULT_PATH), "commit", "-m", msg],
            capture_output=True, text=True,
        )
        if result.returncode != 0 and "nothing to commit" not in result.stdout:
            log.warning(f"[archiver] sweep commit returned {result.returncode}: {result.stdout}")
            return
        push = subprocess.run(
            ["git", "-C", str(VAULT_PATH), "push"],
            capture_output=True, text=True,
        )
        if push.returncode != 0:
            log.error(f"[archiver] sweep push failed: {push.stderr}")
        else:
            log.info(f"[archiver] pushed sweep commit")
    except subprocess.CalledProcessError as e:
        log.error(f"[archiver] git op failed during sweep commit: {e}")


# ---------------------------------------------------------------------------
# CLI — for manual inspection / testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"

    if cmd == "list":
        # Show what's in active/
        if not ACTIVE_DIR.exists():
            print(f"active/ does not exist yet: {ACTIVE_DIR}")
            sys.exit(0)
        print(f"=== Active checkpoints ({ACTIVE_DIR}) ===")
        for cp_file in sorted(ACTIVE_DIR.iterdir()):
            if cp_file.suffix != ".md" or cp_file.name.startswith("."):
                continue
            content = cp_file.read_text()
            fm, _ = _parse_frontmatter(content)
            marker = "★" if fm.get("archive_after") else " "
            print(
                f"{marker} {cp_file.name} "
                f"[{fm.get('date', '?')} {fm.get('time', '?')}] "
                f"project={fm.get('project', '?')} "
                f"archive_after={fm.get('archive_after', 'unset')}"
            )

    elif cmd == "read":
        # Test the window-based reader. Uses last 30 days.
        from datetime import datetime, timedelta
        since = (datetime.now() - timedelta(days=30)).isoformat()
        cps = read_active_checkpoints(since)
        import json
        print(json.dumps(cps, indent=2, default=str))

    elif cmd == "sweep":
        # Run the sweep — actually moves files
        result = sweep_archive()
        print(f"Sweep complete: {result}")

    elif cmd == "dry-sweep":
        # Show what would be swept without moving anything
        if not ACTIVE_DIR.exists():
            print("active/ does not exist")
            sys.exit(0)
        now = datetime.now()
        eligible = []
        for cp_file in sorted(ACTIVE_DIR.iterdir()):
            if cp_file.suffix != ".md" or cp_file.name.startswith("."):
                continue
            content = cp_file.read_text()
            fm, _ = _parse_frontmatter(content)
            archive_after_str = fm.get("archive_after")
            if not archive_after_str:
                continue
            try:
                archive_after = datetime.fromisoformat(str(archive_after_str))
                if archive_after <= now:
                    eligible.append((cp_file.name, archive_after_str))
            except ValueError:
                pass
        print(f"=== Would sweep {len(eligible)} checkpoint(s) ===")
        for name, ts in eligible:
            print(f"  {name} (archive_after: {ts})")

    else:
        print("Usage: checkpoint_archiver.py [list | read | sweep | dry-sweep]")
        sys.exit(1)
