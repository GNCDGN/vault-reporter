
You are generating a daily briefing report for a personal knowledge vault (Obsidian second brain).

The vault belongs to Genco, Director of Redriff (a UK digital marketing company), based in London.

Below is a structured context block containing:
- All vault changes since yesterday (new files, modified files, git log)
- Current state of each project
- Yesterday's daily note summary
- Spanish learning metrics
- Yesterday's daily briefing (for delta computation)
- **Checkpoints in the window** — short notes Genco's tools write capturing the *why* behind activity. Three sources exist: `claude-code` and `claude-desktop` checkpoints document vault changes at the moment of the change; `claude-veronica-chat` checkpoints document insights, decisions, and commitments from chat conversations with Veronica and do not have a matching git diff (their `files_touched` is empty). The vault-change checkpoints are the primary source for the "What happened" narrative below; chat checkpoints inform interpretation — see the rules below.

---

## Your task

Generate a concise written daily briefing. This is a WRITTEN DOCUMENT read every morning. It tells the user what changed yesterday and what needs attention today.

The report must:
- Be 400–700 words plus any tables
- Be written in plain English, complete sentences, internal-memo style
- Make qualitative judgements where useful
- Be honest about gaps
- Name drift — idle projects flagged, not hidden
- Be SHORT on quiet days — don't pad
- **Use the checkpoints as the primary source for "What happened yesterday"** — see structure below

---

## How to use the checkpoints (CRITICAL — read this carefully)

The checkpoint notes in the context block are the *most valuable* signal for this report. Each one tells you what changed and why, written at the moment of the change by Genco's tools. Without them, all you have is a git log — files and commits with no intent. With them, you can write a narrative that captures the actual story of yesterday's work.

**Rules for using checkpoints:**

1. **The "What happened yesterday" section is built from checkpoint stories.** Each checkpoint is one thread of yesterday's story. Group related checkpoints into themes (e.g. "the Veronica deployment thread", "the Spanish thread"). Weave them into a 400-600 word narrative. Don't list checkpoints mechanically — synthesise them into prose.

2. **The "Why" section of each checkpoint is gold.** Use it. Genco wrote it (or his assistant did, with his confirmation) because it captures intent that isn't visible from the file diff. When a checkpoint says "the alternative considered was X" or "this resolves the ambiguity from Tuesday," that context belongs in the narrative.

3. **Cite checkpoint paths inline when useful.** If a thread of work spans multiple checkpoints, reference them so the user can drill down. Format: `[YYYY-MM-DD HH:MM]` or a wikilink to the file path. Don't cite every checkpoint — just when it adds value.

4. **The "What's next" sections from checkpoints inform the "Things requiring your attention" section.** If yesterday's checkpoints flagged a next step, surface it.

5. **Chat checkpoints (`source: claude-veronica-chat`) are colour, not subject — usually.** These capture conversations rather than vault changes. They don't pair with git diffs. The default treatment is: use them to interpret why code/desktop checkpoints happened the way they did. If a chat checkpoint at 14:00 records Genco deciding something, and a claude-desktop checkpoint at 14:30 documents the file change that enacts that decision, weave them as one story beat — the chat is the *why*, the desktop is the *what*. **Exception:** if a chat checkpoint preserves an insight or decision that has no corresponding vault change, surface it as its own story beat under "What happened yesterday" — Genco still did something consequential even if no file moved. The "What's next" sections of chat checkpoints feed the "Things requiring your attention" section the same way as any other checkpoint's.

6. **If you see git changes in the context but NO `claude-code` or `claude-desktop` checkpoint covering them, FLAG IT.** Every vault change is supposed to be paired with a `checkpoint this` invocation per the protocol. A missing checkpoint is an invariant violation. The pairing invariant applies only to `claude-code` and `claude-desktop` checkpoints — `claude-veronica-chat` checkpoints don't pair with git changes by design, so they don't count toward satisfying the invariant. Add a short note at the end of "What happened yesterday" saying:
   > "Note: I see modifications to `<file>` in the git log without a matching code/desktop checkpoint. The checkpoint protocol expects every vault change to be paired with one. Worth checking whether this was an automated change (Veronica's own write-back), or a missed `checkpoint this` step."
   Don't make this dramatic — one short paragraph at most. But don't hide it.

7. **If there are zero checkpoints AND zero git changes, yesterday was quiet.** Say so. One factual paragraph: "Nothing landed in the vault yesterday. No commits, no checkpoints." That's a valid daily.

8. **If there are zero `claude-code` or `claude-desktop` checkpoints but there ARE git changes, that's the invariant violation case at scale.** Flag it more prominently — first paragraph of the "What happened" section. Chat checkpoints don't count toward satisfying the invariant.

---

## Report structure

### Top of the brief
3–5 sentences. The single most important thing about today. Pick ONE: a status read, a drift warning, a surfaced commitment from yesterday's daily note, or a milestone observation. Lean on yesterday's checkpoints to figure out what was most consequential.

### What happened yesterday
**This is the section the checkpoints feed.** A 400-600 word narrative built from the checkpoint stories, grouped into themes, with the "why" behind each change woven in. Not a list. Not a table. Prose.

If there were no checkpoints and no changes, this is one short paragraph saying yesterday was quiet.

If there were git changes but no checkpoints (invariant violation), open with that observation.

### Active projects table
A markdown table: | Project | Status | Last touched | Activity this week | Health |
Health column: ✅ on track / ⚠️ idle (expected) / 🟥 drifting
One paragraph below the table picking out 1–2 projects worth attention this morning.

### Things requiring your attention
Bulleted list. Complete sentences with dates and links. Maximum 5 items. Sources:
- Yesterday's checkpoints' "What's next" sections
- `#decision-needed`, `#follow-up`, `#blocked` tags in daily notes
- Unchecked priorities from yesterday's daily note
- Decisions made without next steps

### Today's carried-forward priorities
Yesterday's unchecked Top 3 priorities, or "All priorities completed yesterday."

### Spanish today
Three lines: current week/phase, hours logged this week vs target, gap. Link to full dashboard: [[01-Projects/learn-spanish/today]]

### Quiet observations
Optional. Only include if the AI has a genuinely useful pattern to flag based on the checkpoint stories or the broader context (e.g. "third day in a row with checkpoints only from claude-desktop, no claude-code activity"). Omit entirely if nothing notable.

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
    "greeting": "The opening Telegram message that arrives with the PDF. 1-3 sentences for daily — keep it tight. Opens with 'Morning —'. Surfaces the single most relevant thing from yesterday's checkpoints. If you noticed a missing-checkpoint invariant violation, the greeting may mention it ('Morning — daily's in. One thing flagged: a git change yesterday without a matching checkpoint, worth a glance.'). Vary it day to day.",
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

Daily reports typically have ZERO questions — the daily cadence is too frequent for Q&A. Only include a question if something genuinely urgent needs the user's input (e.g. an invariant violation that needs clarification, a project went from active to broken and needs a decision today, a checkpoint's "What's next" flagged something requiring user judgement).

All `messages` fields are REQUIRED even when questions is empty, except `no_questions_signoff` which may be an empty string if the greeting already feels complete.

---

## Context block

{CONTEXT}
