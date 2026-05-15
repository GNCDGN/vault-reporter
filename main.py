#!/usr/bin/env python3
"""
main.py

Orchestrator for the vault reporting system.
Ties together: context building → report generation → Telegram delivery.

Usage:
    python main.py weekly              # Generate and deliver weekly report
    python main.py daily               # Generate and deliver daily report
    python main.py monthly             # Generate and deliver monthly report
    python main.py weekly --dry-run    # Build context, print preview, no API calls
    python main.py weekly --no-send    # Generate report but skip Telegram delivery
"""

import os
import sys
import json
import logging
from pathlib import Path

# ── Logging setup ────────────────────────────────────────────────────────────

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

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

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

    from report_generator import generate_report

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
    log.info(f"Greeting: {result['messages'].get('greeting', '')[:120]}")
    log.info(f"Questions: {len(result.get('questions', []))}")

    # Telegram delivery
    if no_send:
        log.info("--no-send flag set. Skipping Telegram delivery.")
    else:
        try:
            from telegram_sender import telegram_configured, deliver_report
        except ImportError as e:
            log.error(f"Could not import telegram_sender: {e}")
            telegram_configured = lambda: False  # noqa: E731

        if telegram_configured():
            log.info("Telegram configured — delivering report...")
            vault_path = Path(os.path.expanduser(
                os.environ.get("VAULT_PATH", "~/vaults/second-brain")
            ))
            try:
                deliver_report(result, vault_path)
            except Exception as e:
                log.error(f"Telegram delivery failed: {e}", exc_info=True)
        else:
            log.info("Telegram not configured. Report is in the vault on GitHub.")

    # Terminal summary for manual runs
    questions = result.get("questions", [])
    print(f"\n{'='*60}")
    print(f"Done. Report at: {result['report_path']}")
    if questions:
        print(f"{len(questions)} question(s) queued for Telegram session.")
        for i, q in enumerate(questions, 1):
            framing = q.get("framing") or q.get("text") or "(missing)"
            print(f"  Q{i}: {framing[:100]}")
    else:
        print("No questions — vault had full signal.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
