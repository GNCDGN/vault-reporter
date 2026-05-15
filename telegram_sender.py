#!/usr/bin/env python3
"""
telegram_sender.py
Telegram bot delivery for the vault reporter.
Named telegram_sender to avoid collision with the 'telegram' pip package.
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


def deliver_report(result: dict, vault_path: Path) -> bool:
    """
    Full delivery pipeline:
      1. Send summary text
      2. Convert markdown to PDF + send as document
      3. Create a session in the database for the questions
      4. Send first question; bot_listener handles the rest
    """
    if not telegram_configured():
        log.info("[telegram] not configured - skipping delivery")
        return False

    summary = result.get("summary", "Report ready.")
    questions = result.get("questions", [])
    report_path = vault_path / result["report_path"]

    # 1. Summary message
    rt = result["report_type"].title()
    nl = "\n\n"
    intro = f"📊 *{rt} report ready*{nl}{summary}"
    if questions:
        intro += f"{nl}_{len(questions)} question(s) follow._"
    send_text(intro)

    # 2. Convert and send PDF
    pdf_filename = f"{result['report_type']}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.pdf"
    reports_dir = Path("/home/vault-reporter/reports")
    reports_dir.mkdir(exist_ok=True)
    pdf_path = reports_dir / pdf_filename

    if markdown_to_pdf(report_path, pdf_path):
        send_document(pdf_path, caption=f"Full {result['report_type']} report")
    else:
        send_text("(PDF generation failed - report is in your vault but couldn't be attached here.)")

    # 3 + 4. Create session and ask first question
    if questions:
        from sessions import create_session
        session_id = create_session(
            report_type=result["report_type"],
            report_path=result["report_path"],
            questions=questions,
        )
        log.info(f"[telegram] created session {session_id} with {len(questions)} questions")

        q = questions[0]
        q_text = f"*Q1/{len(questions)}:* {q['text']}"
        if q.get("hint"):
            q_text += f"{nl}_{q['hint']}_"
        q_text += f"{nl}_Reply with your answer. Send 'skip' to skip, 'done' to end early._"
        send_text(q_text)

    return True


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if not telegram_configured():
        print("ERROR: Telegram not configured. Check .env file.")
        sys.exit(1)

    print("Sending test message...")
    if send_text("👋 *Hello from vault-reporter.*\n\nTelegram bot is working."):
        print("✓ Sent successfully. Check your Telegram.")
    else:
        print("✗ Send failed. Check logs.")
        sys.exit(1)
