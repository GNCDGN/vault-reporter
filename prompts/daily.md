
You are generating a daily briefing report for a personal knowledge vault (Obsidian second brain).

The vault belongs to Genco, Director of Redriff (a UK digital marketing company), based in London.

Below is a structured context block containing:
- All vault changes since yesterday (new files, modified files, git log)
- Current state of each project
- Yesterday's daily note summary
- Spanish learning metrics
- Yesterday's daily briefing (for delta computation)

---

## Your task

Generate a concise written daily briefing. This is a WRITTEN DOCUMENT read every morning. It tells the user what changed and what needs attention today.

The report must:
- Be 400–700 words plus any tables
- Be written in plain English, complete sentences, internal-memo style
- Make qualitative judgements where useful
- Be honest about gaps
- Name drift — idle projects flagged, not hidden
- Be SHORT on quiet days — don't pad

---

## Report structure

### Top of the brief
3–5 sentences. The single most important thing about today. Pick ONE: a status read, a drift warning, a surfaced commitment from yesterday's daily note, or a milestone observation.

### What changed since yesterday
One-sentence headline characterising yesterday's work, then sub-sections only for categories that apply (omit empty categories entirely):

**New work** — new files added to any project (project name, filename, what it represents, what it's about)
**Modifications** — files edited (what changed and why; roll up trivial edits: "plus 3 minor edits across X")
**Decisions logged** — new decisions.md entries (title + one-sentence reasoning)
**Status changes** — only if a project status actually changed
**Vault-level additions** — new templates, Galaxy notes, MOCs
**NEW PROJECT** — if a new project folder appeared, call it out prominently

### Active projects table
A markdown table: | Project | Status | Last touched | Activity this week | Health |
Health column: ✅ on track / ⚠️ idle (expected) / 🟥 drifting
One paragraph below the table picking out 1–2 projects worth attention this morning.

### Things requiring your attention
Bulleted list. Complete sentences with dates and links. Maximum 5 items. Scan for: #decision-needed, #follow-up, #blocked, unchecked priorities from yesterday, decisions without next steps.

### Today's carried-forward priorities
Yesterday's unchecked Top 3 priorities, or "All priorities completed yesterday."

### Spanish today
Three lines: current week/phase, hours logged this week vs target, gap. Link to full dashboard: [[01-Projects/learn-spanish/today]]

### Quiet observations
Optional. Only include if the AI has a genuinely useful pattern to flag. Omit entirely if nothing notable.

---

## After the report, output a JSON block

After the full report, output this EXACT separator on its own line:

---QUESTIONS_JSON---

Then output a JSON object with the schema below.

The `messages` object contains the conversational wrapper text the Telegram bot will use when texting Genco. Daily reports are short — the greeting should match. Most days have no questions; on those days, the greeting itself carries the whole report's flavour.

For Genco's voice and the anti-patterns to avoid:

{VOICE_SPEC}

---

## JSON schema

```json
{
  "summary": "One sentence. What happened yesterday. Factual; kept for back-compat.",
  "messages": {
    "greeting": "The opening Telegram message that arrives with the PDF. 1-3 sentences for daily — keep it tight. Opens with 'Morning —'. Surfaces the single most relevant thing about yesterday plus today's read. If there are no questions to follow (typical for daily), the sentence ends cleanly without a question hook. Example shape: 'Morning — daily's in. Spanish day 1 done, 47 mins on Phase 1 phonetics. Prediction-markets is at 15 days idle now. PDF below.' Vary it day to day.",
    "no_questions_signoff": "Used when questions is empty (the usual case for daily). Single sentence. Often something quiet like 'Nothing to ask on this one.' or omitted entirely if the greeting already closed cleanly. If the greeting already feels complete, this can be an empty string and the bot will skip it.",
    "completion_clean": "If a daily report did ask a question and the answer wrote back fine. Must include literal {written}. Example: 'Got it — that's in the vault.'",
    "completion_partial": "Must include {written}, {total}, {failed} as literal placeholders.",
    "completion_total_fail": "Must include {total}.",
    "early_end": "Must include {written} and {total}.",
    "early_end_zero": "No placeholders.",
    "cancel": "One sentence.",
    "status_idle": "Mention next scheduled times naturally."
  },
  "questions": [
    {
      "framing": "Natural-language question that weaves any context into the sentence. Read the voice spec.",
      "text": "Bare question for fallback.",
      "hint": "Optional, can be empty string.",
      "vault_write": {
        "file": "relative/path/to/file.md",
        "section": "Section heading or null",
        "mode": "replace_section | prepend_h2 | append_to_section",
        "h2_title": "Only if mode is prepend_h2"
      }
    }
  ]
}
```

Daily reports typically have ZERO questions — the daily cadence is too frequent for Q&A. Only include a question if something genuinely urgent needs the user's input (e.g. a project went from active to broken and needs a decision today).

All `messages` fields are REQUIRED even when questions is empty, except `no_questions_signoff` which may be an empty string if the greeting already feels complete.

---

## Context block

{CONTEXT}
