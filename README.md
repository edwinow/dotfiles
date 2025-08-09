# Edwin's Dotfiles

Personal scripts and configurations for development environment.

## Structure

```
dotfiles/
├── bin/          # Executable scripts
├── hooks/        # Claude Code hooks
├── tools/        # Development tools
└── config/       # Configuration files
```

## Scripts

### bin/last_messages.py
Extract and format Claude Code conversation transcripts with XML tags.

**Usage:**
```bash
uv run last_messages.py <session-id> [n_messages]
uv run last_messages.py <session-id> [n_messages] --no-clip
```

**Features:**
- Extracts user/assistant messages with tool interactions
- Formats output with XML tags (`<User>`, `<Assistant>`, `<ToolUse>`, `<ToolResult>`)
- Saves to `~/copyq` with reverse-sorted filenames (newest first)
- Copies to clipboard automatically
- Truncates long tool results to 2000 characters

### hooks/sidekick-counter.py
PostToolUse hook that tracks tool usage and triggers periodic code reviews.

**Configuration:**
- `SIDEKICK_INTERVAL`: Tool calls between reviews (default: 10)
- `SIDEKICK_COOLDOWN_SECONDS`: Minimum seconds between reviews (default: 120)

### hooks/sidekick-review-worker.py
Background worker that analyzes transcripts and provides actionable feedback.

**Configuration:**
- `SIDEKICK_MODEL`: AI model to use (default: gpt-5)
- `SIDEKICK_THRESHOLD`: Score threshold for showing nudges (default: 0.2)
- `SIDEKICK_MAX_EVENTS`: Number of recent messages to analyze (default: 60)

### hooks/sidekick-nudge.py
PreToolUse hook that displays pending feedback nudges.

### hooks/enforce-pnpm.py
PreToolUse hook for Bash commands that enforces pnpm usage over npm/yarn.

## Installation

1. Clone this repository:
```bash
git clone <your-repo-url> ~/.claude/dotfiles
```

2. Create symlinks to your bin directory:
```bash
ln -s ~/.claude/dotfiles/bin/last_messages.py ~/bin/last_messages.py
```

3. Configure Claude Code hooks in `~/.claude/settings.json`:
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "$HOME/.claude/hooks/sidekick-nudge.py",
            "timeout": 2
          }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "$HOME/.claude/hooks/enforce-pnpm.py",
            "timeout": 5
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "$HOME/.claude/hooks/sidekick-counter.py",
            "timeout": 3
          }
        ]
      }
    ]
  }
}
```

## Requirements

- Python 3.8+
- `uv` for Python package management
- `pyperclip` for clipboard support (installed automatically by uv)

## License

Personal use only.