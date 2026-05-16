#!/usr/bin/env python3
"""
report_generator.py

Calls Claude Code CLI with the prompt + context, parses the response into
report markdown + JSON metadata (summary, conversational messages, questions).
Writes the report to the vault, commits, pushes.

Phase 6+ schema: the JSON metadata now includes a `messages` block containing
the conversational text the Telegram bot will use. See prompts/*.md and
prompts/_voice.md for the full schema.

Phase 3 (checkpoint integration): for daily reports, the run sequence is:
    1. sweep_archive()  — move eligible checkpoints to archive/ (housekeeping)
    2. build_context()  — reads active/ post-sweep + injects into the prompt
    3. claude --print   — generate report referencing the checkpoints
    4. write + commit + push the report
    5. mark_for_archive() — mark the checkpoints just read as eligible for
       sweep in ARCHIVE_BUFFER_DAYS (only if steps 2-4 succeeded)

Back-compat: the old `summary` field and the legacy `text` + `hint` fields on
each question are preserved. If a generated report is missing the new
`messages` block (e.g. transient parse failure), fallback strings live in
this file under DEFAULT_MESSAGES.
"""

import os
import sys
import json
import logging
import subprocess
from pathlib import Path
from datetime import datetime

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = SCRIPT_DIR / "prompts"
VOICE_SPEC_PATH = PROMPTS_DIR / "_voice.md"

VAULT_PATH = Path(os.path.expanduser(
    os.environ.get("VAULT_PATH", "~/vaults/second-brain")
))

# ---------------------------------------------------------------------------
# Fallback messages — used if the AI fails to produce a valid `messages` block
# ---------------------------------------------------------------------------

DEFAULT_MESSAGES = {
    "greeting": "Report's ready. PDF below.",
    "no_questions_signoff": "",
    "completion_clean": "Got it — {written} written back to the vault.",
    "completion_partial": "Got it. {written} of {total} written, {failed} didn't take. I've left a log entry.",
    "completion_total_fail": "That didn't land — none of the {total} made it to the vault. Log entry's there.",
    "early_end": "Saving what we've got — {written} of {total} written, the rest dropped.",
    "early_end_zero": "Dropped, nothing written.",
    "cancel": "Cancelled. Nothing written. Reports continue on schedule.",
    "status_idle": "Nothing live right now. Next up: daily 06:00, weekly Sunday 20:00, monthly 1st 08:00.",
}

# Required message keys — if any are missing from the AI output, the fallback fills them in
REQUIRED_MESSAGE_KEYS = list(DEFAULT_MESSAGES.keys())

# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

def load_voice_spec() -> str:
    """Load the shared voice/anti-pattern spec block."""
    if not VOICE_SPEC_PATH.exists():
        log.warning(f"[generator] voice spec not found at {VOICE_SPEC_PATH}")
        return ""
    return VOICE_SPEC_PATH.read_text()

def load_prompt(report_type: str, context: str) -> str:
    """
    Load the prompt template for a given report type and substitute
    {VOICE_SPEC} and {CONTEXT} placeholders.
    """
    prompt_path = PROMPTS_DIR / f"{report_type}.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt not found: {prompt_path}")

    template = prompt_path.read_text()
    voice_spec = load_voice_spec()

    # Substitute in this order: voice first (it can be referenced inside the
    # report instructions), then context last
    rendered = template.replace("{VOICE_SPEC}", voice_spec)
    rendered = rendered.replace("{CONTEXT}", context)
    return rendered

# ---------------------------------------------------------------------------
# Claude Code invocation
# ---------------------------------------------------------------------------

def call_claude(prompt: str, timeout: int = 600) -> str:
    """
    Send the prompt to Claude Code via stdin and return the response.
    """
    log.info(f"[generator] calling claude --print ({len(prompt)} chars)")
    try:
        result = subprocess.run(
            ["claude", "--print"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log.error(f"[generator] claude timed out after {timeout}s")
        raise
    except FileNotFoundError:
        log.error("[generator] 'claude' command not found on PATH")
        raise

    if result.returncode != 0:
        log.error(f"[generator] claude exit code {result.returncode}")
        log.error(f"[generator] stderr: {result.stderr[:500]}")
        raise RuntimeError(f"claude --print failed: {result.stderr[:200]}")

    response = result.stdout
    log.info(f"[generator] received {len(response)} chars from claude")
    return response

# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

SEPARATOR = "---QUESTIONS_JSON---"

def parse_response(response: str) -> tuple[str, dict]:
    """
    Split the response on the QUESTIONS_JSON separator.
    Returns (report_markdown, metadata_dict).
    metadata_dict is normalised: missing fields get DEFAULT_MESSAGES values
    so downstream code can always trust the shape.
    """
    if SEPARATOR not in response:
        log.error(f"[generator] separator '{SEPARATOR}' not found in response")
        # Best-effort: treat the whole thing as report markdown, no metadata
        return response, _normalise_metadata({})

    report_md, _, json_block = response.partition(SEPARATOR)
    report_md = report_md.strip()
    json_block = json_block.strip()

    # Strip a possible ```json fence
    if json_block.startswith("```"):
        # Remove leading fence and optional language tag
        json_block = json_block.split("\n", 1)[1] if "\n" in json_block else ""
        # Remove trailing fence
        if json_block.rstrip().endswith("```"):
            json_block = json_block.rstrip()[:-3]
        json_block = json_block.strip()

    try:
        metadata = json.loads(json_block)
    except json.JSONDecodeError as e:
        log.error(f"[generator] JSON parse failed: {e}")
        log.error(f"[generator] JSON block was: {json_block[:500]}")
        metadata = {}

    return report_md, _normalise_metadata(metadata)

def _normalise_metadata(metadata: dict) -> dict:
    """
    Ensure metadata has the expected shape:
    - summary: string
    - messages: dict with all REQUIRED_MESSAGE_KEYS present
    - questions: list of dicts each with framing/text/hint/vault_write
    Missing fields are filled with safe defaults rather than crashing later.
    """
    out = {
        "summary": metadata.get("summary", "").strip() or "Report ready.",
        "messages": {},
        "questions": [],
    }

    # Messages: merge with defaults
    src_messages = metadata.get("messages", {}) or {}
    for key in REQUIRED_MESSAGE_KEYS:
        value = src_messages.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            # Use default — but allow empty string for no_questions_signoff
            if key == "no_questions_signoff":
                out["messages"][key] = value if value is not None else ""
            else:
                out["messages"][key] = DEFAULT_MESSAGES[key]
                log.warning(f"[generator] messages.{key} missing, using fallback")
        else:
            out["messages"][key] = value

    # Questions: normalise each entry
    for q in metadata.get("questions", []) or []:
        if not isinstance(q, dict):
            continue
        out["questions"].append({
            "framing": q.get("framing") or q.get("text") or "(question missing)",
            "text": q.get("text", ""),
            "hint": q.get("hint", ""),
            "vault_write": q.get("vault_write") or {},
        })

    return out

# ---------------------------------------------------------------------------
# Report file paths
# ---------------------------------------------------------------------------

def report_output_path(report_type: str) -> Path:
    """Where in the vault should this report be written?"""
    now = datetime.now()
    if report_type == "daily":
        return VAULT_PATH / "today.md"
    if report_type == "weekly":
        iso_year, iso_week, _ = now.isocalendar()
        return VAULT_PATH / "06-Reviews" / "weekly" / f"{iso_year}-W{iso_week:02d}.md"
    if report_type == "monthly":
        return VAULT_PATH / "06-Reviews" / "monthly" / f"{now.year}-{now.month:02d}.md"
    raise ValueError(f"Unknown report type: {report_type}")

# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------

def git_commit_and_push(report_type: str, report_path: Path):
    """Commit and push the report from the vault repo.
    Returns True if the push succeeded, False otherwise."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    commit_msg = f"vault: {report_type} report {timestamp}"

    try:
        # Stage the report
        subprocess.run(
            ["git", "-C", str(VAULT_PATH), "add", str(report_path.relative_to(VAULT_PATH))],
            check=True, capture_output=True,
        )
        # Commit (allow empty to be a no-op rather than an error)
        result = subprocess.run(
            ["git", "-C", str(VAULT_PATH), "commit", "-m", commit_msg],
            capture_output=True, text=True,
        )
        if result.returncode != 0 and "nothing to commit" not in result.stdout:
            log.warning(f"[generator] git commit returned {result.returncode}: {result.stdout}")
            return False
        # Push
        push = subprocess.run(
            ["git", "-C", str(VAULT_PATH), "push"],
            capture_output=True, text=True,
        )
        if push.returncode != 0:
            log.error(f"[generator] git push failed: {push.stderr}")
            return False
        log.info(f"[generator] pushed: {push.stdout.strip() or 'ok'}")
        return True
    except subprocess.CalledProcessError as e:
        log.error(f"[generator] git operation failed: {e}")
        return False

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_report(report_type: str, dry_run: bool = False) -> dict:
    """
    Full pipeline: build context, call Claude, parse, write, commit, push.
    Returns a dict with keys: report_type, report_path, summary, messages,
    questions.

    For daily reports: also runs the checkpoint sweep at the start and the
    mark-for-archive at the end (only after a successful commit). See module
    docstring for the full sequence.
    """
    if report_type not in ("daily", "weekly", "monthly"):
        raise ValueError(f"Invalid report type: {report_type}")

    # Step 1 — Checkpoint housekeeping (daily only)
    # Sweep any checkpoints whose archive_after has passed. This happens
    # FIRST so build_context() reads only the still-active checkpoints.
    if report_type == "daily" and not dry_run:
        try:
            from checkpoint_archiver import sweep_archive
            log.info("[generator] running checkpoint sweep...")
            sweep_summary = sweep_archive()
            if sweep_summary["moved"] > 0:
                log.info(
                    f"[generator] archived {sweep_summary['moved']} "
                    f"checkpoint(s) past their buffer"
                )
            if sweep_summary["errors"] > 0:
                log.warning(
                    f"[generator] {sweep_summary['errors']} errors during sweep — "
                    f"those checkpoints remain in active/"
                )
        except ImportError:
            log.info("[generator] checkpoint_archiver not available; skipping sweep")
        except Exception as e:
            log.error(f"[generator] sweep failed: {e}", exc_info=True)
            # Don't abort — sweep failure shouldn't prevent the report

    # Step 2 — Build the context block
    from context_builder import build_context, context_to_markdown
    context_dict = build_context(report_type)
    context = context_to_markdown(context_dict)

    # Remember which checkpoints went into the prompt — we mark them for
    # archive after a successful commit
    checkpoint_paths_in_window = [
        cp["path"] for cp in context_dict.get("checkpoints", [])
    ]

    # Assemble the prompt
    prompt = load_prompt(report_type, context)
    log.info(f"[generator] assembled prompt: {len(prompt)} chars")

    if dry_run:
        log.info("[generator] dry run — skipping Claude call")
        preview = prompt[:1500] + ("\n... [truncated]" if len(prompt) > 1500 else "")
        print("\n=== PROMPT PREVIEW ===\n" + preview + "\n=== END PREVIEW ===\n")
        return {
            "report_type": report_type,
            "report_path": "",
            "summary": "(dry run)",
            "messages": dict(DEFAULT_MESSAGES),
            "questions": [],
            "checkpoints_in_window": checkpoint_paths_in_window,
        }

    # Step 3 — Call Claude
    response = call_claude(prompt)

    # Step 4 — Parse response into report + metadata
    report_md, metadata = parse_response(response)

    # Step 5 — Write report to the correct vault path
    output = report_output_path(report_type)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report_md)
    log.info(f"[generator] wrote report: {output} ({len(report_md)} chars)")

    # Step 6 — Commit and push to vault repo
    push_ok = git_commit_and_push(report_type, output)

    # Vault path is absolute; downstream consumers want it relative to the vault
    try:
        relative_path = output.relative_to(VAULT_PATH)
    except ValueError:
        relative_path = output

    # Step 7 — Mark integrated checkpoints for archive (daily only, only if
    # the report was successfully committed and pushed)
    if report_type == "daily" and push_ok and checkpoint_paths_in_window:
        try:
            from checkpoint_archiver import mark_for_archive
            mark_for_archive(checkpoint_paths_in_window, str(relative_path))
        except ImportError:
            log.warning(
                "[generator] checkpoint_archiver not available; "
                "cannot mark checkpoints for archive"
            )
        except Exception as e:
            log.error(f"[generator] mark_for_archive failed: {e}", exc_info=True)
            # Don't fail the run — the report is already committed.
            # On next daily run, the checkpoints will still be read and
            # the markup re-attempted.

    return {
        "report_type": report_type,
        "report_path": str(relative_path),
        "summary": metadata["summary"],
        "messages": metadata["messages"],
        "questions": metadata["questions"],
        "checkpoints_in_window": checkpoint_paths_in_window,
    }

# ---------------------------------------------------------------------------
# CLI entry (for manual testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if len(sys.argv) < 2:
        print("Usage: report_generator.py [daily|weekly|monthly] [--dry-run]")
        sys.exit(1)
    rt = sys.argv[1]
    dry = "--dry-run" in sys.argv[2:]
    result = generate_report(rt, dry_run=dry)
    print(json.dumps({
        "report_type": result["report_type"],
        "report_path": result["report_path"],
        "summary": result["summary"],
        "n_questions": len(result["questions"]),
        "n_checkpoints_in_window": len(result.get("checkpoints_in_window", [])),
        "messages_keys": list(result["messages"].keys()),
    }, indent=2))
