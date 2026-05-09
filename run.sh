#!/bin/zsh
# Start Nihongo Vocab server
cd "$(dirname "$0")"
source $HOME/.local/bin/env 2>/dev/null || true
uv run --with flask --with genanki python3 app.py
