#!/usr/bin/env python3
"""
chat_handler.py

Conversational mode for Veronica (v4 Phase 1).

Implements the two-stage retrieval flow described in the v4 master design:

    Stage 1 — file selection: build a vault index (paths + frontmatter only),
              send it plus the user's message to `claude --print` with the
              file-selection prompt, parse the response into a list of paths.

    Stage 2 — answer generation: read the selected file contents, assemble
              voice spec + conversational prompt + vault file blocks + user
              message, send to `claude --print`, return the response.

The single public entry point is `handle_chat_message(user_text: str) -> str`.
It always returns a string and never raises — all errors become reply text per
the design's failure-mode table. The bot listener calls this function and
sends the result back over Telegram.

Phase 1 scope: read-only, single-message Q&A, no session memory, no checkpoint
writing. Memory arrives in Phase 2 and checkpoint writing in Phase 3.

The `git pull` of the vault clone happens at the start of each turn. A pull
failure does not block the reply — the handler falls back to stale local state
and notes that in the reply.
"""

import os
import sys
import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths and configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = SCRIPT_DIR / "prompts"
VOICE_SPEC_PATH = PROMPTS_DIR / "_voice.md"
FILE_SELECTION_PROMPT_PATH = PROMPTS_DIR / "_file_selection.md"
CONVERSATIONAL_PROMPT_PATH = PROMPTS_DIR / "_conversational.md"
COMPRESSION_PROMPT_PATH = PROMPTS_DIR / "_history_compression.md"

VAULT_PATH = Path(os.path.expanduser(
    os.environ.get("VAULT_PATH", "~/vaults/second-brain")
))

# Folders excluded from the vault index — noise and system folders the chat
# handler should never read or surface. Paths are vault-relative, matched
# against the first segment of each candidate file's relative path.
NOISE_FOLDERS = {
    "04-Archive",
    "_attachments",
    ".trash",
    "00-Inbox/_dumps",
    ".git",
    ".obsidian",
    ".smart-env",
}

# Hard limits
MAX_SELECTED_FILES = 10           # cap on stage-1 output
CHAT_CLAUDE_TIMEOUT = 60          # per-stage timeout, seconds
MAX_FILE_CONTENT_CHARS = 50_000   # safety cap per file in stage 2
GIT_PULL_TIMEOUT = 15             # seconds

# Rolling-compression levers (v4 Phase 2 Step 5). Fixed by design — do not
# tune here. Lever 4: don't compress a conversation shorter than this many
# exchanges. Lever 2: don't compress fewer than this many unsummarised turns.
# Lever 1: an exchange whose combined user+assistant content is under this
# many chars is "short"; an all-short batch is dropped, not summarised.
COMPRESSION_MIN_EXCHANGES = 8
COMPRESSION_MIN_UNSUMMARISED = 5
COMPRESSION_SHORT_EXCHANGE_CHARS = 100
COMPRESSION_TIMEOUT = 30          # seconds, per the compression claude call

# ---------------------------------------------------------------------------
# Vault index — Stage 1 input
# ---------------------------------------------------------------------------

def _is_noise(rel_path: Path) -> bool:
    """True if rel_path is inside one of the NOISE_FOLDERS."""
    parts = rel_path.parts
    if not parts:
        return False
    # Drop anything in a dot-directory anywhere in the path
    if any(p.startswith(".") for p in parts):
        return True
    # Match noise folders by their leading segment(s)
    rel_str = str(rel_path)
    for noise in NOISE_FOLDERS:
        if rel_str == noise or rel_str.startswith(noise + "/"):
            return True
    return False


def _parse_frontmatter(text: str) -> dict:
    """
    Minimal YAML-ish parser for the leading frontmatter block of a markdown
    file. Handles scalars, simple lists (inline [a, b, c] and block - a),
    and ignores nested structures. Returns {} if no frontmatter is present.

    Deliberately tolerant — the chat handler only needs the frontmatter as a
    rough signal for file selection, not a full parse.
    """
    if not text.startswith("---"):
        return {}

    # Find the closing --- on its own line
    lines = text.split("\n")
    if len(lines) < 2:
        return {}

    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        return {}

    fm: dict = {}
    current_list_key: str | None = None
    for raw in lines[1:end_idx]:
        if not raw.strip():
            current_list_key = None
            continue

        # Block list continuation: "  - item"
        if current_list_key and raw.lstrip().startswith("- "):
            item = raw.lstrip()[2:].strip().strip('"').strip("'")
            fm[current_list_key].append(item)
            continue

        # key: value (or key:)
        if ":" not in raw:
            current_list_key = None
            continue

        key, _, value = raw.partition(":")
        key = key.strip()
        value = value.strip()

        if not key:
            current_list_key = None
            continue

        if not value:
            # Empty value — assume a block list follows
            fm[key] = []
            current_list_key = key
            continue

        # Inline list: [a, b, c]
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            items = [s.strip().strip('"').strip("'") for s in inner.split(",") if s.strip()]
            fm[key] = items
            current_list_key = None
            continue

        # Plain scalar — strip quotes
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        fm[key] = value
        current_list_key = None

    return fm


def build_vault_index() -> list[dict]:
    """
    Walk the vault, return one entry per markdown file outside the noise
    folders. Each entry: {"path": "<vault-relative>", "frontmatter": {...}}.

    Reads only the leading frontmatter of each file, not the body — cheap
    enough to do on every turn for the v4 Phase 1 vault size.
    """
    if not VAULT_PATH.exists():
        log.error(f"[chat] vault path does not exist: {VAULT_PATH}")
        return []

    entries: list[dict] = []
    for md_path in VAULT_PATH.rglob("*.md"):
        try:
            rel = md_path.relative_to(VAULT_PATH)
        except ValueError:
            continue
        if _is_noise(rel):
            continue
        try:
            # Read just enough to capture the frontmatter — frontmatter blocks
            # in this vault are well under 2 KB.
            with md_path.open("r", encoding="utf-8") as f:
                head = f.read(4096)
        except (OSError, UnicodeDecodeError) as e:
            log.warning(f"[chat] could not read {rel} for index: {e}")
            continue
        fm = _parse_frontmatter(head)
        entries.append({"path": str(rel), "frontmatter": fm})

    log.info(f"[chat] vault index built: {len(entries)} files")
    return entries


def _render_index(entries: list[dict]) -> str:
    """
    Render the vault index for stage 1's prompt. Format matches the contract
    shown in prompts/_file_selection.md.
    """
    lines: list[str] = []
    for e in entries:
        lines.append(f"path: {e['path']}")
        fm = e.get("frontmatter") or {}
        if not fm:
            lines.append("frontmatter: (none)")
        else:
            lines.append("frontmatter:")
            for k, v in fm.items():
                if isinstance(v, list):
                    rendered_v = "[" + ", ".join(str(x) for x in v) + "]"
                else:
                    rendered_v = str(v)
                lines.append(f"  {k}: {rendered_v}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Claude subprocess
# ---------------------------------------------------------------------------

def _call_claude_chat(prompt: str, stage_label: str,
                      timeout: int = CHAT_CLAUDE_TIMEOUT) -> tuple[bool, str]:
    """
    Call `claude --print` for one chat stage. Returns (ok, output_or_error).
    ok=True means the call succeeded and output is the model's response.
    ok=False means a failure occurred and the second value is a short error
    string the caller can fold into a reply.

    Deliberately does not raise — chat handler must always return a string.
    """
    log.info(f"[chat:{stage_label}] calling claude ({len(prompt)} chars, timeout {timeout}s)")
    try:
        result = subprocess.run(
            ["claude", "--print"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log.error(f"[chat:{stage_label}] claude timed out after {timeout}s")
        return False, "timeout"
    except FileNotFoundError:
        log.error(f"[chat:{stage_label}] 'claude' not on PATH")
        return False, "claude-not-found"

    if result.returncode != 0:
        log.error(
            f"[chat:{stage_label}] claude exit {result.returncode}; "
            f"stderr: {result.stderr[:300]}"
        )
        return False, f"exit-{result.returncode}"

    out = result.stdout or ""
    log.info(f"[chat:{stage_label}] received {len(out)} chars")
    return True, out


# ---------------------------------------------------------------------------
# Stage 1 — file selection
# ---------------------------------------------------------------------------

def _parse_file_selection(response: str, valid_paths: set[str]) -> list[str]:
    """
    Parse the file-selection stage's output into a deduplicated list of
    valid, vault-relative paths. Drops blank lines, code fences, and any path
    that doesn't actually exist in the index. Caps at MAX_SELECTED_FILES.
    """
    paths: list[str] = []
    seen: set[str] = set()
    for raw in response.splitlines():
        line = raw.strip()
        if not line:
            continue
        # Strip code-fence wrappers in case the model adds them despite the prompt
        if line.startswith("```"):
            continue
        # Strip bullet markers if they appear
        if line.startswith(("- ", "* ")):
            line = line[2:].strip()
        # Strip wrapping quotes/backticks
        line = line.strip("`").strip('"').strip("'").strip()
        if not line:
            continue
        if line in seen:
            continue
        if line not in valid_paths:
            log.info(f"[chat:stage1] dropping unknown path: {line}")
            continue
        paths.append(line)
        seen.add(line)
        if len(paths) >= MAX_SELECTED_FILES:
            break
    return paths


def select_files(user_text: str, index: list[dict]) -> tuple[list[str], str]:
    """
    Run stage 1. Returns (selected_paths, stage_status).
    stage_status: "ok", "empty" (model returned no paths — general-knowledge
    question), or a short error code like "timeout"/"exit-1" if the call
    failed. Callers should treat "empty" as a valid no-file selection.
    """
    if not FILE_SELECTION_PROMPT_PATH.exists():
        log.error(f"[chat:stage1] prompt missing: {FILE_SELECTION_PROMPT_PATH}")
        return [], "prompt-missing"

    template = FILE_SELECTION_PROMPT_PATH.read_text()
    index_block = _render_index(index)

    prompt = (
        template
        + "\n\n---\n\n## Vault index\n\n"
        + index_block
        + "\n---\n\n## User message\n\n"
        + user_text
        + "\n\n---\n\nReturn the paths now, one per line, nothing else.\n"
    )

    ok, output = _call_claude_chat(prompt, stage_label="stage1")
    if not ok:
        return [], output

    valid_paths = {e["path"] for e in index}
    selected = _parse_file_selection(output, valid_paths)

    if not selected:
        log.info("[chat:stage1] no files selected — treating as general-knowledge")
        return [], "empty"

    log.info(f"[chat:stage1] selected {len(selected)} file(s): {selected}")
    return selected, "ok"


# ---------------------------------------------------------------------------
# Stage 2 — answer generation
# ---------------------------------------------------------------------------

def _load_selected_files(paths: list[str]) -> dict[str, str]:
    """
    Read each selected file in full (capped at MAX_FILE_CONTENT_CHARS). Skips
    files that have disappeared since the index was built. Returns a dict
    keyed by vault-relative path.
    """
    out: dict[str, str] = {}
    for p in paths:
        full = VAULT_PATH / p
        if not full.exists():
            log.warning(f"[chat:stage2] selected file vanished: {p}")
            continue
        try:
            content = full.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            log.warning(f"[chat:stage2] could not read {p}: {e}")
            continue
        if len(content) > MAX_FILE_CONTENT_CHARS:
            content = content[:MAX_FILE_CONTENT_CHARS] + "\n\n[... truncated]"
        out[p] = content
    return out


def _render_vault_block(files: dict[str, str]) -> str:
    """Wrap each loaded file in a <vault_file path="..."> ... </vault_file> block."""
    if not files:
        return ""
    parts: list[str] = []
    for path, content in files.items():
        parts.append(f'<vault_file path="{path}">')
        parts.append(content)
        parts.append("</vault_file>")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _render_history_section(session: dict) -> str:
    """
    Render today's prior conversation for the stage 2 prompt. Returns the
    section string (header + optional summary block + per-turn blocks), or
    "" if there is nothing to show — in which case the caller splices
    nothing and the model treats the conversation as fresh.

    The <conversation_summary> / <prior_user_message> / <prior_assistant_message>
    wrapping is the prompt-injection isolation mechanism, mirroring how
    <vault_file> isolates vault content: history is data, not instructions.
    """
    if not session:
        return ""
    summary = (session.get("summary") or "").strip()
    messages = session.get("messages") or []
    if not summary and not messages:
        return ""

    parts: list[str] = ["## Conversation history (today)", ""]
    if summary:
        parts.append("<conversation_summary>")
        parts.append(summary)
        parts.append("</conversation_summary>")
        parts.append("")
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        ts = m.get("ts")
        if role == "user":
            tag = "prior_user_message"
        elif role == "assistant":
            tag = "prior_assistant_message"
        else:
            continue
        open_tag = f'<{tag} ts="{ts}">' if ts else f"<{tag}>"
        parts.append(f"{open_tag}{content}</{tag}>")
    return "\n".join(parts).rstrip() + "\n"


def generate_answer(user_text: str, files: dict[str, str],
                    stale_note: str = "") -> tuple[bool, str]:
    """
    Run stage 2. Returns (ok, reply_or_error).
    On success the second value is the reply text. On failure it's a short
    error code the caller turns into a reply via _error_to_reply().
    """
    if not CONVERSATIONAL_PROMPT_PATH.exists():
        log.error(f"[chat:stage2] prompt missing: {CONVERSATIONAL_PROMPT_PATH}")
        return False, "prompt-missing"

    if not VOICE_SPEC_PATH.exists():
        log.warning(f"[chat:stage2] voice spec missing: {VOICE_SPEC_PATH}")
        voice_spec = ""
    else:
        voice_spec = VOICE_SPEC_PATH.read_text()

    template = CONVERSATIONAL_PROMPT_PATH.read_text()
    system_prompt = template.replace("{VOICE_SPEC}", voice_spec)

    # Conversation history (v4 Phase 2) — read-only. Today's prior turns.
    # `sessions` is imported lazily and the whole read is guarded: importing
    # it runs init_db() against the sessions DB, and a missing DB / SQLite
    # error must never break the reply. On any failure we proceed with no
    # history and the model treats the conversation as fresh.
    history_section = ""
    try:
        import sessions
        session = sessions.get_chat_session(sessions.uk_today())
        history_section = _render_history_section(session)
    except Exception as e:
        log.warning(f"[chat:stage2] could not load chat history; "
                    f"proceeding without it: {e}")
        history_section = ""

    vault_block = _render_vault_block(files)
    vault_section = (
        "## Selected vault files\n\n" + vault_block
        if vault_block else
        "## Selected vault files\n\n(none — answer from general knowledge if appropriate)\n"
    )

    stale_section = (
        f"\n## Note on vault freshness\n\n{stale_note}\n"
        if stale_note else ""
    )

    prompt = (
        system_prompt
        + "\n\n---\n\n"
        + vault_section
        + stale_section
        + ("\n---\n\n" + history_section if history_section else "")
        + "\n---\n\n## User message\n\n"
        + f"<user_message>{user_text}</user_message>\n\n"
        + "---\n\nReply now as Veronica. Reply text only, nothing else.\n"
    )

    ok, output = _call_claude_chat(prompt, stage_label="stage2")
    if not ok:
        return False, output

    reply = output.strip()
    if not reply:
        log.error("[chat:stage2] empty reply from claude")
        return False, "empty-reply"

    return True, reply


# ---------------------------------------------------------------------------
# Vault sync
# ---------------------------------------------------------------------------

def _pull_vault() -> bool:
    """git pull on the vault clone. Returns True on success, False otherwise."""
    if not (VAULT_PATH / ".git").exists():
        log.warning(f"[chat] vault path is not a git repo: {VAULT_PATH}")
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(VAULT_PATH), "pull", "--ff-only"],
            capture_output=True,
            text=True,
            timeout=GIT_PULL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        log.warning(f"[chat] git pull timed out after {GIT_PULL_TIMEOUT}s")
        return False
    except FileNotFoundError:
        log.error("[chat] 'git' not on PATH")
        return False
    if result.returncode != 0:
        log.warning(f"[chat] git pull failed: {result.stderr.strip()[:200]}")
        return False
    return True


# ---------------------------------------------------------------------------
# Error → reply translation
# ---------------------------------------------------------------------------

def _error_to_reply(stage: str, code: str) -> str:
    """Map an internal error code to a chat reply string."""
    if code == "timeout":
        return "Hit a timeout on that one — try once more?"
    if code == "claude-not-found":
        return "Something's wrong with my setup — I can't reach Claude right now."
    if code == "prompt-missing":
        return "Something's wrong with my setup — a prompt file is missing."
    if code == "empty-reply":
        return "Got an empty response back — try once more?"
    if code.startswith("exit-"):
        return "Something went wrong on my end — try once more?"
    log.warning(f"[chat] unmapped error code in {stage}: {code}")
    return "Something went wrong — try once more?"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def handle_chat_message(user_text: str) -> str:
    """
    Single-message handler. Takes the user's text, runs the two-stage
    retrieval, returns the reply string. Never raises.
    """
    text = (user_text or "").strip()
    if not text:
        return "got it — anything to ask?"

    # Step 1 — vault sync (best effort)
    pull_ok = _pull_vault()
    stale_note = "" if pull_ok else (
        "The vault couldn't be refreshed just now — answer reflects the local "
        "state from the last successful pull. Mention this in your reply if it "
        "could affect accuracy."
    )

    # Step 2 — build index
    index = build_vault_index()
    if not index:
        return "Vault looks empty or unreachable from here — that's a setup problem, not yours."

    # Step 3 — file selection
    selected_paths, stage1_status = select_files(text, index)
    if stage1_status not in ("ok", "empty"):
        return _error_to_reply("stage1", stage1_status)

    # Step 4 — load selected files (skipped if empty)
    files = _load_selected_files(selected_paths) if selected_paths else {}

    # Step 5 — answer generation
    ok, result = generate_answer(text, files, stale_note=stale_note)
    if not ok:
        return _error_to_reply("stage2", result)

    # Step 6 — persist the exchange (v4 Phase 2, success path only).
    # Both turns written atomically after a successful reply. Same lazy-import
    # discipline as generate_answer's history read: importing `sessions` runs
    # init_db(), so it's imported inside this guarded block. Any persistence
    # failure is swallowed — the reply goes back to Telegram regardless. Failed
    # turns (early returns above) intentionally leave no trace in history.
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        import sessions
        ts = datetime.now(ZoneInfo("Europe/London")).strftime("%H:%M")
        sessions.append_chat_exchange(sessions.uk_today(), text, result, ts)
    except Exception as e:
        log.warning(f"[chat:persist] could not persist exchange; "
                    f"reply returned anyway: {e}")

    return result


# ---------------------------------------------------------------------------
# Rolling background compression (v4 Phase 2 Step 5)
# ---------------------------------------------------------------------------

def _pair_turns(turns: list) -> list:
    """Group a flat chronological turn list into (ts, user, assistant)
    exchange tuples. Turns are appended user-then-assistant per exchange, but
    the unsummarised slice can begin mid-exchange, so a leading orphan
    assistant turn and a trailing unanswered user turn are both tolerated."""
    exchanges: list = []
    i = 0
    while i < len(turns):
        t = turns[i] or {}
        if t.get("role") == "user":
            u = t.get("content", "")
            uts = t.get("ts", "")
            if i + 1 < len(turns) and (turns[i + 1] or {}).get("role") == "assistant":
                exchanges.append((uts, u, turns[i + 1].get("content", "")))
                i += 2
            else:
                exchanges.append((uts, u, ""))
                i += 1
        else:
            # Orphan assistant turn (slice started mid-exchange)
            exchanges.append((t.get("ts", ""), "", t.get("content", "")))
            i += 1
    return exchanges


def _render_new_exchanges(exchanges: list) -> str:
    """Render paired exchanges into the <new_exchanges> block the compression
    prompt expects."""
    parts: list[str] = ["<new_exchanges>"]
    for ets, u, a in exchanges:
        open_tag = f'<exchange ts="{ets}">' if ets else "<exchange>"
        parts.append(f"{open_tag}user: {u}\nassistant: {a}</exchange>")
    parts.append("</new_exchanges>")
    return "\n".join(parts)


def run_compression_check(date: str) -> None:
    """Background-task entry point. Decides whether to compress today's chat
    history, runs the compression call if so, writes the result. Never
    raises — invoked fire-and-forget via asyncio.to_thread from the bot
    listener after the reply has already been sent."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        import sessions
    except Exception as e:
        log.warning(f"[chat:compression] could not import sessions; skipping: {e}")
        return

    try:
        session = sessions.get_chat_session(date)
    except Exception as e:
        log.warning(f"[chat:compression] could not read session; skipping: {e}")
        return

    # Capture the clear epoch as of this read. If a /clear lands while the
    # compression model call is running, the epoch will have moved by the
    # time we re-check just before the write, and we abort rather than write
    # a stale summary onto a freshly-cleared row. The user's clear wins.
    start_epoch = session["clear_epoch"]

    # Lever 4 — conversation too short to be worth compressing yet.
    if session["exchange_count"] < COMPRESSION_MIN_EXCHANGES:
        return

    # Lever 2 — too little new material since the last compression.
    if session["unsummarised_turn_count"] < COMPRESSION_MIN_UNSUMMARISED:
        return

    try:
        turns = sessions.get_unsummarised_turns(date)
    except Exception as e:
        log.warning(f"[chat:compression] could not load unsummarised turns; "
                    f"skipping: {e}")
        return
    if not turns:
        return

    exchanges = _pair_turns(turns)

    # Lever 1 — every new exchange is short (small talk, acks). No information
    # was missed, so advance the buffer without spending a model call.
    # update_chat_summary resets unsummarised to 0; pass the unchanged summary
    # so the running record is preserved verbatim.
    if exchanges and all(
        len(u) + len(a) < COMPRESSION_SHORT_EXCHANGE_CHARS
        for _ts, u, a in exchanges
    ):
        try:
            sessions.update_chat_summary(date, session["summary"])
        except Exception as e:
            log.warning(f"[chat:compression] short-turn buffer reset failed: {e}")
            return
        log.info("[chat:compression] skipped — short turns only")
        return

    if not COMPRESSION_PROMPT_PATH.exists():
        log.warning(f"[chat:compression] prompt missing: {COMPRESSION_PROMPT_PATH}")
        return

    template = COMPRESSION_PROMPT_PATH.read_text()
    existing_summary = (session.get("summary") or "").strip()
    prompt = (
        template
        + "\n\n---\n\n"
        + f"<existing_summary>{existing_summary}</existing_summary>\n\n"
        + _render_new_exchanges(exchanges)
        + "\n\n---\n\nReturn the updated running summary now. "
        + "Prose only, 150-200 words, nothing else.\n"
    )

    ok, output = _call_claude_chat(
        prompt, stage_label="compression", timeout=COMPRESSION_TIMEOUT
    )
    if not ok:
        log.warning(f"[chat:compression] failed: {output}")
        return

    new_summary = output.strip()
    if not new_summary:
        log.warning("[chat:compression] failed: empty-reply")
        return

    # Epoch re-check — the last thing before the write. Compression already
    # paid its cost (the model call ran), but if a /clear intervened we drop
    # the result rather than resurrect a stale summary onto a cleared row.
    try:
        current_epoch = sessions.get_clear_epoch(date)
    except Exception as e:
        log.warning(f"[chat:compression] could not re-check clear epoch; "
                    f"skipping write: {e}")
        return
    if current_epoch != start_epoch:
        log.info(
            f"[chat:compression] aborted — clear intervened "
            f"(epoch {start_epoch} → {current_epoch})"
        )
        return

    ts = datetime.now(ZoneInfo("Europe/London")).isoformat(timespec="seconds")
    try:
        updated = sessions.apply_compression(date, new_summary, len(turns), ts)
    except Exception as e:
        log.warning(f"[chat:compression] could not write compressed summary: {e}")
        return

    log.info(
        f"[chat:compression] applied — {len(turns)} turns summarised, "
        f"count now {updated['compression_count']}"
    )


# ---------------------------------------------------------------------------
# CLI — manual testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    if len(sys.argv) < 2:
        print('Usage: chat_handler.py "your question here"')
        sys.exit(1)
    question = " ".join(sys.argv[1:])
    reply = handle_chat_message(question)
    print("\n=== REPLY ===\n")
    print(reply)
    print("\n=== END ===\n")
