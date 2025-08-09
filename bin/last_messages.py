#!/usr/bin/env python3
# requirements: pyperclip~=1.8.2
"""
last_messages.py  –  Dump the last N chat messages of a Claude Code session.

Usage:
    uv run last_messages.py <session-id> [n_messages]
    uv run last_messages.py <session-id> [n_messages] --no-clip
    uv run last_messages.py <session-id> --path /custom/file.jsonl

• Finds the transcript JSONL under ~/.claude/projects/**/ matching the session-id.
• Extracts user/assistant messages (full text, no truncation).
• Writes a text file to ~/copyq (configurable via $COPYQ_DIR) whose
  name sorts so **newest files appear first** in an A→Z listing.
• Copies the same text block to the clipboard (pyperclip).  
  In WSL, if pyperclip has no backend it falls back to clip.exe.

Author: 2025-08-01
"""

from __future__ import annotations
import argparse, json, os, sys, time, pathlib, subprocess
from datetime import datetime, timezone
from typing import List

try:
    import pyperclip
    _HAVE_PYPERCLIP = True
except ModuleNotFoundError:  # uv will install it, but keep guard for first run
    _HAVE_PYPERCLIP = False

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def find_transcript(session_id: str, explicit_path: str | None = None) -> pathlib.Path:
    """Locate the transcript JSONL file for the given session id."""
    if explicit_path:
        p = pathlib.Path(explicit_path).expanduser()
        if p.is_file():
            return p
        sys.exit(f"✗ --path {explicit_path} does not exist.")
    
    projects_root = pathlib.Path.home() / ".claude" / "projects"
    pattern = f"**/{session_id}.jsonl"
    matches = list(projects_root.glob(pattern))
    if not matches:
        sys.exit(f"✗ No transcript {session_id}.jsonl found beneath {projects_root}")
    if len(matches) > 1:
        print(f"⚠  Multiple transcripts found; using {matches[0]}", file=sys.stderr)
    return matches[0]

def extract_messages(path: pathlib.Path, limit: int) -> List[str]:
    """Return the last <limit> user/assistant/tool messages, oldest→newest."""
    msgs: List[str] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = entry.get("type") or entry.get("role")
            if role not in ("user", "assistant"):
                continue

            # Claude stores payload under entry["message"]["content"]
            content_parts = []
            msg_obj = entry.get("message") or {}
            cont = msg_obj.get("content")
            if isinstance(cont, list):
                for c in cont:
                    if c.get("type") == "text":
                        text = c.get("text", "")
                        if text.strip():
                            content_parts.append(text.strip())
                    elif c.get("type") == "tool_use":
                        # Extract tool call details
                        tool_name = c.get("name", "unknown")
                        tool_input = c.get("input", {})
                        tool_text = f"<ToolUse name=\"{tool_name}\">\n{json.dumps(tool_input, indent=2)}\n</ToolUse>"
                        content_parts.append(tool_text)
                    elif c.get("type") == "tool_result":
                        # Extract tool result - it's in 'content' field
                        output = c.get("content", "")
                        # Truncate very long outputs
                        if len(output) > 2000:
                            output = output[:2000] + "\n... [truncated]"
                        tool_result = f"<ToolResult>\n{output}\n</ToolResult>"
                        content_parts.append(tool_result)
            elif isinstance(cont, str):
                if cont.strip():
                    content_parts.append(cont.strip())
            
            if content_parts:
                # Join all content parts
                full_content = "\n\n".join(content_parts)
                # Format with XML tags
                msgs.append(f"<{role.capitalize()}>\n{full_content}\n</{role.capitalize()}>")
    return msgs[-limit:]  # keep last N

def write_file(text: str, session_id: str, out_dir: pathlib.Path) -> pathlib.Path:
    """Write <text> to out_dir with reverse-sorted filename."""
    out_dir.mkdir(parents=True, exist_ok=True)
    epoch = int(time.time())
    inv = 9_999_999_999 - epoch          # bigger epoch → smaller inv → sorts first
    fname = f"{inv:010d}_{session_id[:8]}.txt"
    dest = out_dir / fname
    dest.write_text(text, encoding="utf-8")
    return dest

def copy_clipboard(text: str):
    """Best-effort clipboard copy."""
    if _HAVE_PYPERCLIP:
        try:
            pyperclip.copy(text)
            print("✓ Copied to clipboard", file=sys.stderr)
            return
        except pyperclip.PyperclipException:
            pass   # fall through
    # Fallback for WSL → safe-clip or clip.exe (Windows clipboard)
    if "WSL_DISTRO_NAME" in os.environ:
        # Try safe-clip first (handles UTF-8 properly)
        try:
            subprocess.run(["safe-clip"], input=text, text=True, check=True)
            print("✓ Copied via safe-clip", file=sys.stderr)
            return
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fallback to direct clip.exe with UTF-16LE encoding
            try:
                # Convert to UTF-16LE for clip.exe
                subprocess.run(["clip.exe"], input=text.encode("utf-16le"), check=True)
                print("✓ Copied via clip.exe (UTF-16LE)", file=sys.stderr)
                return
            except Exception:
                pass
    print("⚠ Clipboard copy failed (install xclip/xsel or pyperclip backend).", file=sys.stderr)

# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main(argv: List[str] | None = None):
    parser = argparse.ArgumentParser(description="Dump last N Claude messages")
    parser.add_argument("session_id", help="Claude session UUID")
    parser.add_argument("n", nargs="?", type=int, default=50,
                        help="How many recent messages (default: 50)")
    parser.add_argument("--path", help="Explicit path to transcript JSONL")
    parser.add_argument("--no-clip", action="store_true", help="Skip clipboard copy")
    args = parser.parse_args(argv)

    transcript_path = find_transcript(args.session_id, args.path)
    msgs = extract_messages(transcript_path, args.n)
    if not msgs:
        sys.exit("✗ No user/assistant messages found.")

    header = (
        f"─ Session {args.session_id} (last {len(msgs)} messages) "
        f"{'-'*20}\n"
    )
    
    # Format messages with separators for better readability
    separator = "\n───\n"
    formatted_msgs = separator.join(msgs)
    
    # File output includes header
    file_output = header + formatted_msgs + "\n"
    
    # Clipboard output is wrapped in XML tags without header
    clipboard_output = f"<ai-transcript>\n{formatted_msgs}\n</ai-transcript>"

    copyq_dir = pathlib.Path(
        os.environ.get("COPYQ_DIR", "/home/edwin/copyq")
    ).expanduser()
    written_to = write_file(file_output, args.session_id, copyq_dir)
    print(f"✓ Wrote {written_to}", file=sys.stderr)

    if not args.no_clip:
        copy_clipboard(clipboard_output)

    # Also print to stdout so you can pipe/preview (file format)
    print(file_output)

if __name__ == "__main__":
    main()