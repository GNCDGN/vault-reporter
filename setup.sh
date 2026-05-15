#!/bin/bash
# setup.sh
# Run this once on your Mac (Phase 1) or on the Hetzner VPS (Phase 2).
# Sets up the vault-reporter environment.

set -e

echo "=== Vault Reporter Setup ==="
echo ""

# ── Python version check ──────────────────────────────────────────────────────
PYTHON=$(which python3)
PY_VERSION=$($PYTHON --version 2>&1)
echo "Python: $PY_VERSION"

if ! $PYTHON -c "import sys; assert sys.version_info >= (3,11)" 2>/dev/null; then
    echo "ERROR: Python 3.11+ required. You have: $PY_VERSION"
    exit 1
fi

# ── Claude Code check ─────────────────────────────────────────────────────────
if ! command -v claude &>/dev/null; then
    echo "ERROR: Claude Code CLI not found. Install from: https://claude.ai/download"
    echo "Then run: claude login"
    exit 1
fi
echo "Claude Code: $(claude --version 2>/dev/null || echo 'found')"

# ── Git check ─────────────────────────────────────────────────────────────────
if ! command -v git &>/dev/null; then
    echo "ERROR: git not found."
    exit 1
fi
echo "Git: $(git --version)"

# ── pip install dependencies ──────────────────────────────────────────────────
echo ""
echo "Installing Python dependencies..."
pip3 install --quiet --upgrade pip
# No external dependencies needed for Phase 1 — stdlib only.
# Phase 4 will add: twilio, fastapi, uvicorn, python-multipart
echo "No external dependencies required for Phase 1."

# ── Vault path detection ──────────────────────────────────────────────────────
echo ""
DEFAULT_VAULT="$HOME/vaults/second-brain"
echo "Looking for vault..."

if [ -d "$DEFAULT_VAULT" ]; then
    VAULT_PATH="$DEFAULT_VAULT"
    echo "Found vault at: $VAULT_PATH"
else
    read -p "Enter vault path (default: $DEFAULT_VAULT): " VAULT_PATH
    VAULT_PATH="${VAULT_PATH:-$DEFAULT_VAULT}"
fi

# Verify it's an Obsidian vault
if [ ! -d "$VAULT_PATH/.obsidian" ]; then
    echo "WARNING: No .obsidian folder found at $VAULT_PATH. Is this the right path?"
fi

# Verify it's a git repo
if [ ! -d "$VAULT_PATH/.git" ]; then
    echo "WARNING: No .git folder found at $VAULT_PATH. Git features will be limited."
fi

# ── Create .env file ──────────────────────────────────────────────────────────
ENV_FILE="$(dirname "$0")/.env"

if [ -f "$ENV_FILE" ]; then
    echo ""
    echo ".env already exists. Skipping creation."
else
    echo ""
    echo "Creating .env file..."
    cat > "$ENV_FILE" << EOF
# Vault Reporter Environment Configuration
# Edit these values, then run: source .env

# Path to your Obsidian vault
VAULT_PATH=$VAULT_PATH

# Path to Claude Code CLI binary (usually auto-detected)
CLAUDE_CODE_PATH=$(which claude)

# State file (tracks last run timestamps)
STATE_FILE=$HOME/.vault-reporter-state.json

# Log file
LOG_FILE=$HOME/vault-reporter.log

# === Phase 4 additions (leave blank for now) ===
# Twilio credentials
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_NUMBER=
YOUR_WHATSAPP_NUMBER=

# GitHub token (for VPS use — not needed locally)
GITHUB_TOKEN=
VAULT_REPO=
EOF
    echo ".env created at: $ENV_FILE"
fi

# ── Make scripts executable ───────────────────────────────────────────────────
chmod +x "$(dirname "$0")/main.py"
chmod +x "$(dirname "$0")/context_builder.py"
chmod +x "$(dirname "$0")/report_generator.py"

# ── Test run ──────────────────────────────────────────────────────────────────
echo ""
echo "=== Running dry-run test ==="
echo ""

export VAULT_PATH="$VAULT_PATH"
cd "$(dirname "$0")"

python3 main.py weekly --dry-run

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Review the context preview above — does it look right?"
echo "  2. Run: source .env && python3 main.py weekly"
echo "     This will generate a real weekly report using Claude Code."
echo "  3. Check your vault: 06-Reviews/weekly/ for the generated report."
echo ""
echo "If the context looks wrong (missing projects, wrong vault path),"
echo "check the VAULT_PATH in .env and re-run."
