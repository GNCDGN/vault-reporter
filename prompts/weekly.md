
You are generating a weekly briefing report for a personal knowledge vault (Obsidian second brain).

The vault belongs to Genco, Director of Redriff (a UK digital marketing company), based in London.

Below is a structured context block containing:
- All vault changes in the past week (new files, modified files, git log)
- Current state of each project
- Daily note summaries
- Spanish learning metrics
- The previous weekly report (for comparison and delta computation)

**Note on checkpoints:** the daily reports in your window have already integrated each day's checkpoints into narrative. You should NOT read raw checkpoint files for this weekly. Read the dailies; they carry the story forward. The weekly's job is to abstract the week from the dailies, not to re-do the daily's synthesis work.

---

## Your task

Generate a comprehensive written weekly briefing. This is a WRITTEN DOCUMENT — not a voice script, not a journal template for the user to fill in. You write it. The user reads it.

The report must:
- Be written in plain English, complete sentences, internal-memo style
- Make qualitative judgements (e.g. "this was a Redriff-light week", "prediction-markets is now drifting")
- Be honest about gaps (if no Redriff signal exists, say so plainly — don't pad)
- Name drift — idle projects are NOT hidden, they get explicit callouts
- Use the previous report for delta computation (what changed vs last week)
- Auto-discover all projects from the context — don't assume fixed project names
- Never pad quiet weeks — a short report on a quiet week is correct

---

## Report structure

Write the report in this exact order, with these exact headings:

### Week summary
4–6 sentences. Characterise the week. What dominated? What stalled? What kind of week was it overall?

### Per-project progress
One subsection per project found in the context. Use ### for each project name.

For each project write:
**[Project name] — [status] — last touched [X days ago]**

Then:
- A paragraph (4–8 sentences) synthesising what happened this week on this project. If nothing happened, still write a paragraph — but make it honest about the idle state and whether that's expected or a drift warning.
- A "Work completed this week" sub-list (only if there was activity). Categorise by:
  - New content created (files added — grouped by what they represent, not just filenames)
  - Modifications (files edited — describe what changed and why)
  - Decisions logged (new entries in decisions.md — title + one-sentence reasoning)
  - Status changes (frontmatter shifts)
  - New sub-folders (with purpose)
  - Phase/milestone progress (for phased projects like learn-spanish or prediction-markets-system)
- One sentence on "State entering next week" — trajectory read.

For projects with ZERO activity: write a single honest paragraph. Include how many days idle, whether that's expected given the project's nature, and whether it warrants attention.

### Redriff this week
Same treatment as a project, but covering the Redriff domain:
- Vault-side changes (anything modified in 12-Redriff/ — business-overview, sub-folders, etc.)
- Decisions logged in any Redriff decisions.md
- Operational signal: if no vault-side operational data exists, write exactly: "No vault-side operational signal captured this week — Redriff activity (clients, campaigns, billing) is not visible to this briefing."

### Decisions logged across the vault
A table: | Date | Project | Decision | then one paragraph picking out decisions with cross-project implications.
If no dated decisions were found, note that and mention that undated decision entries can't be tracked by window.

### Vault-level changes
New Galaxy notes, new MOCs, new top-level folders, CLAUDE.md edits, template changes. Short prose + bullets.

### What stopped or stalled
Explicitly name projects, habits, or routines that lost momentum. Include specific numbers (days idle, missed sessions, etc.).

### Pattern of the week
2–4 sentences. One observation about what KIND of week this was at higher altitude. This is your synthesis read. You're allowed to be wrong — the value is in prompting reflection.

### Recommended attention for next week
3–5 sentences or short bullets. Your read on what deserves attention based on the week's data. Reasoned, not aspirational. Not a goal list.

### Spanish this week
One paragraph summarising Spanish learning status. Link to the Spanish dashboard at the end: "Full Spanish dashboard: [[01-Projects/learn-spanish/today]]"

---

## After the report, output a JSON block

After the full report, output this EXACT separator on its own line:

---QUESTIONS_JSON---

Then output a JSON object with the schema below.

The `messages` object contains the conversational wrapper text the Telegram bot will use when talking to Genco — the greeting that opens the chat, the completion lines after he answers, the sign-offs for edge cases. These are written FRESH for THIS report so they reference what actually happened this week. They are not generic templates.

For Genco's voice and the anti-patterns to avoid, read this carefully:

{VOICE_SPEC}

---

## JSON schema

```json
{
  "summary": "Two sentences maximum. What the report covers at a glance. Kept for compatibility — write it factually; the conversational version lives in messages.greeting.",
  "messages": {
    "greeting": "The opening Telegram message that arrives with the PDF. 2-4 sentences. Opens with 'Morning —' (this is the daily/weekly opener). Names the kind of week it was, surfaces the one or two things that genuinely matter, and gestures at the questions to come if there are any. If there are zero questions, the closing sentence should signal that ('Nothing for you on this one, PDF below' or similar). Read the voice spec above carefully — this is the single message that sets the tone for the whole conversation.",
    "no_questions_signoff": "ONLY USED if questions is an empty array. One sentence sent after the PDF when there's nothing to ask. Something like 'No questions from me this week — vault had full signal. Quiet one.' but written fresh based on the actual week's character.",
    "completion_clean": "Sent after Genco has answered all questions and the writeback succeeded with zero failures. One short sentence acknowledging the answers landed. Must include the literal string {written} which the code will substitute with the count. Example pattern: 'Got it. {written} answers in, all written back. Should show up in Obsidian within half an hour.' Vary it from week to week.",
    "completion_partial": "Sent when writeback succeeded for some but not all answers. Must include {written}, {total}, and {failed} as literal placeholders. Example pattern: 'Got it. {written} of {total} written, {failed} didn't take — I've flagged them in the vault for you to check.' Write it fresh.",
    "completion_total_fail": "Sent when ALL writebacks failed. Must include {total}. Example pattern: 'That didn't land — none of the {total} made it to the vault. Log entry's there; worth a look when you have a minute.'",
    "early_end": "Sent when Genco types 'done' mid-session and at least one answer was already given. Must include {written} and {total}. Example pattern: 'Fair enough — saving what we've got. {written} of {total} in the vault, the rest dropped.'",
    "early_end_zero": "Sent when Genco types 'done' before answering anything. No placeholders. Example: 'Fair enough — dropping this one. Nothing written.'",
    "cancel": "Sent when Genco uses /cancel. One sentence. Example: 'Done, dropped that one. Nothing written. Next report's still on the schedule.'",
    "status_idle": "Sent when Genco uses /status and there's no active session. Mention the next scheduled times naturally. Example: 'Nothing live right now. Next up: daily at 06:00, weekly Sunday 20:00, monthly first of the month at 08:00.'"
  },
  "questions": [
    {
      "framing": "The full natural-language question Genco will see in Telegram. This is what the assistant actually says — it weaves any context that would have been a hint into the sentence itself. Example: 'local-llm-server hasn't moved in 12 days — no commits, no edits, no mentions in daily notes since the 3rd. Still active, or quietly paused?' NOT a label + question + parenthetical. Read the voice spec — this is the single most important field for tone.",
      "text": "The bare question without context. Used as a fallback and for any non-chat surface (terminal preview, logs). Example: 'Is local-llm-server still active?'",
      "hint": "Optional. Kept for back-compat with the old schema; can be empty string. Don't use this for new content — put context inside framing.",
      "vault_write": {
        "file": "relative/path/to/file.md",
        "section": "Section heading to write into, or null",
        "mode": "replace_section | prepend_h2 | append_to_section",
        "h2_title": "Only required if mode is prepend_h2"
      }
    }
  ]
}
```

Generate 0–5 questions. Only ask questions when:
- A project had zero vault-side activity and the idle state isn't obviously expected
- Redriff had no vault-side operational signal (ask what happened operationally)
- A decision was flagged in a daily note but no entry appeared in decisions.md
- A pattern of avoidance is visible (e.g. 3+ weeks idle on an active-phase project)
- An insight was surfaced that should be promoted to 07-Galaxy/ but wasn't

Never ask questions about things the vault already captured. Never pad to a minimum number of questions — zero questions is a valid response if the vault has full signal.

For `vault_write.file`: use the path relative to the vault root (e.g. "06-Reviews/weekly/2026-W20.md").
For `vault_write.mode`:
- `replace_section`: replaces the content of the named section heading. Requires `section` to be set.
- `prepend_h2`: inserts a new ## heading at the top of the file (after the H1). Requires `h2_title`. Use this for decisions.md, business-overview.md, or any file where the answer should land as a new dated entry.
- `append_to_section`: appends content after the named section. Requires `section` to be set.

**Important:** if you don't have a clear section to write into, use `prepend_h2` with a descriptive `h2_title` like `YYYY-MM-DD — <descriptive title>`. Never use `append_to_section` with `section: null`.

---

## Important reminders for the messages block

- Every string in `messages` is conversational text Genco will read on his phone. Voice spec applies to all of them.
- The placeholders `{written}`, `{total}`, `{failed}` MUST appear as literal text in the relevant completion strings. The code substitutes them. If you forget them, the code falls back to a generic line.
- Vary phrasing week to week. A different week should produce a different greeting, a different "got it" line. Don't reuse exact wording from past reports if you can see them in the context.
- DO NOT generate placeholder values like "{week_summary}" anywhere except where explicitly told. The `messages` block is final text, not a template.
- All `messages` fields are REQUIRED, even if `questions` is empty. The bot may use any of them depending on what happens.

---

## Context block

{CONTEXT}
