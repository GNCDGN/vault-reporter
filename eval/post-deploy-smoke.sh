#!/usr/bin/env bash
# eval/post-deploy-smoke.sh — non-interactive Phase 3 e2e verification
set -euo pipefail
systemctl is-active vault-reporter-bot.service | grep -q active
sleep 5
tail -50 /home/vault-reporter/bot-listener.log | grep -qv "Traceback"
python3 -c "import sqlite3; c = sqlite3.connect('/home/vault-reporter/sessions.db'); c.execute('SELECT COUNT(*) FROM chat_detection_log').fetchone()"
echo "post-deploy smoke: ok"
