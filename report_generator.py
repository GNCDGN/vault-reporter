#!/usr/bin/env python3
"""
report_generator.py
Calls Claude Code CLI with the context block + prompt to generate a report.
Parses the output into (report_markdown, questions_json).
Writes the report to the correct vault location.
"""

import os
import json
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from context_builder import build_context, context_to_markdown, save_state, load_state

# ── Configuration ─────────────────────────────────────────────────────────────

VAULT_PATH = Path(os.path.expanduser(os.environ.get("VAULT_PATH", "~/vaults/second-brain")))
PROMPTS_DIR = Path(__file__).parent / "prompts"
CLAUDE_BIN = os.environ.get("CLAUDE_CODE_PATH", "claude")

REPORT_LOCATIONS = {
    "daily":   "today.md",
    "weekly":  "06-Reviews/weekly",
    "monthly": "06-Reviews/monthly",
}

DAILY_ARCHIVE = "06-Reviews/daily-archive"
QUESTIONS_SEPARATOR = "---QUESTIONS_JSON---"


# ── Report filename helpers ───────────────────────────────────────────────────

def get_report_filename(report_type: str) -> str:
    """Return the vault-relative path where this report should be written."""
    now = datetime.now()

    if report_type == "daily":
        return "today.md"

    if report_type == "weekly":
        # ISO week: 2026-W20
        week_str = now.strftime("%G-W%V")
        return f"06-Reviews/weekly/{week_str}.md"

    if report_type == "monthly":
        month_str = now.strftime("%Y-%m")
        return f"06-Reviews/monthly/{month_str}.md"

    raise ValueError(f"Unknown report type: {report_type}")


def get_daily_archive_path() -> str:
    """Return the archive path for yesterday's daily report."""
    yesterday = datetime.now().strftime("%Y-%m-%d")
    return f"{DAILY_ARCHIVE}/{yesterday}.md"


# ── Claude Code CLI caller ────────────────────────────────────────────────────

def call_claude(prompt: str) -> str:
    """
    Call `claude --print` with the given prompt text.
    Returns the full response as a string.
    """
    # Write prompt to a temp file to avoid shell escaping issues
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(prompt)
        prompt_file = f.name

    try:
        result = subprocess.run(
            [CLAUDE_BIN, "--print", f"$(cat {prompt_file})"],
            shell=False,
            capture_output=True,
            text=True,
            timeout=300,  # 5-minute timeout
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Claude Code exited with code {result.returncode}.\n"
                f"stderr: {result.stderr}"
            )

        return result.stdout

    finally:
        os.unlink(prompt_file)


def call_claude_with_stdin(prompt: str) -> str:
    """
    Alternative: pipe prompt via stdin.
    Use this if the cat-based approach has issues.
    """
    result = subprocess.run(
        [CLAUDE_BIN, "--print"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Claude Code exited with code {result.returncode}.\n"
            f"stderr: {result.stderr}"
        )

    return result.stdout


# ── Output parser ─────────────────────────────────────────────────────────────

def parse_output(raw_output: str) -> tuple[str, dict]:
    """
    Split Claude's output into (report_markdown, questions_dict).
    The separator is ---QUESTIONS_JSON--- on its own line.
    """
    if QUESTIONS_SEPARATOR not in raw_output:
        # No separator found — treat entire output as the report, no questions
        print("[report_generator] Warning: no QUESTIONS_JSON separator found in output.")
        return raw_output.strip(), {"summary": "", "questions": []}

    parts = raw_output.split(QUESTIONS_SEPARATOR, 1)
    report_markdown = parts[0].strip()
    json_block = parts[1].strip()

    # Strip any markdown code fences around the JSON
    if json_block.startswith("```"):
        json_block = json_block.split("```")[1]
        if json_block.startswith("json"):
            json_block = json_block[4:]
        json_block = json_block.strip()

    try:
        questions_dict = json.loads(json_block)
    except json.JSONDecodeError as e:
        print(f"[report_generator] Warning: could not parse questions JSON: {e}")
        questions_dict = {"summary": "", "questions": []}

    return report_markdown, questions_dict


# ── Vault writer ──────────────────────────────────────────────────────────────

def write_report_to_vault(report_type: str, report_markdown: str):
    """Write the generated report to the correct vault location."""
    report_path = VAULT_PATH / get_report_filename(report_type)

    # For daily: archive yesterday's report first
    if report_type == "daily":
        if report_path.exists():
            archive_path = VAULT_PATH / get_daily_archive_path()
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            archive_path.write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"[report_generator] Archived yesterday's daily to {archive_path}")

    # Ensure directory exists
    report_path.parent.mkdir(parents=True, exist_ok=True)

    # Write the report
    report_path.write_text(report_markdown, encoding="utf-8")
    print(f"[report_generator] Report written to {report_path}")


def git_commit_report(report_type: str):
    """Commit and push the new report to GitHub."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    subprocess.run(
        ["git", "add", "-A"],
        cwd=VAULT_PATH,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", f"vault: {report_type} report {now}"],
        cwd=VAULT_PATH,
        check=True,
    )

    # Only push if remote is configured
    remotes = subprocess.run(
        ["git", "remote"],
        cwd=VAULT_PATH,
        capture_output=True,
        text=True,
    ).stdout.strip()

    if remotes:
        subprocess.run(["git", "push"], cwd=VAULT_PATH, check=True)
        print("[report_generator] Pushed to GitHub.")
    else:
        print("[report_generator] No remote configured — skipping push (local run).")


# ── Main generator ────────────────────────────────────────────────────────────

def generate_report(report_type: str, dry_run: bool = False) -> dict:
    """
    Full pipeline: build context → call Claude → parse → write to vault.

    Returns a dict with:
      - report_type
      - report_markdown
      - questions (list)
      - summary (str)
      - report_path (str)
    """
    print(f"\n{'='*60}")
    print(f"GENERATING {report_type.upper()} REPORT")
    print(f"{'='*60}\n")

    # Step 1: Build context
    context = build_context(report_type)
    context_markdown = context_to_markdown(context)

    # Step 2: Load prompt template
    prompt_file = PROMPTS_DIR / f"{report_type}.md"
    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_file}")

    prompt_template = prompt_file.read_text(encoding="utf-8")
    full_prompt = prompt_template.replace("{CONTEXT}", context_markdown)

    print(f"[report_generator] Prompt size: {len(full_prompt):,} chars")

    if dry_run:
        print("[report_generator] DRY RUN — not calling Claude or writing files.")
        print("\n--- CONTEXT BLOCK PREVIEW (first 500 chars) ---")
        print(context_markdown[:500])
        print("...")
        return {"report_type": report_type, "dry_run": True}

    # Step 3: Call Claude Code
    print("[report_generator] Calling Claude Code CLI...")
    try:
        raw_output = call_claude_with_stdin(full_prompt)
    except RuntimeError as e:
        print(f"[report_generator] ERROR calling Claude: {e}")
        raise

    print(f"[report_generator] Got response: {len(raw_output):,} chars")

    # Step 4: Parse output
    report_markdown, questions_dict = parse_output(raw_output)
    summary = questions_dict.get("summary", "")
    questions = questions_dict.get("questions", [])

    print(f"[report_generator] Report: {len(report_markdown):,} chars")
    print(f"[report_generator] Summary: {summary}")
    print(f"[report_generator] Questions generated: {len(questions)}")
    for i, q in enumerate(questions):
        print(f"  Q{i+1}: {q.get('text', '')[:80]}...")

    # Step 5: Write to vault
    write_report_to_vault(report_type, report_markdown)

    # Step 6: Git commit + push
    git_commit_report(report_type)

    # Step 7: Update state (mark this run's timestamp)
    state = load_state()
    state[f"last_{report_type}"] = datetime.now().isoformat()
    save_state(state)

    report_path = get_report_filename(report_type)

    return {
        "report_type": report_type,
        "report_markdown": report_markdown,
        "report_path": report_path,
        "summary": summary,
        "questions": questions,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    report_type = "weekly"
    dry_run = False

    for arg in args:
        if arg in ("daily", "weekly", "monthly"):
            report_type = arg
        elif arg in ("--dry-run", "-n"):
            dry_run = True

    result = generate_report(report_type, dry_run=dry_run)

    if not dry_run:
        print(f"\n{'='*60}")
        print("GENERATION COMPLETE")
        print(f"{'='*60}")
        print(f"Report written to: {result['report_path']}")
        print(f"Summary: {result['summary']}")
        print(f"Questions: {len(result['questions'])}")
        if result["questions"]:
            print("\nQuestions to ask via WhatsApp:")
            for i, q in enumerate(result["questions"], 1):
                print(f"  {i}. {q['text']}")
                print(f"     Hint: {q.get('hint', '')}")
                print(f"     Write to: {q['vault_write']['file']} "
                      f"[{q['vault_write']['mode']}]")
