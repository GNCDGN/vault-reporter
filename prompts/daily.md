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

Then output a JSON object:

```json
{
  "summary": "One sentence. What happened yesterday. Written for a WhatsApp notification.",
  "questions": []
}
```

Daily reports typically have zero questions — the daily cadence is too frequent for Q&A. Only include a question if something genuinely urgent needs the user's input (e.g. a project went from active to broken and needs a decision today).

---

## Context block

{CONTEXT}
