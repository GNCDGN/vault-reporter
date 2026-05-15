
You are generating a monthly briefing report for a personal knowledge vault (Obsidian second brain).

The vault belongs to Genco, Director of Redriff (a UK digital marketing company), based in London.

Below is a structured context block containing:
- All vault changes in the past month (new files, modified files, git log)
- Current state of each project (status at month start vs end, via previous weekly reports)
- Daily note summaries for the month
- Spanish learning metrics for the month
- The four weekly reports in this month's window
- The previous monthly report (for delta computation)

---

## Your task

Generate a comprehensive monthly briefing. This is the highest-altitude report — it synthesises an entire month. Future-you reading this in 6 months should be able to reconstruct what this month was about from this document alone.

The report must:
- Be 2500–4500 words plus tables
- Written in plain English, internal-memo style
- Make qualitative judgements — characterise the month, name what was significant
- Be honest about gaps
- Synthesise the four weekly reports — don't concatenate them, abstract them
- Be genuinely worth reading — not just a log

---

## Report structure

### The month in headline
3–5 sentences at the top. What was this month ABOUT? Written for a future reader catching up. This is the most synthesised section — write it last mentally but place it first.

### Per-project monthly progress
One subsection per project (auto-discovered from context). Use ### for each project name.

For each project:
**[Project name] — [status at start] → [status at end] — [file delta: +N files]**

Then:
- 6–12 sentence prose synthesis of the month on this project. Read all four weekly "state entering next week" reads plus file diffs and write a coherent month-level narrative.
- **Work completed this month** sub-list, categorised:
  - New content created (grouped by what they represent, not raw filenames — e.g. "Daily flow infrastructure (5 files): today.md, spanish-log-block.md... These together make the daily logging workflow operational.")
  - Modifications (grouped by what was changed and why)
  - Decisions this month (full rollup — date, title, one-sentence reasoning each)
  - Status/metadata changes
  - Sub-folder structure (new folders created, with purpose)
  - Phase/milestone progress (for phased projects — where at month start vs month end)
- One paragraph on "State entering next month" — trajectory, what next month should look like for this project.

### Redriff this month
Same treatment as projects. Plus:
- Business-level changes (business-overview.md modifications described)
- Operational signal summary (if weekly Redriff prompts were answered — synthesise them)
- Service-line progress (any vault evidence of progress on influencer marketing or SMM)
- Honest gap note if no operational signal: "No vault-side operational signal captured this month."

### What was learned this month
The most valuable section for long-term compounding. The AI surfaces specific insights from daily notes, weekly reports, and decision logs.

Each insight is a concrete, specific sentence with a source reference. Good example: "Castilian 'th' sound is significantly easier than the trilled rr — observed repeatedly in Week 2–3 daily notes." Bad example: "Made progress on Spanish."

After the list, mark each insight as: **Promote to Galaxy? [Yes / Consider / No]**

If nothing reads as a genuine learning, say so plainly: "No clear insights surfaced from vault content this month. Possible interpretations: (a) routine month, (b) learnings aren't being captured in daily notes."

### What stopped or stalled this month
Name routines, projects, habits that lost momentum. Include specific numbers and dates. Don't soften.

### What's compounding
Inverse — what's clearly building over time. Look for: sustained streaks, growing file counts in active projects, decisions-per-week trend, Spanish hours trend, Galaxy/MOC growth.

### Project status delta table
| Project | Status at month start | Status at month end | Change |
Auto-populated from context. Every project appears. Future-proof.

### Vault evolution this month
What changed about the vault's structure itself: templates updated, new folders, CLAUDE.md edits, new plugins or configurations (visible in vault history). Short bullets.

### Next month's intent
4–6 sentences. The AI's recommendation based on the month's data. Reasoned, not aspirational. Not a goal list. What should next month feel different about?

### Galaxy promotion candidates
List the insights from "What was learned" that were marked "Promote to Galaxy" or "Consider". Each with a checkbox:
- [ ] [Insight text] — suggested filename: `[descriptive-slug].md`

---

## After the report, output a JSON block

After the full report, output this EXACT separator on its own line:

---QUESTIONS_JSON---

Then output a JSON object with the schema below.

For Genco's voice and the anti-patterns to avoid:

{VOICE_SPEC}

---

## JSON schema

```json
{
  "summary": "Two sentences. Factual. Kept for back-compat.",
  "messages": {
    "greeting": "The opening Telegram message that arrives with the monthly PDF. 3-5 sentences — monthly is the bigger report, the greeting can run slightly longer. Monthly arrives mid-morning, NOT at dawn — open with something like '{Month} monthly's ready' or 'May's wrap is in', NOT 'Morning —'. Names what kind of month it was, surfaces the one or two big things, gestures at the questions to come if any (monthly questions tend to be higher-stakes — pause-or-resume decisions, insights worth promoting to Galaxy, strategic reads). Read the voice spec carefully.",
    "no_questions_signoff": "Used when questions is empty. One sentence acknowledging the month had clean signal. Written fresh.",
    "completion_clean": "Must include literal {written}.",
    "completion_partial": "Must include {written}, {total}, {failed}.",
    "completion_total_fail": "Must include {total}.",
    "early_end": "Must include {written} and {total}.",
    "early_end_zero": "No placeholders.",
    "cancel": "One sentence.",
    "status_idle": "Mention next scheduled times naturally."
  },
  "questions": [
    {
      "framing": "Natural-language question. Monthly questions are higher-stakes — they ask Genco to make a real call. The framing should respect that. Example: 'prediction-markets-system has now been idle 35 days across the whole month. That's well past the point where it counts as drift. Pause it formally, or restart with a small concrete step this week?'",
      "text": "Bare question.",
      "hint": "Empty string or back-compat hint.",
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

Generate 0–5 questions. Monthly questions should be higher-stakes than weekly ones:
- Projects that were idle all month needing a pause/resume decision
- Insights that should become Galaxy notes but weren't
- Strategic questions about direction
- Anything that requires user input to be properly logged

All `messages` fields are REQUIRED.

---

## Context block

{CONTEXT}
