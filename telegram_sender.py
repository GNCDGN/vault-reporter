#!/usr/bin/env python3
"""
telegram_sender.py

Telegram bot delivery for the vault reporter.

Phase 6+: the bot's conversational scaffolding is now AI-generated. Every
report comes with a `messages` dict containing the greeting, completion
lines, sign-offs, etc. — written fresh by Claude as part of the same
generation call that produced the report. This file just delivers them.

Static templates remain ONLY as fallbacks when the AI fails to produce a
valid `messages` block. See report_generator.DEFAULT_MESSAGES.
"""

import os
import subprocess
import logging
import json
from pathlib import Path
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.parse import urlencode

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot"

# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

def telegram_configured() -> bool:
    """True if Telegram credentials are set."""
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))

def _api_url(method: str) -> str:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    return f"{TELEGRAM_API}{token}/{method}"

def send_text(text: str, parse_mode: str = "Markdown") -> bool:
    """Send a text message. Returns True on success."""
    if not telegram_configured():
        log.warning("[telegram] not configured - skipping send")
        return False
    if len(text) > 4000:
        text = text[:3997] + "..."
    try:
        data = urlencode({
            "chat_id": os.environ["TELEGRAM_CHAT_ID"],
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": "true",
        }).encode()
        req = Request(_api_url("sendMessage"), data=data)
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
        if result.get("ok"):
            log.info(f"[telegram] sent text ({len(text)} chars)")
            return True
        log.error(f"[telegram] send_text failed: {result}")
        # If Markdown failed (e.g. unescaped underscores in user content),
        # retry once as plain text — better to deliver something than nothing.
        if parse_mode == "Markdown":
            log.info("[telegram] retrying as plain text")
            return send_text(text, parse_mode="")
        return False
    except Exception as e:
        log.error(f"[telegram] send_text exception: {e}")
        if parse_mode == "Markdown":
            return send_text(text, parse_mode="")
        return False

def send_document(file_path: Path, caption: str = "") -> bool:
    """Send a document via multipart upload using curl."""
    if not telegram_configured():
        return False
    if not file_path.exists():
        log.error(f"[telegram] file not found: {file_path}")
        return False
    try:
        cmd = [
            "curl", "-s", "-X", "POST",
            _api_url("sendDocument"),
            "-F", f"chat_id={os.environ['TELEGRAM_CHAT_ID']}",
            "-F", f"document=@{file_path}",
            "-F", f"caption={caption}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        response = json.loads(result.stdout) if result.stdout else {}
        if response.get("ok"):
            log.info(f"[telegram] sent document {file_path.name}")
            return True
        log.error(f"[telegram] send_document failed: {response}")
        return False
    except Exception as e:
        log.error(f"[telegram] send_document exception: {e}")
        return False

def markdown_to_pdf(markdown_path: Path, pdf_path: Path) -> bool:
    """Convert markdown to PDF using pandoc + xelatex."""
    try:
        result = subprocess.run(
            [
                "pandoc", str(markdown_path), "-o", str(pdf_path),
                "--pdf-engine=xelatex",
                "-V", "geometry:margin=2cm",
                "-V", "fontsize=11pt",
                "-V", "colorlinks=true",
                "-V", "linkcolor=blue",
            ],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            log.error(f"[telegram] pandoc failed: {result.stderr}")
            return False
        log.info(f"[telegram] generated PDF at {pdf_path}")
        return True
    except Exception as e:
        log.error(f"[telegram] pandoc error: {e}")
        return False

# ---------------------------------------------------------------------------
# Question rendering
# ---------------------------------------------------------------------------

# The dim footer that gives Genco the escape hatches. Static — these are
# command words, not voice text, so they don't need AI generation.
QUESTION_FOOTER = "\n\n_skip · done_"

def render_question(question: dict) -> str:
    """
    Turn a question dict into the Telegram message body.
    Uses `framing` (the natural-language version) when available, falling
    back to text + hint composition for legacy questions.
    """
    framing = (question.get("framing") or "").strip()
    if framing:
        return framing + QUESTION_FOOTER

    # Legacy fallback: compose from text + hint
    text = (question.get("text") or "").strip()
    hint = (question.get("hint") or "").strip()
    composed = text
    if hint:
        composed += f"\n\n_{hint}_"
    return composed + QUESTION_FOOTER

# ---------------------------------------------------------------------------
# Full delivery pipeline
# ---------------------------------------------------------------------------

def deliver_report(result: dict, vault_path: Path) -> bool:
    """
    Full delivery pipeline:
    1. Send greeting (AI-generated, varies per report)
    2. Convert markdown to PDF + send as document
    3. Either:
       - Create a session and ask the first question (if questions exist), OR
       - Send the no-questions sign-off

    `result` shape (from report_generator.generate_report):
      {
        "report_type": "weekly" | "daily" | "monthly",
        "report_path": "06-Reviews/weekly/2026-W20.md",
        "summary": "...",                        # back-compat factual summary
        "messages": {
            "greeting": "...",
            "no_questions_signoff": "...",
            "completion_clean": "...",            # contains {written}
            "completion_partial": "...",          # contains {written}/{total}/{failed}
            "completion_total_fail": "...",       # contains {total}
            "early_end": "...",                   # contains {written}/{total}
            "early_end_zero": "...",
            "cancel": "...",
            "status_idle": "...",
        },
        "questions": [{"framing": "...", "text": "...", "hint": "...", "vault_write": {...}}, ...],
      }
    """
    if not telegram_configured():
        log.info("[telegram] not configured - skipping delivery")
        return False

    messages = result.get("messages", {}) or {}
    questions = result.get("questions", []) or []
    report_path = vault_path / result["report_path"]

    # 1. Greeting — AI-generated, varies per report. If missing for any
    # reason, fall back to the factual summary.
    greeting = (messages.get("greeting") or "").strip()
    if not greeting:
        greeting = result.get("summary") or "Report ready."
        log.warning("[telegram] no greeting in messages, using summary fallback")
    send_text(greeting)

    # 2. Convert and send PDF
    pdf_filename = f"{result['report_type']}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.pdf"
    reports_dir = Path("/home/vault-reporter/reports")
    reports_dir.mkdir(exist_ok=True)
    pdf_path = reports_dir / pdf_filename

    if markdown_to_pdf(report_path, pdf_path):
        send_document(pdf_path, caption=f"Full {result['report_type']} report")
    else:
        send_text("(PDF didn't generate this time — report is in the vault though.)")

    # 3. Questions or sign-off
    if questions:
        from sessions import create_session
        session_id = create_session(
            report_type=result["report_type"],
            report_path=result["report_path"],
            questions=questions,
            messages=messages,
        )
        log.info(f"[telegram] created session {session_id} with {len(questions)} questions")

        # Ask the first question. No more "Q1/3" prefix — that was a form-field
        # tell; the framing carries the question naturally and Genco knows the
        # rest are coming.
        first_q_text = render_question(questions[0])
        send_text(first_q_text)
    else:
        # No questions to ask. Send the sign-off if there is one.
        signoff = (messages.get("no_questions_signoff") or "").strip()
        if signoff:
            send_text(signoff)

    return True

# ---------------------------------------------------------------------------
# Manual smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if not telegram_configured():
        print("ERROR: Telegram not configured. Check .env file.")
        sys.exit(1)
    print("Sending test message...")
    if send_text("Hello from vault-reporter — Telegram bot is working."):
        print("✓ Sent successfully. Check your Telegram.")
    else:
        print("✗ Send failed. Check logs.")
        sys.exit(1)
