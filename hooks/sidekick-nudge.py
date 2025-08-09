#!/usr/bin/env python3
"""
PreToolUse hook: if a pending feedback file exists and is fresh, print it to stderr and consume it.
Use exit(1) so Claude shows it but doesn't block the tool.
Global version with per-project state isolation.
"""

import json, sys, os, time, hashlib
from pathlib import Path
from datetime import datetime, timezone

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

def main():
    # Read event from stdin to get cwd
    try:
        data_in = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    
    cwd = data_in.get("cwd", os.getcwd())
    
    # Per-project pending file
    PENDING = proj_dir(cwd) / "pending_feedback.json"
    
    if not PENDING.exists():
        sys.exit(0)
    
    try:
        data = json.loads(PENDING.read_text())
    except Exception:
        sys.exit(0)

    created = data.get("created_at")
    ttl = int(data.get("ttl_seconds", 600))
    try:
        created_ts = datetime.fromisoformat(created.replace("Z","+00:00"))
    except Exception:
        created_ts = datetime.now(timezone.utc)

    age = (datetime.now(timezone.utc) - created_ts).total_seconds()
    if age > ttl:
        try: PENDING.unlink()
        except Exception: pass
        sys.exit(0)

    nudge = data.get("nudge_markdown","").strip()
    reason = data.get("reason","")
    if nudge:
        print("🤖 GPT-5 Sidekick suggestion", file=sys.stderr)
        if reason:
            print(f"_Why_: {reason}\n", file=sys.stderr)
        print(nudge, file=sys.stderr)
        # consume it so it doesn't repeat
        try: PENDING.unlink()
        except Exception: pass
        sys.exit(1)  # non-blocking display

    sys.exit(0)

if __name__ == "__main__":
    main()