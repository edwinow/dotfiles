#!/usr/bin/env python3
"""
Advanced sidekick with sophisticated memory management.
Features:
- Git integration (commits, branches, changes)
- Progressive message summarization (not sampling)
- Feature lifecycle tracking
- 10K character memory utilization
- Visible memory file with symlink
- Rollback and removal suggestions
"""

import os, sys, json, time, argparse, re, hashlib, subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
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

DEFAULT_MODEL = os.environ.get("SIDEKICK_MODEL", "gpt-5")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
API_URL = os.environ.get("SIDEKICK_API_URL", "https://api.openai.com/v1/chat/completions")

# Focus on quality over quantity
MAX_RECENT_MSGS = 50  # Only the most recent messages in detail
MAX_LINES_TOTAL = 1500
THRESHOLD = 0
NUDGE_TTL = 900

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
        json.dump(obj, f, indent=2, default=str)

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def get_git_context(cwd: str) -> dict:
    """Get rich git context from the project"""
    git_info = {
        "branch": "unknown",
        "recent_commits": [],
        "uncommitted_changes": 0,
        "last_commit_message": "",
        "commit_frequency": "unknown"
    }
    
    try:
        # Current branch
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=cwd, capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            git_info["branch"] = result.stdout.strip()
        
        # Recent commits with stats
        result = subprocess.run(
            ["git", "log", "--oneline", "--stat", "-n", "10"],
            cwd=cwd, capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            commits = []
            current_commit = []
            for line in result.stdout.splitlines():
                if re.match(r'^[a-f0-9]{7,}', line):
                    if current_commit:
                        commits.append(" ".join(current_commit))
                    current_commit = [line]
                elif line.strip() and current_commit:
                    # Stats line - extract key info
                    if "files changed" in line:
                        current_commit.append(f"({line.strip()})")
            if current_commit:
                commits.append(" ".join(current_commit))
            git_info["recent_commits"] = commits[:10]
            
            if commits:
                git_info["last_commit_message"] = commits[0].split(' ', 1)[1] if ' ' in commits[0] else commits[0]
        
        # Uncommitted changes
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd, capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            git_info["uncommitted_changes"] = len(result.stdout.splitlines())
        
        # Commit frequency (commits in last 7 days)
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()
        result = subprocess.run(
            ["git", "rev-list", "--count", f"--since={week_ago}", "HEAD"],
            cwd=cwd, capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            count = int(result.stdout.strip())
            if count > 20:
                git_info["commit_frequency"] = "very active"
            elif count > 10:
                git_info["commit_frequency"] = "active"
            elif count > 3:
                git_info["commit_frequency"] = "moderate"
            else:
                git_info["commit_frequency"] = "low"
                
    except Exception:
        pass  # Git operations are optional
    
    return git_info

def get_project_context(cwd: str) -> dict:
    """Analyze project structure and technology stack"""
    context = {
        "languages": [],
        "frameworks": [],
        "build_tools": [],
        "test_frameworks": [],
        "has_tests": False,
        "project_type": "unknown"
    }
    
    project_path = Path(cwd)
    
    # Check for common files
    files_to_check = {
        "package.json": ("js", "node"),
        "requirements.txt": ("python", None),
        "pyproject.toml": ("python", None),
        "Cargo.toml": ("rust", None),
        "go.mod": ("go", None),
        "pom.xml": ("java", "maven"),
        "build.gradle": ("java", "gradle"),
        "composer.json": ("php", None),
        "Gemfile": ("ruby", None),
        ".csproj": ("csharp", None),
        "tsconfig.json": ("typescript", None),
        "next.config.js": ("js", "nextjs"),
        "vite.config.js": ("js", "vite"),
        "webpack.config.js": ("js", "webpack"),
    }
    
    for file, (lang, framework) in files_to_check.items():
        if (project_path / file).exists() or list(project_path.glob(f"**/{file}")):
            if lang and lang not in context["languages"]:
                context["languages"].append(lang)
            if framework and framework not in context["frameworks"]:
                context["frameworks"].append(framework)
    
    # Check for test directories
    test_dirs = ["test", "tests", "__tests__", "spec", "test_*.py", "*_test.go"]
    for pattern in test_dirs:
        if list(project_path.glob(f"**/{pattern}")):
            context["has_tests"] = True
            break
    
    # Determine project type
    if "nextjs" in context["frameworks"]:
        context["project_type"] = "Next.js web app"
    elif "vite" in context["frameworks"] or "webpack" in context["frameworks"]:
        context["project_type"] = "Frontend web app"
    elif "node" in context["languages"] and (project_path / "server.js").exists():
        context["project_type"] = "Node.js server"
    elif "python" in context["languages"]:
        if (project_path / "manage.py").exists():
            context["project_type"] = "Django app"
        elif (project_path / "app.py").exists():
            context["project_type"] = "Flask/Python app"
        else:
            context["project_type"] = "Python project"
    
    return context

def summarize_messages(messages: list, max_chars: int = 2000) -> str:
    """Create a smart summary of messages focusing on key information"""
    if not messages:
        return ""
    
    summary_parts = []
    
    # Group messages by role and extract key patterns
    user_intents = []
    assistant_actions = []
    errors_encountered = []
    files_modified = []
    tests_written = False
    
    for msg in messages:
        text = msg.get("text", "")
        role = msg.get("role", "")
        tools = msg.get("tools", [])
        
        # Extract user intent
        if role == "user" and len(text) < 200:
            user_intents.append(text[:100])
        
        # Track assistant actions
        if role == "assistant":
            if "Edit" in tools or "Write" in tools or "MultiEdit" in tools:
                # Extract file paths
                file_matches = re.findall(r'["\']([^"\']*\.[a-z]+)["\']', text)
                files_modified.extend(file_matches[:3])
                
            if "test" in text.lower():
                tests_written = True
                
        # Track errors
        if "error" in text.lower() or "failed" in text.lower():
            error_snippet = text[:150]
            if error_snippet not in errors_encountered:
                errors_encountered.append(error_snippet)
    
    # Build structured summary
    if user_intents:
        summary_parts.append(f"User requests: {'; '.join(user_intents[-5:])}")
    
    if files_modified:
        unique_files = list(dict.fromkeys(files_modified))  # Remove duplicates
        summary_parts.append(f"Files modified: {', '.join(unique_files[-10:])}")
    
    if errors_encountered:
        summary_parts.append(f"Errors: {'; '.join(errors_encountered[-3:])}")
    
    if tests_written:
        summary_parts.append("Tests were written/modified")
    
    # Add activity summary
    tool_usage = {}
    for msg in messages:
        for tool in msg.get("tools", []):
            tool_usage[tool] = tool_usage.get(tool, 0) + 1
    
    if tool_usage:
        top_tools = sorted(tool_usage.items(), key=lambda x: x[1], reverse=True)[:5]
        summary_parts.append(f"Tools used: {', '.join([f'{t}({c})' for t, c in top_tools])}")
    
    summary = "\n".join(summary_parts)
    if len(summary) > max_chars:
        summary = summary[:max_chars-3] + "..."
    
    return summary

def create_memory_symlink(cwd: str, memory_file: Path):
    """Create a visible symlink to memory in project .claude directory"""
    claude_dir = Path(cwd) / ".claude"
    if claude_dir.exists():
        symlink_path = claude_dir / "memory.json"
        try:
            if symlink_path.exists() or symlink_path.is_symlink():
                symlink_path.unlink()
            symlink_path.symlink_to(memory_file)
        except Exception:
            pass  # Symlink creation is optional

def read_messages_since(transcript_path: Path, last_ts: str | None):
    """Read messages and extract structured information"""
    msgs = []
    feature_changes = []
    
    if not transcript_path or not transcript_path.exists():
        return msgs, feature_changes
        
    with transcript_path.open() as f:
        for line in f:
            try:
                entry = json.loads(line)
            except Exception:
                continue
            
            ts = entry.get("timestamp","")
            if last_ts and ts and ts <= last_ts:
                continue
                
            if entry.get("type") in ("user","assistant") and "message" in entry:
                msg = entry["message"]
                role = msg.get("role", entry["type"])
                content = msg.get("content", "")
                
                # Process content
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
                            
                            # Track feature changes
                            if name in ["Write", "Edit", "MultiEdit"]:
                                file_path = inp.get("file_path", "")
                                if "add" in text_parts[-1].lower() or "implement" in text_parts[-1].lower():
                                    feature_changes.append({
                                        "type": "addition",
                                        "file": file_path,
                                        "timestamp": ts
                                    })
                                elif "remove" in text_parts[-1].lower() or "delete" in text_parts[-1].lower():
                                    feature_changes.append({
                                        "type": "removal",
                                        "file": file_path,
                                        "timestamp": ts
                                    })
                            
                            # Compact representation
                            if name in ["Edit", "Write", "MultiEdit"]:
                                text_parts.append(f"[{name}: {inp.get('file_path', 'unknown')}]")
                            else:
                                text_parts.append(f"[{name}]")
                                
                        elif t == "tool_result":
                            out = c.get("content","")[:200]
                            if "error" in out.lower():
                                text_parts.append(f"[ERROR: {out[:100]}]")
                                
                elif isinstance(content, str):
                    text_parts.append(content)
                    
                text = "\n".join(text_parts).strip()
                if text:
                    msgs.append({
                        "role": role,
                        "text": text,
                        "timestamp": ts,
                        "tools": tools
                    })
    
    return msgs, feature_changes

def build_rich_memory(memory: dict, new_messages: list, feature_changes: list, 
                      git_context: dict, project_context: dict) -> dict:
    """Build a comprehensive memory structure optimized for signal"""
    
    # Update session tracking
    memory["sessions"] = memory.get("sessions", 0) + 1
    memory["last_updated"] = now_iso()
    
    # 1. Project Context (static, updated occasionally)
    if not memory.get("project_context") or memory["sessions"] % 10 == 0:
        memory["project_context"] = project_context
    
    # 2. Git Context (dynamic, updated each time)
    memory["git_context"] = git_context
    
    # 3. Progressive Conversation Summary (10K chars available!)
    if not memory.get("conversation_summary"):
        memory["conversation_summary"] = {
            "recent": "",      # Last session
            "rolling": "",     # Last 5 sessions
            "historical": ""   # All time
        }
    
    # Summarize new messages
    new_summary = summarize_messages(new_messages, max_chars=1500)
    
    # Update conversation summaries with cascading
    memory["conversation_summary"]["recent"] = new_summary
    
    # Rolling summary combines last few recents
    rolling = memory["conversation_summary"].get("rolling", "")
    rolling = f"Session {memory['sessions']}: {new_summary}\n{rolling}"[:3000]
    memory["conversation_summary"]["rolling"] = rolling
    
    # Historical gets key points from rolling
    if memory["sessions"] % 5 == 0 and rolling:
        historical = memory["conversation_summary"].get("historical", "")
        key_points = extract_key_points(rolling)
        historical = f"{key_points}\n{historical}"[:2000]
        memory["conversation_summary"]["historical"] = historical
    
    # 4. Feature Lifecycle Tracking
    if not memory.get("feature_lifecycle"):
        memory["feature_lifecycle"] = {
            "active_features": [],
            "completed_features": [],
            "struggling_features": [],
            "removed_features": []
        }
    
    # Process feature changes
    for change in feature_changes:
        feature_id = f"{change['file']}_{change['timestamp'][:10]}"
        if change["type"] == "addition":
            memory["feature_lifecycle"]["active_features"].append({
                "id": feature_id,
                "file": change["file"],
                "started": change["timestamp"],
                "iterations": 1
            })
        elif change["type"] == "removal":
            memory["feature_lifecycle"]["removed_features"].append({
                "id": feature_id,
                "file": change["file"],
                "removed": change["timestamp"]
            })
    
    # Identify struggling features (edited many times)
    edit_counts = {}
    for msg in new_messages:
        for tool in msg.get("tools", []):
            if tool in ["Edit", "MultiEdit"]:
                file_match = re.search(r'([^/\\]+\.[a-z]+)', msg["text"])
                if file_match:
                    file = file_match.group(1)
                    edit_counts[file] = edit_counts.get(file, 0) + 1
    
    for file, count in edit_counts.items():
        if count > 5:  # Edited more than 5 times
            memory["feature_lifecycle"]["struggling_features"].append({
                "file": file,
                "edit_count": count,
                "session": memory["sessions"],
                "suggestion": f"Consider reverting changes to {file} and trying a different approach"
            })
    
    # Keep lists bounded
    for key in ["active_features", "completed_features", "struggling_features", "removed_features"]:
        memory["feature_lifecycle"][key] = memory["feature_lifecycle"][key][-20:]
    
    # 5. Technical Decisions and Patterns
    if not memory.get("technical_decisions"):
        memory["technical_decisions"] = []
    
    # Extract technical decisions from messages
    for msg in new_messages:
        text = msg["text"].lower()
        if any(keyword in text for keyword in ["decided", "chose", "using", "switched to", "migrated"]):
            if len(msg["text"]) < 300:
                memory["technical_decisions"].append({
                    "decision": msg["text"][:200],
                    "timestamp": msg["timestamp"],
                    "session": memory["sessions"]
                })
    
    memory["technical_decisions"] = memory["technical_decisions"][-30:]
    
    # 6. Error Patterns and Solutions
    if not memory.get("error_patterns"):
        memory["error_patterns"] = {}
    
    for msg in new_messages:
        if "error" in msg["text"].lower():
            error_type = "unknown"
            if "typescript" in msg["text"].lower() or "type error" in msg["text"].lower():
                error_type = "typescript"
            elif "import" in msg["text"].lower():
                error_type = "import"
            elif "undefined" in msg["text"].lower() or "null" in msg["text"].lower():
                error_type = "null_reference"
            
            memory["error_patterns"][error_type] = memory["error_patterns"].get(error_type, 0) + 1
    
    # 7. Recommendations for rollback
    memory["recommendations"] = []
    
    # Check if we should recommend rollback
    if memory["feature_lifecycle"]["struggling_features"]:
        latest_struggle = memory["feature_lifecycle"]["struggling_features"][-1]
        if git_context["recent_commits"]:
            memory["recommendations"].append({
                "type": "rollback",
                "reason": f"File {latest_struggle['file']} edited {latest_struggle['edit_count']} times",
                "suggestion": f"git reset --soft HEAD~1 to undo last commit and try different approach",
                "confidence": 0.7
            })
    
    # Check if tests are needed
    if project_context.get("has_tests") == False and memory["sessions"] > 5:
        memory["recommendations"].append({
            "type": "testing",
            "reason": "No test directory found after multiple sessions",
            "suggestion": "Initialize test framework: pnpm add -D jest @types/jest",
            "confidence": 0.9
        })
    
    return memory

def extract_key_points(text: str, max_points: int = 5) -> str:
    """Extract key points from text - simple heuristic version"""
    lines = text.split('\n')
    key_lines = []
    
    keywords = ["error", "fixed", "implemented", "added", "removed", "bug", "feature", 
                "refactor", "test", "deploy", "security", "performance"]
    
    for line in lines:
        if any(keyword in line.lower() for keyword in keywords):
            key_lines.append(line[:150])
            if len(key_lines) >= max_points:
                break
    
    return "\n".join(key_lines)

def call_openai(model: str, system_prompt: str, user_payload: str, timeout=60):
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")
    
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    
    if "gpt-5" in model:
        body = {
            "model": model,
            "messages": [
                {"role":"system","content": system_prompt},
                {"role":"user","content": user_payload}
            ],
            "max_completion_tokens": 10000,
            "reasoning_effort": "high"
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
    return r.json()["choices"][0]["message"]["content"]

SCHEMA_HINT = """
{
  "should_intervene": boolean,
  "score": number (0-1),
  "reason": string (<120 chars),
  "nudge_markdown": string (<1000 chars with specific commands),
  "commands": string[] (0-5 executable commands),
  "memory_insights": string (key observations about patterns),
  "feature_recommendations": {
    "continue": string[],
    "reconsider": string[],
    "rollback": string[]
  }
}
"""

SYSTEM_PROMPT = """You are an advanced architectural sidekick with access to comprehensive project memory.
You see: git history, project evolution, conversation summaries, feature lifecycle, error patterns, and technical decisions.
Your role is to provide strategic guidance, not just tactical fixes.

Key responsibilities:
1. Identify when features are struggling (many edits, errors) and suggest rollbacks
2. Track technical debt and suggest refactoring opportunities  
3. Ensure consistency with past decisions recorded in memory
4. Prevent repeated mistakes by learning from error patterns
5. Guide architectural decisions based on project trajectory

Be strategic, not just reactive. If you see 10 commits struggling with one feature, suggest reverting.
If errors repeat across sessions, address the root cause.
Your memory spans sessions - use it to provide continuity and wisdom."""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--event-file", required=True)
    args = ap.parse_args()

    try:
        event_path = Path(args.event_file)
        event = json.loads(event_path.read_text())
        event_path.unlink()
    except Exception:
        return

    cwd = event.get("cwd", os.getcwd())
    
    # Per-project state files
    P_DIR = proj_dir(cwd)
    MEMORY_FILE = P_DIR / "memory.json"
    PENDING_FILE = P_DIR / "pending_feedback.json"
    
    # Load memory
    memory = load_json(MEMORY_FILE, {
        "last_timestamp": None,
        "sessions": 0,
        "project_context": {},
        "conversation_summary": {},
        "feature_lifecycle": {},
        "technical_decisions": [],
        "error_patterns": {},
        "recommendations": []
    })
    
    # Get git and project context
    git_context = get_git_context(cwd)
    project_context = get_project_context(cwd)
    
    # Read new messages
    transcript = event.get("transcript_path")
    if transcript:
        transcript = Path(transcript)
    else:
        # Find latest transcript
        proj_claude = Path(cwd) / ".claude"
        if proj_claude.exists():
            candidates = list(proj_claude.rglob("*.jsonl"))
            if candidates:
                transcript = max(candidates, key=lambda p: p.stat().st_mtime)
    
    if not transcript:
        return
    
    msgs, feature_changes = read_messages_since(transcript, memory.get("last_timestamp"))
    if not msgs:
        return
    
    # Build rich memory structure
    memory = build_rich_memory(memory, msgs, feature_changes, git_context, project_context)
    
    # Create visible symlink
    create_memory_symlink(cwd, MEMORY_FILE)
    
    # Prepare GPT-5 payload with rich context (using our 10K budget wisely)
    recent_messages = "\n---\n".join([f"{m['role']}: {m['text'][:200]}" for m in msgs[-30:]])
    
    payload = {
        "memory": {
            "sessions_count": memory["sessions"],
            "project_type": memory["project_context"].get("project_type", "unknown"),
            "languages": memory["project_context"].get("languages", []),
            "has_tests": memory["project_context"].get("has_tests", False),
            "git_branch": git_context["branch"],
            "recent_commits": git_context["recent_commits"][:5],
            "uncommitted_changes": git_context["uncommitted_changes"],
            "conversation_summary": memory["conversation_summary"],
            "struggling_features": memory["feature_lifecycle"].get("struggling_features", [])[-3:],
            "recent_technical_decisions": memory["technical_decisions"][-5:],
            "error_patterns": dict(list(memory["error_patterns"].items())[-5:]),
            "active_recommendations": memory["recommendations"][:3]
        },
        "recent_activity": recent_messages,
        "request": {
            "analyze": "Review the memory and recent activity",
            "consider": "Should you intervene with strategic guidance?",
            "focus": "Rollbacks, architectural issues, repeated patterns",
            "output": "JSON only"
        },
        "schema": SCHEMA_HINT
    }
    
    user_payload = json.dumps(payload, ensure_ascii=False)
    
    # Call GPT-5
    try:
        raw = call_openai(DEFAULT_MODEL, SYSTEM_PROMPT, user_payload)
    except Exception:
        return
    
    # Parse response
    m = re.search(r'\{[\s\S]*\}', raw.strip())
    if not m:
        return
        
    try:
        result = json.loads(m.group(0))
    except Exception:
        return
    
    # Update memory with insights
    if result.get("memory_insights"):
        memory["ai_insights"] = memory.get("ai_insights", [])
        memory["ai_insights"].append({
            "timestamp": now_iso(),
            "insight": result["memory_insights"],
            "session": memory["sessions"]
        })
        memory["ai_insights"] = memory["ai_insights"][-20:]
    
    # Track feature recommendations
    if result.get("feature_recommendations"):
        for feature in result["feature_recommendations"].get("rollback", []):
            memory["feature_lifecycle"]["struggling_features"].append({
                "feature": feature,
                "ai_suggested_rollback": True,
                "session": memory["sessions"]
            })
    
    # Save enhanced memory
    memory["last_timestamp"] = msgs[-1]["timestamp"] if msgs else memory.get("last_timestamp")
    save_json(MEMORY_FILE, memory)
    
    # Write nudge if warranted
    if result.get("should_intervene") and result.get("score", 0) >= THRESHOLD:
        nudge_data = {
            "created_at": now_iso(),
            "ttl_seconds": NUDGE_TTL,
            "nudge_markdown": result.get("nudge_markdown", ""),
            "commands": result.get("commands", []),
            "score": result.get("score", 0),
            "reason": result.get("reason", ""),
            "context": f"Session {memory['sessions']}, Branch: {git_context['branch']}, {len(msgs)} new messages",
            "memory_based": True
        }
        save_json(PENDING_FILE, nudge_data)

if __name__ == "__main__":
    main()