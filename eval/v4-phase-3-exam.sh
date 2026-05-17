#!/usr/bin/env bash
#
# v4-phase-3-exam.sh — Phase 3 final exam, Pattern 2 (live VPS via Telegram).
#
# RUN ON THE VPS, not the Mac. The script reads bot-listener.log and
# sessions.db directly; both are VPS-only. The human runs this script in
# one terminal, sends each question from their phone Telegram client to
# Veronica, and presses Enter when the reply has arrived.
#
# Output: $HOME/v4-phase-3-final-YYYY-MM-DD.md, ready to paste into the
# vault history file at
# veronica/evaluations/history/<date>-v4-phase-3-final.md
#
# Usage:
#   bash eval/v4-phase-3-exam.sh            # real exam run
#   bash eval/v4-phase-3-exam.sh --dry-run  # walk through structure, no real interaction
#
# Dry-run mode: prints what each step WOULD do, doesn't pause for input,
# emits a fake output file. Lets us verify the script's flow on the VPS
# before committing to the real exam.

set -u

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
fi

EXAM_SLUG="v4-phase-3-final"
DATE_STR=$(date +%Y-%m-%d)
OUTPUT="$HOME/${EXAM_SLUG}-${DATE_STR}.md"
LOG="/home/vault-reporter/bot-listener.log"
DB="/home/vault-reporter/sessions.db"
VAULT="/home/vault-reporter/vault"
CHECKPOINTS_DIR="$VAULT/01-Projects/second-brain/checkpoints/active"
SESSION_DATE=$(TZ=Europe/London date +%Y-%m-%d)

# Sanity checks (skip in dry-run for portability)
if [ "$DRY_RUN" -eq 0 ]; then
  if [ ! -f "$LOG" ]; then
    echo "[exam] ERROR: $LOG not found. Run on VPS, not Mac." >&2
    exit 1
  fi
  if [ ! -f "$DB" ]; then
    echo "[exam] ERROR: $DB not found." >&2
    exit 1
  fi
  if [ ! -d "$CHECKPOINTS_DIR" ]; then
    echo "[exam] ERROR: $CHECKPOINTS_DIR not found." >&2
    exit 1
  fi
fi

# ─── QUESTION + INTENT DEFINITIONS ────────────────────────────────────────
# Phase 3 final exam: 14 turns across three blocks (13 scripted + A8 manual).
#   Block A (Initiative calibration): A1-A7 scripted, A8 (skip path) is
#     handled as a manual addendum after the scripted run — A8 requires
#     skip to arrive within 60s of the checkpoint registration, which
#     scripts can't do reliably across the human-paced Telegram turn.
#   Block B (Regression anchors): B1-B4 scripted.
#   Block C (Compression + post-/clear): C1-C2 scripted.

questions=(
  # === Block A — Initiative calibration (7 scripted; A8 manual) ===
  "should the phase 3 final exam decision be recorded as its own checkpoint or folded into the phase 3 ship docs commit"
  "should phase 4 inherit the fire-and-forget pattern from phase 3's detection or should write-back actions be on the user-visible reply path"
  "the obsidian git plugin missed the phase 2 docs commit last night and we caught it in step 0 — is there anything we should change so this doesnt happen again"
  "when phase 4 starts what's the first thing that needs to be true before we touch any code"
  "what's the vps ip address"
  "thanks"
  "where am I on prediction-markets"
  # === Block B — Regression anchors ===
  "what's the status of veronica"
  "did i decide anything about whether to use a public or private repo for veronica's code"
  "what's the difference between a daily and a weekly report in this system"
  "tell me more about the gate from a moment ago"
  # === Block C — Compression fidelity + post-/clear ===
  "was there a specific phase 4 precondition i asked about earlier"
  "remind me what we just decided about the exam commit"
)

intents=(
  "A1 — Initiative: clear positive, decision shape. Should CHECKPOINT. Probes Q-INITIATIVE-001 (new)."
  "A2 — Initiative: clear positive, principle shape. Should CHECKPOINT. Probes Q-INITIATIVE-002 (new)."
  "A3 — Initiative: clear positive, gap flag. Should CHECKPOINT. Probes Q-INITIATIVE-003 (new)."
  "A4 — Initiative: clear positive, what's-next commitment. Should CHECKPOINT. Probes Q-INITIATIVE-004 (new)."
  "A5 — Initiative: clear negative, pure factual lookup. Should SKIP. Doubles as Q-FACT-001 regression."
  "A6 — Initiative: clear negative, acknowledgement. Should gate-skip (short-user). Doubles as Q-OFFTOPIC-002."
  "A7 — Initiative: ambiguous. Detection's call is the data. Status lookup phrased casually."
  "B1 — Q-STATUS-001 regression. Voice + accuracy + selection anchor."
  "B2 — Q-DECISION-001 regression. Voice + accuracy + selection."
  "B3 — Q-CONCEPT-001 regression. Also likely compression trigger if we've crossed N=8."
  "B4 — Q-MEMORY-002 regression. Implicit back-reference to A1's gate decision."
  "C1 — Q-COMPRESSION-001. Detail recovery across compression — pulls A4's precondition through summary."
  "C2 — Q-MEMORY-005 negative case. Send /clear FIRST (manually), then ask. Tests no-fabrication post-clear."
)

# Special-handling flags per turn
# - clear_before_q: Q index (0-based) that requires sending /clear before the question
CLEAR_BEFORE_Q=12  # C2 is index 12 in zero-based array

# ─── HELPER FUNCTIONS ─────────────────────────────────────────────────────

wait_for_user() {
  local msg="$1"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] would wait for: $msg"
    return
  fi
  echo ""
  echo "------------------------------------------------------------"
  echo "$msg"
  echo "------------------------------------------------------------"
  read -r -p "Press Enter when done. > " _
}

log_position() {
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "0"
    return
  fi
  wc -l < "$LOG"
}

log_delta() {
  local from="$1"
  local to="$2"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "(dry-run: would capture lines $from..$to from $LOG)"
    return
  fi
  if [ "$to" -le "$from" ]; then
    echo "(no new log lines)"
    return
  fi
  sed -n "$((from+1)),${to}p" "$LOG"
}

chat_today_state() {
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "(dry-run: would query chat_sessions for $SESSION_DATE)"
    return
  fi
  sqlite3 "$DB" <<SQL
.mode line
SELECT session_date, exchange_count, unsummarised_turn_count, compression_count,
       last_compression_at, clear_epoch,
       pending_checkpoint_path, pending_checkpoint_expiry, pending_checkpoint_clear_epoch,
       length(summary) AS summary_len, length(messages_json) AS messages_json_len
FROM chat_sessions WHERE session_date = '$SESSION_DATE';
SQL
}

active_checkpoints_list() {
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "(dry-run: would list $CHECKPOINTS_DIR)"
    return
  fi
  ls -1 "$CHECKPOINTS_DIR" 2>/dev/null | grep -v '^\.' || true
}

# Returns names that exist in $2 but not $1
diff_lists() {
  local before="$1"
  local after="$2"
  comm -13 <(echo "$before" | sort) <(echo "$after" | sort)
}

cat_checkpoint() {
  local rel_path="$1"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "(dry-run: would cat $rel_path)"
    return
  fi
  local abs_path="$VAULT/$rel_path"
  if [ -f "$abs_path" ]; then
    cat "$abs_path"
  else
    # Maybe rel_path is just a filename, not vault-relative
    local guess="$CHECKPOINTS_DIR/$rel_path"
    if [ -f "$guess" ]; then
      cat "$guess"
    else
      echo "(file not found: $rel_path)"
    fi
  fi
}

# ─── HEADER ──────────────────────────────────────────────────────────────

mkdir -p "$(dirname "$OUTPUT")"

cat > "$OUTPUT" <<EOF
# v4 Phase 3 final exam — ${DATE_STR}

Pattern 2 exam (live VPS via Telegram) run on $(date '+%Y-%m-%d %H:%M %Z').
${#questions[@]} questions across three blocks. Q8 (A8, skip-path verification)
is run as a manual addendum after this scripted block.

Run by: $(whoami)
Script: eval/v4-phase-3-exam.sh
Output: $OUTPUT

## Purpose

Phase 3 (quiet-confirm checkpoint promotion) is in production as of
2026-05-17 ~15:46 BST. Detection fires on real claude calls, files are
written to active/, italic followups arrive via direct HTTPS, skip
handler exists. This exam is the **ship gate** — Pattern 2 against the
framework with rubric version 2 (memory coherence + compression
fidelity first-class, new dimension 11 "initiative quality").

The exam's primary purpose is to **calibrate initiative quality** —
does Veronica decide correctly when to write a checkpoint? Regression
anchors are included to confirm no degradation in Phase 1/2 behaviour
from the new code paths.

## Gate criteria

Phase 3 passes if **all** of:

1. **Initiative quality** (rubric dimension 11) — at least 5 of 7 Block A
   turns receive Strong or Pass. No Fails.
2. **Regression anchors** (B1-B4) — no regression from Phase 1/2 baselines.
   No Fails on voice, accuracy, selection, memory.
3. **Compression fidelity** (C1) — Strong or Pass.
4. **Post-/clear honesty** (C2) — Strong or Pass on memory coherence.
5. **No latency catastrophes** — user-visible latency under 60s on every
   turn. Background detection latency uncapped (acceptable).
6. **Skip path** (A8, manual addendum) — verified to delete file and ack.

Decision triggered after the script + the A8 addendum + grading.

## Shape and dimensions

- **Shape:** Pattern 2 (live VPS via Telegram).
- **Dimensions in scope:** Voice, Accuracy, Judgement, Selection, Latency,
  Honesty, Memory coherence, Compression fidelity, **Initiative quality (new)**.
- **Refusal handling and Failure-mode handling:** N/A — exam doesn't
  include write-action triggers or deliberate failure inductions.

---

## Pre-exam state

EOF

# Pre-exam state capture
{
  echo ""
  echo "### bot-listener.service status"
  echo ""
  echo '```'
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "(dry-run: would capture systemctl status output)"
  else
    systemctl status vault-reporter-bot.service --no-pager | head -10
  fi
  echo '```'
  echo ""
  echo "### chat_sessions row before exam"
  echo ""
  echo '```'
  chat_today_state
  echo '```'
  echo ""
  echo "### active checkpoints before exam"
  echo ""
  echo '```'
  active_checkpoints_list
  echo '```'
  echo ""
  echo "### log position at start"
  echo ""
  EXAM_START_POS=$(log_position)
  echo "Line ${EXAM_START_POS} of bot-listener.log"
  echo ""
  echo "---"
  echo ""
} >> "$OUTPUT"

# Capture initial baseline files for diffing
BASELINE_FILES=$(active_checkpoints_list)
PRIOR_POS="$EXAM_START_POS"

# ─── PER-TURN LOOP ────────────────────────────────────────────────────────

for i in "${!questions[@]}"; do
  q="${questions[$i]}"
  intent="${intents[$i]}"
  n=$((i + 1))
  total=${#questions[@]}

  # Special handling: send /clear before Q13 (C2)
  if [ "$i" -eq "$CLEAR_BEFORE_Q" ]; then
    {
      echo ""
      echo "## Pre-Q${n} — /clear"
      echo ""
      echo "Send \`/clear\` to Veronica from Telegram. Wait for the \"cleared.\" reply."
      echo ""
    } >> "$OUTPUT"
    wait_for_user "Send '/clear' from Telegram. Wait for 'cleared.' reply, then press Enter."

    pos_after_clear=$(log_position)
    {
      echo "### Log delta for /clear"
      echo ""
      echo '```'
      log_delta "$PRIOR_POS" "$pos_after_clear"
      echo '```'
      echo ""
      echo "### chat_sessions state after /clear"
      echo ""
      echo '```'
      chat_today_state
      echo '```'
      echo ""
    } >> "$OUTPUT"
    PRIOR_POS="$pos_after_clear"
  fi

  echo "[exam] Q${n}/${total}: $q" >&2

  {
    echo ""
    echo "## Q${n} — $(echo "$intent" | cut -c1-80)"
    echo ""
    echo "**Question:**"
    echo ""
    echo '```'
    echo "$q"
    echo '```'
    echo ""
    echo "**Intent:** $intent"
    echo ""
  } >> "$OUTPUT"

  wait_for_user "Send to Telegram: \"$q\""$'\n'"Wait for Veronica's full reply (and any italic followup). Then press Enter."

  pos_after_turn=$(log_position)
  current_files=$(active_checkpoints_list)
  new_files=$(diff_lists "$BASELINE_FILES" "$current_files")

  {
    echo "**Log delta:**"
    echo ""
    echo '```'
    log_delta "$PRIOR_POS" "$pos_after_turn"
    echo '```'
    echo ""
    echo "**chat_sessions state after this turn:**"
    echo ""
    echo '```'
    chat_today_state
    echo '```'
    echo ""

    # If new checkpoint files appeared, dump them
    if [ -n "$new_files" ]; then
      echo "**New checkpoint file(s) created this turn:**"
      echo ""
      while IFS= read -r fname; do
        if [ -z "$fname" ]; then
          continue
        fi
        echo "**File:** \`01-Projects/second-brain/checkpoints/active/$fname\`"
        echo ""
        echo '```markdown'
        cat_checkpoint "01-Projects/second-brain/checkpoints/active/$fname"
        echo '```'
        echo ""
      done <<< "$new_files"
      # Update baseline so subsequent turns only show new files
      BASELINE_FILES="$current_files"
    else
      echo "**No new checkpoint file this turn.**"
      echo ""
    fi

    echo "**Grading scaffold:** _(fill by hand against rubric v2)_"
    echo ""
    echo "| Dimension | Grade | Notes |"
    echo "|---|---|---|"
    echo "| Voice | | |"
    echo "| Accuracy | | |"
    echo "| Judgement | | |"
    echo "| Selection | | |"
    echo "| Latency | | |"
    echo "| Honesty | | |"
    echo "| Memory coherence | | |"
    echo "| Compression fidelity | | |"
    echo "| **Initiative quality (new)** | | |"
    echo ""
    echo "---"
    echo ""
  } >> "$OUTPUT"

  PRIOR_POS="$pos_after_turn"
done

# ─── GRADING SCAFFOLD ─────────────────────────────────────────────────────

{
  echo ""
  echo "## Final state"
  echo ""
  echo "### chat_sessions row after all scripted turns"
  echo ""
  echo '```'
  chat_today_state
  echo '```'
  echo ""
  echo "### active checkpoints after scripted turns"
  echo ""
  echo '```'
  active_checkpoints_list
  echo '```'
  echo ""
  echo "### Error scan (exam-session-window only)"
  echo ""
  echo '```'
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "(dry-run: would scan log lines from start to end of exam for errors)"
  else
    exam_end_pos=$(log_position)
    sed -n "$((EXAM_START_POS+1)),${exam_end_pos}p" "$LOG" | grep -iE "error|exception|traceback" || echo "(no errors in exam window)"
  fi
  echo '```'
  echo ""
  echo "---"
  echo ""
  echo "## A8 (manual addendum) — skip-within-window verification"
  echo ""
  echo "Run this AFTER the scripted block finishes."
  echo ""
  echo "1. Copy the literal word \`skip\` to clipboard before sending the next question."
  echo "2. Send to Telegram: \`let's commit the phase 3 exam decision before tomorrow's daily run rather than waiting — agree or not\`"
  echo "3. Wait for Veronica's reply AND the italic \`_checkpointed that — reply skip to drop_\` followup."
  echo "4. The moment the italic message arrives, paste-and-send \`skip\`."
  echo "5. Verify Veronica replies \`dropped that checkpoint.\` and the checkpoint file is gone from active/."
  echo "6. Capture: the log lines for A8 turn + skip turn, plus the active-checkpoints listing before/after."
  echo ""
  echo "Add the result to the section below."
  echo ""
  echo "### A8 result (fill in)"
  echo ""
  echo "_(log lines + Telegram outcome + active-checkpoints diff go here)_"
  echo ""
  echo "---"
  echo ""
  echo "## Grading summary (fill in)"
  echo ""
  echo "| Dimension | Strong | Pass | Weak | Fail | N/A |"
  echo "|---|---|---|---|---|---|"
  echo "| Voice | | | | | |"
  echo "| Accuracy | | | | | |"
  echo "| Judgement | | | | | |"
  echo "| Selection | | | | | |"
  echo "| Latency | | | | | |"
  echo "| Honesty | | | | | |"
  echo "| Memory coherence | | | | | |"
  echo "| Compression fidelity | | | | | |"
  echo "| **Initiative quality** | | | | | |"
  echo ""
  echo "## Decision (fill in)"
  echo ""
  echo "Reference the gate criteria from the Purpose section. State **pass / fail / override**."
  echo "If override, the reasoning."
  echo ""
  echo "## What this exam added to the bank (fill in)"
  echo ""
  echo "- Q-INITIATIVE-001 through 004 — new initiative-probe questions seeded from Block A."
  echo "- A7's framing → potential ambiguous-case category."
  echo "- Rubric version 2 promotion completed."
  echo ""
} >> "$OUTPUT"

echo ""
echo "[exam] done. Output: $OUTPUT"
echo "[exam] next: run the A8 manual addendum, then grade and paste into vault history file."
echo "[exam] history file path:"
echo "[exam]   veronica/evaluations/history/${DATE_STR}-v4-phase-3-final.md"
