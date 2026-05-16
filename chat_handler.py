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

    return result


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
