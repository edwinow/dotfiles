#!/usr/bin/env python3
"""
PostToolUse hook: increment a counter and occasionally launch a background review.
Non-blocking: always returns fast. Uses a cooldown to avoid hammering the API.
Global version with per-project state isolation.
"""

import json, sys, os, time, subprocess, tempfile, hashlib
from pathlib import Path

# Global base directory for all sidekick state
HOME_SIDE_DIR = Path("~/.claude/sidekick").expanduser()

def proj_slug(cwd: str) -> str:
    """Generate stable slug for project path"""
    h = hashlib.sha256(cwd.encode("utf-8")).hexdigest()[:12]
    return h

def proj_dir(cwd: str) -> Path:
    """Get or create per-project state directory"""
    d = HOME_SIDE_DIR / proj_slug(cwd)
    d.mkdir(parents=True, exist_ok=True)
    return d

INTERVAL = int(os.environ.get("SIDEKICK_INTERVAL", "10"))           # every N tool calls
COOLDOWN_S = int(os.environ.get("SIDEKICK_COOLDOWN_SECONDS", "120")) # min seconds between reviews

def read_json_stdin():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}

def load_state(state_file):
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except Exception:
            return {}
    return {}

def save_state(state_file, data):
    # Ensure parent directory exists
    state_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Use tempfile for atomic write operation
    with tempfile.NamedTemporaryFile(mode='w', dir=state_file.parent, 
                                     delete=False, suffix='.tmp') as tmp:
        json.dump(data, tmp, indent=2)
        tmp_path = Path(tmp.name)
    
    # Atomic replace
    tmp_path.replace(state_file)

def main():
    event = read_json_stdin()
    cwd = event.get("cwd", os.getcwd())
    
    # Per-project state file
    P_DIR = proj_dir(cwd)
    STATE_FILE = P_DIR / "state.json"
    
    now = time.time()

    s = load_state(STATE_FILE)
    count = int(s.get("count", 0)) + 1
    last_run = float(s.get("last_run", 0))
    s["count"] = count
    save_state(STATE_FILE, s)

    should_launch = (count % INTERVAL == 0) and (now - last_run >= COOLDOWN_S)

    if should_launch:
        # persist event to a temp file the worker can read
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(event, f)
            tmp_path = f.name

        # Global worker path
        worker = HOME_SIDE_DIR.parent / "hooks" / "sidekick-review-worker.py"
        
        # Fire-and-forget background worker (only if worker exists)
        try:
            if worker.exists():
                subprocess.Popen(
                    ["python3", str(worker), "--event-file", tmp_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
                s["last_run"] = now
                save_state(STATE_FILE, s)
        except Exception:
            # best effort; never block Claude
            pass

    sys.exit(0)

if __name__ == "__main__":
    main()