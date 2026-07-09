#!/bin/bash
set -e

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Use the command name directly for cross-platform compatibility
HOOK_CMD="claude-office-hook"

echo "Uninstalling hooks..."

# Use the python script to remove settings
uv run -p 3.14 "$HOOKS_DIR/manage_hooks.py" uninstall --hook-cmd "$HOOK_CMD"

# Remove the installed tool binary. uv tool uninstall takes the package name
# (claude-office-hooks, plural) — the console-script command is the singular
# claude-office-hook, which is not a valid uv tool identifier.
echo "Removing claude-office-hooks tool..."
uv tool uninstall claude-office-hooks 2>/dev/null || true

# Clean up config and log files
rm -f "$HOME/.claude/claude-office-config.env"
rm -f "$HOME/.claude/claude-office-hooks.log"

echo "Done! Hooks removed from configuration."
