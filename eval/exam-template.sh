#!/usr/bin/env bash
#
# exam-template.sh — reusable Veronica exam runner.
#
# Copy this to a new exam-specific filename, edit the `questions` and
# `intents` arrays at the top, and run it. Output goes to a markdown file
# in $HOME ready to be pasted into the exam's history file in the vault.
#
# Conventions for new exams:
#   1. Copy this file to eval/<exam-slug>.sh (e.g. eval/v4-phase-2-exam.sh)
#   2. Replace EXAM_SLUG below with the exam's slug (used in output filename)
#   3. Replace the questions[] and intents[] arrays with the exam's content
#   4. Run from inside the vault-reporter repo: bash eval/<exam-slug>.sh
#   5. Output: $HOME/<exam-slug>-<date>.md
#
# The script captures: each question, the selected files (parsed from the
# chat handler's log), the reply, stage 1 / stage 2 sizes and latencies,
# and total wall-clock per question. That's the raw evidence layer.
# Grading is human — see veronica/evaluations/grading-rubric.md in the vault.
#
# Wall-clock cost: ~30-60 seconds per question, depending on complexity.
# A 10-question exam takes 5-10 minutes total.

set -u

# ─── EDIT THESE PER EXAM ──────────────────────────────────────────────────

EXAM_SLUG="my-exam"

questions=(
  "what's the status of veronica"
  "did I decide anything about X"
  # … add as many as the exam needs
)

intents=(
  "Status lookup — tests trivial self-lookup path. Regression question."
  "Decision lookup — tests selection on a real recorded decision."
  # … keep aligned to questions[]
)

# Optional: which grading dimensions apply to this exam (informational only,
# the script doesn't grade — this just gets written into the output header
# so the grader knows the intent).
GRADING_DIMENSIONS="voice, accuracy, judgement, selection, latency, honesty"

# ─── BELOW HERE IS THE STABLE TEMPLATE ────────────────────────────────────

OUTPUT="$HOME/${EXAM_SLUG}-$(date +%Y-%m-%d).md"
LOG="/tmp/${EXAM_SLUG}-handler-$$.log"

# Sanity check arrays
if [ ${#questions[@]} -ne ${#intents[@]} ]; then
  echo "[exam] ERROR: questions[] and intents[] are different lengths" >&2
  echo "[exam]   questions: ${#questions[@]}" >&2
  echo "[exam]   intents:   ${#intents[@]}" >&2
  exit 1
fi

if [ ${#questions[@]} -eq 0 ]; then
  echo "[exam] ERROR: no questions defined. Edit the questions[] array." >&2
  exit 1
fi

# Header
cat > "$OUTPUT" <<EOF
# Exam: ${EXAM_SLUG} — $(date '+%Y-%m-%d %H:%M %Z')

${#questions[@]} questions run through \`chat_handler.handle_chat_message\`
sequentially. Each entry shows the question, the intent being tested, the
files stage 1 picked, the reply, and the per-stage latency.

**Grading dimensions in scope (informational):** ${GRADING_DIMENSIONS}

Grade per-question against the rubric at
\`veronica/evaluations/grading-rubric.md\`. Use the dimensions listed above;
mark each question as **strong / pass / weak / fail / n/a** per dimension.

This file is the raw output layer. Once graded, paste this content into a
new exam history file at
\`veronica/evaluations/history/$(date +%Y-%m-%d)-${EXAM_SLUG}.md\` under a
"## Raw output" heading, with the grading and decision in their own
sections per the framework template.

---

EOF

# Run each question
for i in "${!questions[@]}"; do
  q="${questions[$i]}"
  intent="${intents[$i]}"
  n=$((i + 1))
  total=${#questions[@]}

  echo "[exam] running Q${n}/${total}: $q" >&2

  : > "$LOG"

  ts_start=$(date +%s)
  output=$(python3 chat_handler.py "$q" 2>"$LOG")
  ts_end=$(date +%s)
  elapsed=$((ts_end - ts_start))

  # Extract reply between markers
  reply=$(echo "$output" | awk '/=== REPLY ===/{flag=1; next} /=== END ===/{flag=0} flag' | sed '/^$/d')

  # Parse selected files
  selected=$(grep -o "selected [0-9]* file(s): \[.*\]" "$LOG" | head -1 || true)
  if [ -z "$selected" ]; then
    if grep -q "no files selected" "$LOG"; then
      selected="(empty — general-knowledge mode)"
    else
      selected="(no selection line found in log)"
    fi
  fi

  # Parse stage metrics (chars in, chars out)
  stage1_in=$(grep -oP "\[chat:stage1\] calling claude \(\K[0-9]+" "$LOG" | head -1 || echo "?")
  stage1_out=$(grep -oP "\[chat:stage1\] received \K[0-9]+" "$LOG" | head -1 || echo "?")
  stage2_in=$(grep -oP "\[chat:stage2\] calling claude \(\K[0-9]+" "$LOG" | head -1 || echo "?")
  stage2_out=$(grep -oP "\[chat:stage2\] received \K[0-9]+" "$LOG" | head -1 || echo "?")

  cat >> "$OUTPUT" <<EOF
## Q${n} — $(echo "$intent" | head -c 80)$([ ${#intent} -gt 80 ] && echo "...")

**Question:** $q

**Intent:** $intent

**Selected files:** $selected

**Stage 1:** ${stage1_in} chars in → ${stage1_out} chars out · **Stage 2:** ${stage2_in} chars in → ${stage2_out} chars out · **Total latency:** ${elapsed}s

**Reply:**

$(echo "$reply" | sed 's/^/> /')

**Grade per dimension:** _(fill in by hand against the rubric)_

---

EOF

  echo "[exam] Q${n} done (${elapsed}s)" >&2
done

# Footer
cat >> "$OUTPUT" <<EOF

## Grading summary (fill in)

For each dimension in scope, summarise the pattern across all questions.

| Dimension | Strong | Pass | Weak | Fail | N/A |
|---|---|---|---|---|---|
| Voice | | | | | |
| Accuracy | | | | | |
| Judgement | | | | | |
| Selection | | | | | |
| Refusal handling | | | | | |
| Latency | | | | | |
| Honesty / no-fabrication | | | | | |

## Decision (fill in)

Reference the exam's stated gate criteria or quality bar. State the
decision: **pass / fail / override**. If override, record the reasoning.

## What this exam added to the bank (fill in)

New questions to add to \`veronica/evaluations/question-bank.md\`, with
category and intent. Framework or rubric feedback to fold into the next
version. Any bugs Veronica surfaced during the exam (separate from
grading her own behaviour).

EOF

rm -f "$LOG"

echo ""
echo "[exam] done. Output at: $OUTPUT"
echo "[exam] open with: cat $OUTPUT"
echo ""
echo "[exam] next step: grade per the rubric, then paste into the exam"
echo "[exam] history file at:"
echo "[exam]   veronica/evaluations/history/$(date +%Y-%m-%d)-${EXAM_SLUG}.md"
