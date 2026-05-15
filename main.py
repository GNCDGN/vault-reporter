#!/usr/bin/env python3
"""
main.py
Orchestrator for the vault reporting system.
Ties together: context building → report generation → WhatsApp delivery.

Usage:
  python main.py weekly              # Generate and deliver weekly report
  python main.py daily               # Generate and deliver daily report
  python main.py monthly             # Generate and deliver monthly report
  python main.py weekly --dry-run    # Build context + show preview, no API calls
  python main.py weekly --no-send    # Generate report but don't send WhatsApp

Phase 1 (current): report generation only.
Phase 4+: WhatsApp delivery is enabled via --send flag or by default.
"""

import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path

# ── Logging setup ─────────────────────────────────────────────────────────────

LOG_FILE = os.environ.get("LOG_FILE", os.path.expanduser("~/vault-reporter.log"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE),
    ],
)
log = logging.getLogger(__name__)


# ── Phase detection ───────────────────────────────────────────────────────────

def whatsapp_available() -> bool:
    """Check if Twilio credentials are configured (Phase 4+)."""
    return bool(os.environ.get("TWILIO_ACCOUNT_SID"))


# ── WhatsApp stub (Phase 4 — not yet implemented) ─────────────────────────────

def send_whatsapp_report(result: dict):
    """
    Phase 4: Send the report via WhatsApp.
    Currently a stub — prints what would be sent.
    """
    log.info("[whatsapp] STUB — would send:")
    log.info(f"[whatsapp] Summary: {result.get('summary', '')}")
    log.info(f"[whatsapp] Report PDF: {result.get('report_path', '')}")
    questions = result.get("questions", [])
    if questions:
        log.info(f"[whatsapp] Would ask {len(questions)} questions:")
        for i, q in enumerate(questions, 1):
            log.info(f"[whatsapp]   Q{i}: {q['text']}")
    else:
        log.info("[whatsapp] No questions to ask.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    # Parse arguments
    report_type = None
    dry_run = False
    no_send = False

    for arg in args:
        if arg in ("daily", "weekly", "monthly"):
            report_type = arg
        elif arg in ("--dry-run", "-n"):
            dry_run = True
        elif arg == "--no-send":
            no_send = True

    if not report_type:
        print("Usage: python main.py [daily|weekly|monthly] [--dry-run] [--no-send]")
        sys.exit(1)

    log.info(f"Starting {report_type} report generation")
    log.info(f"Vault: {os.environ.get('VAULT_PATH', '~/vaults/second-brain')}")
    log.info(f"Dry run: {dry_run}")

    # Import here so errors surface clearly
    from report_generator import generate_report

    # Generate the report
    try:
        result = generate_report(report_type, dry_run=dry_run)
    except Exception as e:
        log.error(f"Report generation failed: {e}", exc_info=True)
        sys.exit(1)

    if dry_run:
        log.info("Dry run complete. No files written, no messages sent.")
        return

    log.info(f"Report generated: {result['report_path']}")
    log.info(f"Summary: {result['summary']}")
    log.info(f"Questions: {len(result.get('questions', []))}")

    # WhatsApp delivery (Phase 4+)
    if no_send:
        log.info("--no-send flag set. Skipping WhatsApp delivery.")
    elif whatsapp_available():
        log.info("Twilio configured — sending via WhatsApp...")
        send_whatsapp_report(result)
    else:
        log.info("Twilio not configured (Phase 1/2/3). Skipping WhatsApp delivery.")
        log.info("Report is written to vault and committed to GitHub.")

    # Print questions to terminal so you can see what would be asked
    questions = result.get("questions", [])
    if questions:
        print("\n" + "="*60)
        print("QUESTIONS THAT WOULD BE ASKED VIA WHATSAPP:")
        print("="*60)
        for i, q in enumerate(questions, 1):
            print(f"\nQ{i}: {q['text']}")
            print(f"     {q.get('hint', '')}")
            print(f"     → Write to: {q['vault_write']['file']} "
                  f"[{q['vault_write']['mode']}]")
    else:
        print("\nNo questions to ask — vault has full signal.")

    print(f"\n{'='*60}")
    print(f"Done. Report at: {result['report_path']}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
