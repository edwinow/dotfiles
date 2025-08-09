#!/usr/bin/env python3
"""
Enhanced sidekick review worker with improved context management.
Changes:
- Increased message history (100 vs 60)
- Better memory structure with key decisions tracking
- Sliding window for older important context
- Full use of GPT-5's token capacity
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

DEFAULT_MODEL = os.environ.get("SIDEKICK_MODEL", "gpt-5")  # GPT-5 for advanced code review
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")          # required for API mode
API_URL = os.environ.get("SIDEKICK_API_URL", "https://api.openai.com/v1/chat/completions")

# ENHANCED: Increased limits for better context
MAX_EVENTS = int(os.environ.get("SIDEKICK_MAX_EVENTS", "100"))      # More messages (was 60)
MAX_LINES_TOTAL = int(os.environ.get("SIDEKICK_MAX_LINES_TOTAL", "2000"))  # More lines (was 1200)
MAX_BLOCK_LINES = int(os.environ.get("SIDEKICK_MAX_BLOCK_LINES", "500"))   
THRESHOLD  = float(os.environ.get("SIDEKICK_THRESHOLD", "0")) # always show nudges
NUDGE_TTL  = int(os.environ.get("SIDEKICK_NUDGE_TTL_SECONDS", "900"))

def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return default
    return default

def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(obj, f, indent=2)

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def find_transcript(event, cwd):
    """Find transcript with project-scoped fallback only"""
    tp = event.get("transcript_path")
    if tp and Path(tp).exists():
        return Path(tp)
    proj_claude = Path(cwd) / ".claude"
    candidates = list(proj_claude.rglob("*.jsonl")) if proj_claude.exists() else []
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None

def extract_key_info(msg):
    """Extract important information from a message for memory"""
    text = msg.get("text", "")
    tools = msg.get("tools", [])
    
    # Key patterns to remember
    key_events = []
    if any(tool in tools for tool in ["Write", "MultiEdit", "Edit"]):
        if "test" in text.lower():
            key_events.append("wrote_tests")
        if "fix" in text.lower() or "bug" in text.lower():
            key_events.append("bug_fix")
        if "refactor" in text.lower():
            key_events.append("refactoring")
    
    if "error" in text.lower() or "failed" in text.lower():
        key_events.append("error_encountered")
    
    if "security" in text.lower() or "auth" in text.lower():
        key_events.append("security_related")
        
    return key_events

def read_messages_since(transcript_path: Path, last_ts: str | None):
    msgs = []
    key_decisions = []  # ENHANCED: Track important decisions
    
    if not transcript_path or not transcript_path.exists():
        return msgs, key_decisions
        
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
                            inp = c.get("input",{})
                            # Keep more context for important tools
                            if name in ["Edit", "Write", "MultiEdit"]:
                                snip = json.dumps(inp, ensure_ascii=False)[:200]
                            else:
                                snip = json.dumps({k:inp.get(k) for k in ("command","file_path","paths","query") if k in inp}, ensure_ascii=False)
                            text_parts.append(f"[TOOL USE {name}] {snip}")
                        elif t == "tool_result":
                            out = c.get("content","")
                            lines = out.splitlines()
                            out = "\n".join(lines[:40])
                            text_parts.append(f"[TOOL RESULT] {out}")
                elif isinstance(content, str):
                    text_parts.append(content)
                text = "\n".join(text_parts).strip()
                if text:
                    msg_data = {"role": role, "text": text, "timestamp": ts, "tools": tools}
                    msgs.append(msg_data)
                    
                    # Extract key decisions
                    key_info = extract_key_info(msg_data)
                    if key_info:
                        key_decisions.append({
                            "timestamp": ts,
                            "events": key_info,
                            "summary": text[:100]
                        })
    
    return msgs, key_decisions

def truncate_block_lines(text: str, max_lines: int) -> str:
    """Truncate text to max lines"""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[:max_lines]) + f"\n... [truncated {len(lines)-max_lines} lines]"

def sanitize(messages, include_older=False):
    """Keep only useful signals with line-based truncation."""
    # ENHANCED: Option to include older context
    if include_older:
        # Include a sample of older messages for continuity
        older = messages[:-MAX_EVENTS] if len(messages) > MAX_EVENTS else []
        recent = messages[-MAX_EVENTS:]
        
        # Sample every 5th older message
        sampled_older = older[::5][-20:]  # Last 20 sampled messages
        messages_to_process = sampled_older + recent
    else:
        messages_to_process = messages[-MAX_EVENTS:]
    
    cleaned_chunks = []
    for m in messages_to_process:
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
    # Keep first 1000 chars for project context
    return text[:1000].strip()

def call_openai(model: str, system_prompt: str, user_payload: str, timeout=40):
    if not OPENAI_API_KEY:
        log_path = HOME_SIDE_DIR / "api_key_missing.log"
        log_path.write_text(f"API key missing at {now_iso()}\nSet OPENAI_API_KEY environment variable\n")
        raise RuntimeError("OPENAI_API_KEY not set for sidekick-review-worker")
    
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    
    # GPT-5 requires different parameters
    if "gpt-5" in model:
        body = {
            "model": model,
            "messages": [
                {"role":"system","content": system_prompt},
                {"role":"user","content": user_payload}
            ],
            "max_completion_tokens": 10000,
            "reasoning_effort": "high"  # Enable high reasoning for GPT-5
        }
    else:
        body = {
            "model": model,
            "temperature": 0.2,
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
  "memory_update": string|null, // <300 chars summary to append to long-term memory if helpful
  "key_decisions": string[]   // Important decisions/events to remember
}
"""

SYSTEM_PROMPT = """You are an expert code reviewer with deep reasoning capabilities, observing a developer using Claude Code.
Use your enhanced reasoning to identify subtle patterns, potential issues, and non-obvious improvements.
You have access to conversation history including key decisions and patterns over time.
Intervene when you detect: repeated patterns indicating confusion, missing tests after risky changes,
policy violations, security/performance issues, architectural concerns, or when you can suggest 
a significantly better approach the developer might be missing.
Be precise, actionable, and provide copy-pasteable solutions.
Leverage the full context including historical patterns to provide insightful guidance."""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--event-file", required=True)
    args = ap.parse_args()

    try:
        event_path = Path(args.event_file)
        event = json.loads(event_path.read_text())
        try:
            event_path.unlink()
        except:
            pass
    except Exception:
        return

    cwd = event.get("cwd", os.getcwd())
    
    # Per-project state files
    P_DIR = proj_dir(cwd)
    MEMORY_FILE = P_DIR / "memory.json"
    PENDING_FILE = P_DIR / "pending_feedback.json"
    
    transcript = find_transcript(event, cwd)
    memory = load_json(MEMORY_FILE, {
        "last_timestamp": None, 
        "long_term_summary": "", 
        "last_nudge_hash": "",
        "key_decisions": [],  # ENHANCED: Track important decisions
        "error_patterns": [],  # ENHANCED: Track recurring errors
        "session_count": 0
    })

    msgs, new_key_decisions = read_messages_since(transcript, memory.get("last_timestamp"))
    if not msgs:
        return

    # ENHANCED: Update key decisions history
    memory["key_decisions"] = (memory.get("key_decisions", []) + new_key_decisions)[-50:]  # Keep last 50
    memory["session_count"] = memory.get("session_count", 0) + 1

    sanitized = sanitize(msgs, include_older=True)  # Include older context
    policy = read_project_policy(cwd)

    # Build enhanced payload with more context
    payload = {
        "context": {
            "project_policy": policy,
            "long_term_summary": memory.get("long_term_summary","")[:3500],  # Use more (was 2000)
            "key_decisions": memory.get("key_decisions", [])[-20:],  # Last 20 key decisions
            "session_number": memory.get("session_count", 0),
            "recent_events": sanitized
        },
        "request": {
            "goal": "Detect if the developer would benefit from a brief, actionable nudge right now.",
            "context_note": "You have access to historical patterns and key decisions",
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

    # Extract JSON robustly
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
    key_decs = out.get("key_decisions") or []

    # De-dupe: avoid repeating the exact same nudge
    nudge_hash = hashlib.sha256(nudge.encode("utf-8")).hexdigest() if nudge else ""

    # Update memory with enhanced tracking
    lt = memory.get("long_term_summary","")
    if memup:
        lt = (lt + "\n[Session " + str(memory["session_count"]) + "] " + memup).strip()[:4000]
        memory["long_term_summary"] = lt
    
    # Add new key decisions
    for kd in key_decs:
        memory["key_decisions"].append({
            "timestamp": now_iso(),
            "decision": kd
        })
    memory["key_decisions"] = memory["key_decisions"][-50:]  # Keep last 50

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
            "reason": out.get("reason",""),
            "session_context": f"Session {memory['session_count']}, {len(msgs)} new messages"
        }
        save_json(PENDING_FILE, payload)
        memory["last_nudge_hash"] = nudge_hash
        save_json(MEMORY_FILE, memory)

if __name__ == "__main__":
    main()