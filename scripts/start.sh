#!/usr/bin/env bash
cd "$(dirname "$0")/.."
[ -x .venv/bin/python ] || { echo "Run setup first: bash scripts/setup.sh"; exit 1; }
if command -v ollama >/dev/null && ! pgrep -x ollama >/dev/null; then
  (ollama serve >/dev/null 2>&1 &)
  sleep 2
fi
exec .venv/bin/python run.py
