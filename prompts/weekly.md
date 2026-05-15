You are generating a weekly briefing report for a personal knowledge vault (Obsidian second brain).

The vault belongs to Genco, Director of Redriff (a UK digital marketing company), based in London.

Below is a structured context block containing:
- All vault changes in the past week (new files, modified files, git log)
- Current state of each project
- Daily note summaries
- Spanish learning metrics
- The previous weekly report (for comparison and delta computation)

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
4-6 sentences. Characterise the week. What dominated? What stalled? What kind of week was it overall?

### Per-project progress
One subsection per project found in the context. Use ### for each project name.

For each project write:
**[Project name] -- [status] -- last touched [X days ago]**

Then:
- A paragraph (4-8 sentences) synthesising what happened this week on this project. If nothing happened, still write a paragraph -- but make it honest about the idle state and whether that is expected or a drift warning.
- A "Work completed this week" sub-list (only if there was activity). Categorise by:
  - New content created (files added -- grouped by what they represent, not just filenames)
  - Modifications (files edited -- describe what changed and why)
  - Decisions logged (new entries in decisions.md -- title + one-sentence reasoning)
  - Status changes (frontmatter shifts)
  - New sub-folders (with purpose)
  - Phase/milestone progress (for phased projects like learn-spanish or prediction-markets-system)
- One sentence on "State entering next week" -- trajectory read.

For projects with ZERO activity: write a single honest paragraph. Include how many days idle, whether that is expected given the project's nature, and whether it warrants attention.

### Redriff this week
Same treatment as a project, but covering the Redriff domain:
- Vault-side changes (anything modified in 12-Redriff/ -- business-overview, sub-folders, etc.)
- Decisions logged in any Redriff decisions.md
- Operational signal: if no vault-side operational data exists, write exactly: "No vault-side operational signal captured this week -- Redriff activity (clients, campaigns, billing) is not visible to this briefing."

### Decisions logged across the vault
A table: | Date | Project | Decision | then one paragraph picking out decisions with cross-project implications.
If no dated decisions were found, note that and mention that undated decision entries cannot be tracked by window.

### Vault-level changes
New Galaxy notes, new MOCs, new top-level folders, CLAUDE.md edits, template changes. Short prose + bullets.

### What stopped or stalled
Explicitly name projects, habits, or routines that lost momentum. Include specific numbers (days idle, missed sessions, etc.).

### Pattern of the week
2-4 sentences. One observation about what KIND of week this was at higher altitude. This is your synthesis read. You are allowed to be wrong -- the value is in prompting reflection.

### Recommended attention for next week
3-5 sentences or short bullets. Your read on what deserves attention based on the week's data. Reasoned, not aspirational. Not a goal list.

### Spanish this week
One paragraph summarising Spanish learning status. Link to the Spanish dashboard at the end: "Full Spanish dashboard: [[01-Projects/learn-spanish/today]]"

---

## After the report, output a JSON block

After the full report, output this EXACT separator on its own line:

---QUESTIONS_JSON---

Then output a JSON object with this structure:

{
  "summary": "Two sentences maximum. What the report covers at a glance. Written for a WhatsApp notification.",
  "questions": [
    {
      "text": "The question to ask the user",
      "hint": "(context hint in parentheses)",
      "vault_write": {
        "file": "relative/path/to/file.md",
        "section": "Section heading to write into, or null",
        "mode": "replace_section or prepend_h2 or append_to_section",
        "h2_title": "Only required if mode is prepend_h2"
      }
    }
  ]
}

Generate 0-5 questions. Only ask questions when:
- A project had zero vault-side activity and the idle state is not obviously expected
- Redriff had no vault-side operational signal (ask what happened operationally)
- A decision was flagged in a daily note but no entry appeared in decisions.md
- A pattern of avoidance is visible (e.g. 3+ weeks idle on an active-phase project)
- An insight was surfaced that should be promoted to 07-Galaxy/ but was not

Never ask questions about things the vault already captured. Never pad to a minimum number of questions -- zero questions is a valid response if the vault has full signal.

For vault_write.file: use the path relative to the vault root (e.g. "06-Reviews/weekly/2026-W20.md").
For vault_write.mode:
- replace_section: replaces the content of the named section heading
- prepend_h2: inserts a new ## heading at the top of the file (after the H1)
- append_to_section: appends content after the named section

---

## Context block

{CONTEXT}
