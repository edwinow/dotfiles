#!/usr/bin/env python3
"""
Background worker: reads recent transcript, sanitizes, consults GPT (configurable),
decides if a nudge is warranted, updates memory, and writes a pending feedback file.
Global version with per-project state isolation and line-based truncation.

No stdout/stderr noise. Designed to be spawned by sidekick-counter.py.
"""

import os, sys, json, time, argparse, re, hashlib
from pathlib import Path
from datetime import datetime, timezone
import requests

# Global base directory for all sidekick state
HOME_SIDE_DIR = Path("~/.claude/sidekick").expanduser()

def proj_slug(cwd: str) -> str:
    """Generate stable slug for project path"""
    return hashlib.sha256(cwd.encode("utf-8")).hexdigest()[:12]

def proj_dir(cwd: str) -> Path:
    """Get or create per-project state directory"""
    d = HOME_SIDE_DIR / proj_slug(cwd)
    d.mkdir(parents=True, exist_ok=True)
    return d

DEFAULT_MODEL = os.environ.get("SIDEKICK_MODEL", "gpt-5")  # Full GPT-5 model with high reasoning
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")          # required for API mode
API_URL = os.environ.get("SIDEKICK_API_URL", "https://api.openai.com/v1/chat/completions")

MAX_EVENTS = int(os.environ.get("SIDEKICK_MAX_EVENTS", "60"))   # last N messages to inspect
MAX_LINES_TOTAL = int(os.environ.get("SIDEKICK_MAX_LINES_TOTAL", "1200"))  # total line cap
MAX_BLOCK_LINES = int(os.environ.get("SIDEKICK_MAX_BLOCK_LINES", "500"))   # cap for code blocks
THRESHOLD  = float(os.environ.get("SIDEKICK_THRESHOLD", "0.2")) # score gate - lowered for more frequent nudges
NUDGE_TTL  = int(os.environ.get("SIDEKICK_NUDGE_TTL_SECONDS", "900"))

def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return default
    return default

def save_json(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    tmp.replace(path)

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def find_transcript(event, cwd):
    """Find transcript with project-scoped fallback only"""
    # Prefer explicit path from event
    tp = event.get("transcript_path")
    if tp and Path(tp).exists():
        return Path(tp)
    # Fallback ONLY within this project's .claude
    proj_claude = Path(cwd) / ".claude"
    candidates = list(proj_claude.rglob("*.jsonl")) if proj_claude.exists() else []
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None

def read_messages_since(transcript_path: Path, last_ts: str | None):
    msgs = []
    if not transcript_path or not transcript_path.exists():
        return msgs
    with transcript_path.open() as f:
        for line in f:
            try:
                entry = json.loads(line)
            except Exception:
                continue
            ts = entry.get("timestamp","")
            if last_ts and ts and ts <= last_ts:
                continue
            if entry.get("type") in ("user","assistant","system") and "message" in entry:
                msg = entry["message"]
                role = msg.get("role", entry["type"])
                content = msg.get("content", "")
                # flatten structured content
                text_parts = []
                tools = []
                if isinstance(content, list):
                    for c in content:
                        t = c.get("type")
                        if t == "text":
                            text_parts.append(c.get("text",""))
                        elif t == "tool_use":
                            name = c.get("name","")
                            tools.append(name)
                            # keep only key params
                            inp = c.get("input",{})
                            snip = json.dumps({k:inp.get(k) for k in ("command","file_path","paths","query") if k in inp}, ensure_ascii=False)
                            text_parts.append(f"[TOOL USE {name}] {snip}")
                        elif t == "tool_result":
                            out = c.get("output","")
                            # keep first 40 lines
                            lines = out.splitlines()
                            out = "\n".join(lines[:40])
                            text_parts.append(f"[TOOL RESULT] {out}")
                elif isinstance(content, str):
                    text_parts.append(content)
                text = "\n".join(text_parts).strip()
                if text:
                    msgs.append({"role": role, "text": text, "timestamp": ts, "tools": tools})
    return msgs

def truncate_block_lines(text: str, max_lines: int) -> str:
    """Truncate text to max lines"""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[:max_lines]) + f"\n... [truncated {len(lines)-max_lines} lines]"

def sanitize(messages):
    """
    Keep only useful signals with line-based truncation.
    """
    cleaned_chunks = []
    for m in messages[-MAX_EVENTS:]:
        txt = m["text"]
        # strip obvious noise
        txt = re.sub(r'(?i)\b(uuid|request_id|trace_id)\b[:=]\s*[a-f0-9-]+', '', txt)
        txt = re.sub(r'\x1b\[[0-9;]*m', '', txt)  # ANSI
        
        # truncate any ``` blocks by lines
        def _trim_block(match):
            block = match.group(0)
            head = block[:3]  # ```
            body = block[3:-3] if len(block) > 6 else ""
            tail = block[-3:] if len(block) > 6 else ""
            body_trim = truncate_block_lines(body, MAX_BLOCK_LINES)
            return head + body_trim + tail
        
        txt = re.sub(r"```[\s\S]*?```", _trim_block, txt)
        
        # tool_result already limited to 40 lines upstream
        cleaned_chunks.append(f"{m['role'].upper()} @ {m.get('timestamp','')}\n{txt}\n")

    # global line cap
    all_text = ("\n" + ("-"*60) + "\n").join(cleaned_chunks)
    lines = all_text.splitlines()
    if len(lines) > MAX_LINES_TOTAL:
        all_text = "\n".join(lines[:MAX_LINES_TOTAL]) + f"\n... [truncated {len(lines)-MAX_LINES_TOTAL} lines]"
    
    return all_text

def read_project_policy(cwd: str):
    # Pull the pnpm rules + project overview from your CLAUDE.md if present
    md_path = Path(cwd) / "CLAUDE.md"
    if not md_path.exists():
        md_path = Path(cwd) / ".claude" / "CLAUDE.md"
    if not md_path.exists():
        return ""
    text = md_path.read_text(encoding="utf-8", errors="ignore")
    # Keep the PNPM section + short overview
    keep = []
    capture = False
    for line in text.splitlines():
        if line.strip().startswith("## ") and "Package Manager" in line:
            capture = True
        if line.startswith("## ") and capture and "Package Manager" not in line:
            # stop after that section
            capture = False
        if capture:
            keep.append(line)
    # Always add one-sentence project fingerprint to guide the reviewer
    keep.append("\nProject fingerprint: Next.js 15, strict TS, Tailwind v4, pnpm-only.\n")
    return "\n".join(keep).strip()

def call_openai(model: str, system_prompt: str, user_payload: str, timeout=40):
    if not OPENAI_API_KEY:
        # Log to a file so user knows why nudges aren't appearing
        log_path = HOME_SIDE_DIR / "api_key_missing.log"
        log_path.write_text(f"API key missing at {now_iso()}\nSet OPENAI_API_KEY environment variable\n")
        raise RuntimeError("OPENAI_API_KEY not set for sidekick-review-worker")
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "temperature": 0.2,
        "reasoning_effort": "high",  # Use GPT-5's high reasoning mode for better analysis
        "messages": [
            {"role":"system","content": system_prompt},
            {"role":"user","content": user_payload}
        ]
    }
    r = requests.post(API_URL, headers=headers, json=body, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    content = data["choices"][0]["message"]["content"]
    return content

SCHEMA_HINT = """
Return ONLY compact JSON (no markdown) with this schema:
{
  "should_intervene": boolean,
  "score": number,            // 0..1 confidence that the nudge is useful
  "reason": string,           // <120 chars
  "nudge_markdown": string,   // actionable, <= 700 chars, include copy/paste commands in ```bash
  "commands": string[],       // 0..5 raw commands (no comments)
  "memory_update": string|null // <300 chars summary to append to long-term memory if helpful
}
"""

SYSTEM_PROMPT = """You are an expert code reviewer with deep reasoning capabilities, observing a developer using Claude Code.
Use your enhanced reasoning to identify subtle patterns, potential issues, and non-obvious improvements.
Intervene when you detect: repeated patterns indicating confusion, missing tests after risky changes,
policy violations (pnpm-only, Next.js 15, strict TypeScript), security/performance issues, architectural concerns,
or when you can suggest a significantly better approach the developer might be missing.
Be precise, actionable, and provide copy-pasteable solutions. Never suggest bun; always use pnpm.
Leverage the full context to understand the developer's intent and provide insightful guidance.
If nothing valuable to add, set should_intervene=false and a low score."""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--event-file", required=True)
    args = ap.parse_args()

    try:
        event_path = Path(args.event_file)
        event = json.loads(event_path.read_text())
        # Clean up temp file immediately after reading
        try:
            event_path.unlink()
        except:
            pass
    except Exception:
        return  # silent fail

    cwd = event.get("cwd", os.getcwd())
    
    # Per-project state files
    P_DIR = proj_dir(cwd)
    MEMORY_FILE = P_DIR / "memory.json"
    PENDING_FILE = P_DIR / "pending_feedback.json"
    
    transcript = find_transcript(event, cwd)
    memory = load_json(MEMORY_FILE, {"last_timestamp": None, "long_term_summary": "", "last_nudge_hash": ""})

    msgs = read_messages_since(transcript, memory.get("last_timestamp"))
    if not msgs:
        return

    sanitized = sanitize(msgs)
    policy = read_project_policy(cwd)

    # Build user payload (small and structured)
    payload = {
        "context": {
            "project_policy": policy,
            "long_term_summary": memory.get("long_term_summary","")[:2000],
            "recent_events": sanitized
        },
        "request": {
            "goal": "Detect if the developer would benefit from a brief, actionable nudge right now.",
            "house_rules": ["pnpm-only", "Next.js 15", "strict TS"],
            "output": "JSON",
            "schema": "see SCHEMA below"
        },
        "SCHEMA": SCHEMA_HINT.strip()
    }
    user_payload = json.dumps(payload, ensure_ascii=False)

    # Call model
    try:
        raw = call_openai(DEFAULT_MODEL, SYSTEM_PROMPT, user_payload)
    except Exception:
        return

    # Extract JSON robustly (allowing models that wrap it in backticks)
    m = re.search(r'\{[\s\S]*\}\s*$', raw.strip())
    text = m.group(0) if m else raw.strip()
    try:
        out = json.loads(text)
    except Exception:
        return

    should = bool(out.get("should_intervene"))
    score = float(out.get("score", 0.0))
    nudge = (out.get("nudge_markdown") or "").strip()
    cmds  = out.get("commands") or []
    memup = (out.get("memory_update") or "").strip()

    # De-dupe: avoid repeating the exact same nudge
    nudge_hash = hashlib.sha256(nudge.encode("utf-8")).hexdigest() if nudge else ""

    # Update memory
    lt = memory.get("long_term_summary","")
    if memup:
        lt = (lt + "\n" + memup).strip()[:4000]  # cap
        memory["long_term_summary"] = lt

    # Advance the pointer to the last processed timestamp
    last_ts = msgs[-1].get("timestamp") or memory.get("last_timestamp")
    memory["last_timestamp"] = last_ts
    save_json(MEMORY_FILE, memory)

    # Gate + write pending nudge
    if should and score >= THRESHOLD and nudge and nudge_hash != memory.get("last_nudge_hash"):
        payload = {
            "created_at": now_iso(),
            "ttl_seconds": NUDGE_TTL,
            "nudge_markdown": nudge,
            "commands": cmds,
            "score": score,
            "reason": out.get("reason","")
        }
        save_json(PENDING_FILE, payload)
        memory["last_nudge_hash"] = nudge_hash
        save_json(MEMORY_FILE, memory)

if __name__ == "__main__":
    main()