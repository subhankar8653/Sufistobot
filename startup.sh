#!/bin/bash

# Upstream sync script for Railway
# Set UPSTREAM_REPO variable in Railway to enable auto-sync
# Example: UPSTREAM_REPO=https://github.com/originaldev/Sufistobot

if [ -n "$UPSTREAM_REPO" ]; then
    echo "=== Upstream Sync Starting ==="
    
    # Configure git
    git config --global user.email "railway@bot.com"
    git config --global user.name "Railway Bot"
    
    # Add upstream remote if not exists
    git remote remove upstream 2>/dev/null
    git remote add upstream "$UPSTREAM_REPO"
    
    echo "Fetching from upstream: $UPSTREAM_REPO"
    git fetch upstream
    
    # Merge upstream changes (keep our local changes on top)
    git merge upstream/main --no-edit --strategy-option=ours 2>/dev/null || \
    git merge upstream/master --no-edit --strategy-option=ours 2>/dev/null || \
    echo "Upstream merge skipped (branch not found)"
    
    echo "=== Upstream Sync Done ==="
else
    echo "UPSTREAM_REPO not set, skipping upstream sync."
fi

# Start the bot
python3 run.py
