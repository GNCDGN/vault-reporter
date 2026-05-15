#!/usr/bin/env python3
"""
context_builder.py
Reads the Obsidian vault from disk, computes what changed since the last
report, and builds a structured context block for the report generator.
"""

import os
import json
import subprocess
import re
from datetime import datetime, timedelta
from pathlib import Path


# ── Configuration ────────────────────────────────────────────────────────────

VAULT_PATH = Path(os.path.expanduser(os.environ.get("VAULT_PATH", "~/vaults/second-brain")))
STATE_FILE = Path(os.environ.get("STATE_FILE", os.path.expanduser("~/.vault-reporter-state.json")))

# Folders to scan for projects (discovers subfolders automatically)
PROJECT_ROOTS = [
    "01-Projects",
    "12-Redriff/projects",
]

# Files at these paths treated as special Redriff area content (not projects)
REDRIFF_AREA_FILES = [
    "12-Redriff/README.md",
    "12-Redriff/business-overview.md",
]

# Folders to skip when scanning for projects
SKIP_FOLDERS = {".project-template", ".git", "_attachments", ".obsidian"}

# Daily notes folder
DAILY_NOTES_FOLDER = "05-Daily"

# Where reports are stored (for reading previous reports)
REPORT_LOCATIONS = {
    "daily":   "today.md",
    "weekly":  "06-Reviews/weekly",
    "monthly": "06-Reviews/monthly",
}


# ── State management ──────────────────────────────────────────────────────────

def load_state() -> dict:
    """Load the last-run timestamps for each report type."""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    # Default: treat everything as new (never run before)
    return {
        "last_daily":   None,
        "last_weekly":  None,
        "last_monthly": None,
    }


def save_state(state: dict):
    """Save updated timestamps after a successful run."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def get_window_start(report_type: str, state: dict) -> str:
    """
    Return the ISO datetime string for the start of this report's window.
    Falls back to a sensible default if never run before.
    """
    key = f"last_{report_type}"
    if state.get(key):
        return state[key]
    # Defaults if never run
    now = datetime.now()
    defaults = {
        "daily":   (now - timedelta(days=1)).isoformat(),
        "weekly":  (now - timedelta(days=7)).isoformat(),
        "monthly": (now - timedelta(days=30)).isoformat(),
    }
    return defaults[report_type]


# ── Git helpers ───────────────────────────────────────────────────────────────

def git(args: list, cwd=None) -> str:
    """Run a git command in the vault directory and return stdout."""
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd or VAULT_PATH,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def get_git_log(since: str) -> list[dict]:
    """
    Return all commits since `since` (ISO datetime string).
    Each entry: hash, timestamp, message, files_changed, insertions, deletions.
    """
    # Get commits with stats
    log_output = git([
        "log",
        f"--since={since}",
        "--name-only",
        "--format=COMMIT|%H|%ai|%s",
        "--",
        ".",
    ])

    commits = []
    current = None

    for line in log_output.splitlines():
        if line.startswith("COMMIT|"):
            if current:
                commits.append(current)
            parts = line.split("|", 3)
            current = {
                "hash": parts[1],
                "timestamp": parts[2],
                "message": parts[3] if len(parts) > 3 else "",
                "files_changed": [],
            }
        elif line.strip() and current and not line.startswith("COMMIT|"):
            current["files_changed"].append(line.strip())

    if current:
        commits.append(current)

    return commits


def get_new_files(since: str) -> list[str]:
    """Return paths of files created since `since`."""
    output = git([
        "log",
        f"--since={since}",
        "--diff-filter=A",
        "--name-only",
        "--format=",
    ])
    return [line.strip() for line in output.splitlines() if line.strip()]


def get_modified_files(since: str) -> list[str]:
    """Return paths of files modified (not created) since `since`."""
    output = git([
        "log",
        f"--since={since}",
        "--diff-filter=M",
        "--name-only",
        "--format=",
    ])
    return list({line.strip() for line in output.splitlines() if line.strip()})


# ── Vault file readers ────────────────────────────────────────────────────────

def read_file(relative_path: str) -> str | None:
    """Read a vault file. Returns None if not found."""
    full_path = VAULT_PATH / relative_path
    if full_path.exists():
        return full_path.read_text(encoding="utf-8")
    return None


def parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from a markdown file."""
    if not content.startswith("---"):
        return {}
    end = content.find("---", 3)
    if end == -1:
        return {}
    yaml_block = content[3:end].strip()
    result = {}
    for line in yaml_block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def extract_decisions_since(decisions_content: str, since: str) -> list[dict]:
    """
    Extract decision entries from a decisions.md file that were added
    on or after `since`. Looks for ## YYYY-MM-DD headings.
    """
    if not decisions_content:
        return []

    since_dt = datetime.fromisoformat(since[:10])
    decisions = []
    current = None

    for line in decisions_content.splitlines():
        # Match ## YYYY-MM-DD — Title format
        match = re.match(r"^## (\d{4}-\d{2}-\d{2})[^\n]*", line)
        if match:
            if current:
                decisions.append(current)
            decision_date = datetime.strptime(match.group(1), "%Y-%m-%d")
            if decision_date >= since_dt:
                current = {
                    "date": match.group(1),
                    "title": line.lstrip("#").strip(),
                    "body": "",
                }
            else:
                current = None  # Too old
        elif current and line.strip():
            current["body"] += line + "\n"

    if current:
        decisions.append(current)

    return decisions


# ── Project discovery ─────────────────────────────────────────────────────────

def discover_projects() -> list[dict]:
    """
    Auto-discover all project folders under PROJECT_ROOTS.
    Returns one dict per project with path and name.
    """
    projects = []
    for root in PROJECT_ROOTS:
        root_path = VAULT_PATH / root
        if not root_path.exists():
            continue
        for folder in sorted(root_path.iterdir()):
            if not folder.is_dir():
                continue
            if folder.name in SKIP_FOLDERS or folder.name.startswith("."):
                continue
            readme = folder / "README.md"
            projects.append({
                "name": folder.name,
                "path": str(folder.relative_to(VAULT_PATH)),
                "has_readme": readme.exists(),
            })
    return projects


def build_project_state(project: dict, since: str, new_files: list, modified_files: list) -> dict:
    """
    Build a complete state dict for one project.
    """
    project_path = project["path"]

    # Read README frontmatter
    readme_content = read_file(f"{project_path}/README.md")
    frontmatter = parse_frontmatter(readme_content) if readme_content else {}

    # Find last touched file
    all_project_files = [
        f for f in (new_files + modified_files)
        if f.startswith(project_path)
    ]

    # Read decisions.md for new entries
    decisions_content = read_file(f"{project_path}/decisions.md")
    new_decisions = extract_decisions_since(decisions_content or "", since)

    # Get last modification time via git
    last_commit_output = git([
        "log", "-1", "--format=%ai", "--", project_path
    ])
    last_touched = last_commit_output[:10] if last_commit_output else "unknown"

    days_since = None
    if last_touched != "unknown":
        try:
            delta = datetime.now() - datetime.strptime(last_touched, "%Y-%m-%d")
            days_since = delta.days
        except ValueError:
            pass

    # Separate new vs modified files for this project
    project_new = [f for f in new_files if f.startswith(project_path)]
    project_modified = [f for f in modified_files if f.startswith(project_path)]

    # Read content of changed files (skip large files > 50KB)
    changed_contents = {}
    for filepath in (project_new + project_modified):
        full = VAULT_PATH / filepath
        if full.exists() and full.stat().st_size < 50_000:
            # Skip daily notes — we handle those separately
            if DAILY_NOTES_FOLDER not in filepath:
                changed_contents[filepath] = full.read_text(encoding="utf-8")

    return {
        "name": project["name"],
        "path": project_path,
        "readme_frontmatter": frontmatter,
        "files_created_in_window": project_new,
        "files_modified_in_window": project_modified,
        "decisions_new": new_decisions,
        "last_touched": last_touched,
        "days_since_last_touch": days_since,
        "changed_file_contents": changed_contents,
    }


# ── Daily notes ───────────────────────────────────────────────────────────────

def read_daily_notes(since: str, until: str = None) -> list[dict]:
    """
    Read daily notes in the window [since, until].
    Extracts frontmatter fields and key sections.
    """
    since_date = datetime.fromisoformat(since[:10]).date()
    until_date = datetime.fromisoformat(until[:10]).date() if until else datetime.now().date()

    notes = []
    daily_path = VAULT_PATH / DAILY_NOTES_FOLDER

    if not daily_path.exists():
        return notes

    for note_file in sorted(daily_path.iterdir()):
        if not note_file.suffix == ".md":
            continue
        try:
            note_date = datetime.strptime(note_file.stem, "%Y-%m-%d").date()
        except ValueError:
            continue

        if not (since_date <= note_date <= until_date):
            continue

        content = note_file.read_text(encoding="utf-8")
        fm = parse_frontmatter(content)

        # Extract section content
        sections = {}
        current_section = None
        for line in content.splitlines():
            if line.startswith("## "):
                current_section = line.lstrip("#").strip()
                sections[current_section] = []
            elif current_section:
                sections[current_section].append(line)

        # Clean up sections
        sections = {
            k: "\n".join(v).strip()
            for k, v in sections.items()
            if "\n".join(v).strip()
        }

        notes.append({
            "date": str(note_date),
            "spanish_minutes": _parse_int(fm.get("spanish_minutes")),
            "spanish_focus": fm.get("spanish_focus", ""),
            "spanish_phase": _parse_int(fm.get("spanish_phase")),
            "top_3_priorities": sections.get("Top 3 priorities", ""),
            "decisions_made": sections.get("Decisions made", ""),
            "captures": sections.get("Captures", ""),
            "notes": sections.get("Notes", ""),
        })

    return notes


def _parse_int(value) -> int | None:
    try:
        v = int(value)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


# ── Spanish metrics ───────────────────────────────────────────────────────────

def compute_spanish_metrics(daily_notes: list[dict], report_type: str) -> dict:
    """Rollup Spanish metrics from daily notes in the window."""
    sessions = [n for n in daily_notes if n.get("spanish_minutes")]
    total_minutes = sum(n["spanish_minutes"] for n in sessions)
    total_hours = round(total_minutes / 60, 1)

    # Target hours per window
    targets = {"daily": 1.0, "weekly": 7.0, "monthly": 30.0}
    target = targets.get(report_type, 7.0)
    gap = round(total_hours - target, 1)

    # All-time total from state (approximated from most recent daily notes)
    # In a full run, you'd query all daily notes; here we note it's window-only
    return {
        "minutes_in_window": total_minutes,
        "hours_in_window": total_hours,
        "target_hours": target,
        "gap": gap,
        "sessions_in_window": len(sessions),
        "on_target": gap >= 0,
        "daily_breakdown": [
            {
                "date": n["date"],
                "minutes": n["spanish_minutes"],
                "focus": n.get("spanish_focus", ""),
            }
            for n in sessions
        ],
    }


# ── Previous report reader ────────────────────────────────────────────────────

def read_previous_report(report_type: str) -> str | None:
    """Read the most recent previous report of this type."""
    if report_type == "daily":
        return read_file("today.md")

    folder = REPORT_LOCATIONS.get(report_type)
    if not folder:
        return None

    report_dir = VAULT_PATH / folder
    if not report_dir.exists():
        return None

    # Find the most recent file (by name, which is date-based)
    files = sorted(
        [f for f in report_dir.iterdir() if f.suffix == ".md"],
        reverse=True,
    )

    # Skip the current cycle's file if it exists
    for f in files:
        # For weekly: skip if matches current week
        # For monthly: skip if matches current month
        content = f.read_text(encoding="utf-8")
        if content.strip():
            return content

    return None


# ── Main context builder ──────────────────────────────────────────────────────

def build_context(report_type: str) -> dict:
    """
    Build the full context block for a given report type.
    This is the main entry point — call this from report_generator.py.
    """
    print(f"[context_builder] Building context for: {report_type}")

    state = load_state()
    since = get_window_start(report_type, state)
    now = datetime.now().isoformat()

    print(f"[context_builder] Window: {since} → {now}")

    # Git analysis
    print("[context_builder] Analysing git log...")
    git_log = get_git_log(since)
    new_files = get_new_files(since)
    modified_files = get_modified_files(since)

    print(f"[context_builder] Found {len(git_log)} commits, "
          f"{len(new_files)} new files, {len(modified_files)} modified files")

    # Project states
    print("[context_builder] Scanning projects...")
    projects = discover_projects()
    project_states = []
    for project in projects:
        state_entry = build_project_state(project, since, new_files, modified_files)
        project_states.append(state_entry)
        activity = (
            len(state_entry["files_created_in_window"]) +
            len(state_entry["files_modified_in_window"])
        )
        print(f"[context_builder]   {project['name']}: "
              f"{activity} file changes, "
              f"last touched {state_entry['last_touched']}")

    # Redriff area files (not under projects/)
    redriff_area = {}
    for rel_path in REDRIFF_AREA_FILES:
        content = read_file(rel_path)
        if content:
            fm = parse_frontmatter(content)
            was_modified = rel_path in modified_files or rel_path in new_files
            redriff_area[rel_path] = {
                "frontmatter": fm,
                "modified_in_window": was_modified,
                "content_preview": content[:500] + "..." if len(content) > 500 else content,
            }

    # Daily notes
    print("[context_builder] Reading daily notes...")
    daily_notes = read_daily_notes(since)
    print(f"[context_builder]   Found {len(daily_notes)} daily notes in window")

    # Spanish metrics
    spanish_metrics = compute_spanish_metrics(daily_notes, report_type)
    print(f"[context_builder]   Spanish: {spanish_metrics['hours_in_window']}h "
          f"/ {spanish_metrics['target_hours']}h target")

    # Previous report
    print("[context_builder] Reading previous report...")
    previous_report = read_previous_report(report_type)
    if previous_report:
        print(f"[context_builder]   Found previous report ({len(previous_report)} chars)")
    else:
        print("[context_builder]   No previous report found")

    context = {
        "report_type": report_type,
        "generated_at": now,
        "window_start": since,
        "window_end": now,
        "git_log": git_log,
        "new_files_in_window": new_files,
        "modified_files_in_window": modified_files,
        "project_states": project_states,
        "redriff_area": redriff_area,
        "daily_notes": daily_notes,
        "spanish_metrics": spanish_metrics,
        "previous_report": previous_report,
    }

    print("[context_builder] Context built successfully.")
    return context


def context_to_markdown(context: dict) -> str:
    """
    Serialise the context dict to a readable markdown block
    suitable for pasting into the Claude prompt.
    """
    lines = []
    rt = context["report_type"]

    lines.append(f"# Vault Context Block — {rt.upper()} REPORT")
    lines.append(f"Generated: {context['generated_at']}")
    lines.append(f"Window: {context['window_start']} → {context['window_end']}")
    lines.append("")

    # Git summary
    lines.append("## Git Activity")
    lines.append(f"- Commits in window: {len(context['git_log'])}")
    lines.append(f"- New files: {len(context['new_files_in_window'])}")
    lines.append(f"- Modified files: {len(context['modified_files_in_window'])}")
    lines.append("")

    if context["new_files_in_window"]:
        lines.append("### New files")
        for f in context["new_files_in_window"]:
            lines.append(f"- {f}")
        lines.append("")

    if context["modified_files_in_window"]:
        lines.append("### Modified files")
        for f in context["modified_files_in_window"]:
            lines.append(f"- {f}")
        lines.append("")

    # Project states
    lines.append("## Project States")
    for p in context["project_states"]:
        lines.append(f"### {p['name']}")
        lines.append(f"- Path: {p['path']}")
        lines.append(f"- Status: {p['readme_frontmatter'].get('status', 'unknown')}")
        lines.append(f"- Last touched: {p['last_touched']} "
                     f"({p['days_since_last_touch']} days ago)")
        if p["files_created_in_window"]:
            lines.append(f"- Files created: {', '.join(p['files_created_in_window'])}")
        if p["files_modified_in_window"]:
            lines.append(f"- Files modified: {', '.join(p['files_modified_in_window'])}")
        if p["decisions_new"]:
            lines.append(f"- New decisions: {len(p['decisions_new'])}")
            for d in p["decisions_new"]:
                lines.append(f"  - {d['date']}: {d['title']}")
        lines.append("")

        # Include changed file contents
        if p["changed_file_contents"]:
            lines.append("#### Changed file contents")
            for filepath, content in p["changed_file_contents"].items():
                lines.append(f"##### {filepath}")
                lines.append("```")
                lines.append(content[:2000] + ("..." if len(content) > 2000 else ""))
                lines.append("```")
                lines.append("")

    # Redriff area
    if context["redriff_area"]:
        lines.append("## Redriff Area Files")
        for path, data in context["redriff_area"].items():
            lines.append(f"### {path}")
            lines.append(f"- Modified in window: {data['modified_in_window']}")
            lines.append(f"- Status: {data['frontmatter'].get('status', 'unknown')}")
            if data["modified_in_window"]:
                lines.append("#### Content preview")
                lines.append("```")
                lines.append(data["content_preview"])
                lines.append("```")
            lines.append("")

    # Spanish metrics
    lines.append("## Spanish Metrics")
    sm = context["spanish_metrics"]
    lines.append(f"- Hours in window: {sm['hours_in_window']}")
    lines.append(f"- Target: {sm['target_hours']}h")
    lines.append(f"- Gap: {sm['gap']:+.1f}h ({'ahead' if sm['on_target'] else 'behind'})")
    lines.append(f"- Sessions: {sm['sessions_in_window']}")
    if sm["daily_breakdown"]:
        lines.append("- Daily breakdown:")
        for day in sm["daily_breakdown"]:
            lines.append(f"  - {day['date']}: {day['minutes']} min — {day['focus']}")
    lines.append("")

    # Daily notes summary
    lines.append("## Daily Notes Summary")
    for note in context["daily_notes"]:
        lines.append(f"### {note['date']}")
        if note["top_3_priorities"]:
            lines.append(f"**Priorities:** {note['top_3_priorities'][:200]}")
        if note["decisions_made"]:
            lines.append(f"**Decisions made:** {note['decisions_made'][:200]}")
        if note["captures"]:
            lines.append(f"**Captures:** {note['captures'][:200]}")
        lines.append("")

    # Previous report
    lines.append("## Previous Report")
    if context["previous_report"]:
        lines.append("```")
        # Include full previous report but cap at 4000 chars
        prev = context["previous_report"]
        lines.append(prev[:4000] + ("...[truncated]" if len(prev) > 4000 else ""))
        lines.append("```")
    else:
        lines.append("No previous report found — this is the first run.")
    lines.append("")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    report_type = sys.argv[1] if len(sys.argv) > 1 else "weekly"
    if report_type not in ("daily", "weekly", "monthly"):
        print(f"Usage: python context_builder.py [daily|weekly|monthly]")
        sys.exit(1)

    context = build_context(report_type)
    markdown = context_to_markdown(context)

    # Print to stdout (piped into report_generator)
    print(markdown)
