#!/usr/bin/env python3
"""
Global PreToolUse hook to enforce PNPM usage in pnpm projects.
Only enforces when pnpm-lock.yaml exists or package.json specifies pnpm.
"""

import json
import sys
import re
import os
from pathlib import Path

def is_pnpm_repo(cwd: str) -> bool:
    """Check if the current project uses pnpm"""
    p = Path(cwd)
    
    # Check for pnpm-lock.yaml
    if (p / "pnpm-lock.yaml").exists():
        return True
    
    # Check package.json for packageManager field
    pkg = p / "package.json"
    if pkg.exists():
        try:
            meta = json.loads(pkg.read_text())
            pm = meta.get("packageManager", "")
            if isinstance(pm, str) and pm.startswith("pnpm@"):
                return True
        except:
            pass
    
    return False

def main():
    try:
        # Read input from stdin
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)  # Silent fail for global hook

    # Get tool information
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})
    cwd = input_data.get("cwd", os.getcwd())
    
    # Only check Bash commands in pnpm repos
    if tool_name != "Bash" or not is_pnpm_repo(cwd):
        sys.exit(0)
    
    # Get the command being executed
    command = tool_input.get("command", "")
    
    # Pattern to match npm or bun commands
    npm_pattern = r'^\s*(npm|npx)\s+'
    bun_pattern = r'^\s*bun\s+(?!test(\s|$))'  # Allow "bun test" but block other bun commands
    
    if re.match(npm_pattern, command) or re.match(bun_pattern, command):
        # Exit code 2 blocks the tool call and shows stderr to Claude
        error_message = (
            "⚠️ This project uses PNPM as its package manager.\n"
            "Please use 'pnpm' instead of 'npm' or 'bun'.\n"
            "\n"
            "Examples:\n"
            "  • pnpm install (instead of npm install)\n"
            "  • pnpm add <package> (instead of npm install <package>)\n"
            "  • pnpm run <script> (instead of npm run <script>)\n"
            "  • pnpm exec <command> (instead of npx <command>)\n"
            "\n"
            "Note: 'bun test' is allowed for running tests."
        )
        print(error_message, file=sys.stderr)
        sys.exit(2)
    
    # Allow the command to proceed
    sys.exit(0)

if __name__ == "__main__":
    main()