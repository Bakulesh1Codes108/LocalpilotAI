#!/usr/bin/env bash
# LocalPilot setup for macOS / Linux.  Run from the project folder:  bash scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

ask() { read -r -p "$1 [y/N] " a; [[ "$a" =~ ^([yY]|yes)$ ]]; }
echo -e "\n== LocalPilot setup =="

command -v python3 >/dev/null || { echo "Python 3.10+ is required (brew install python / sudo apt install python3 python3-venv)."; exit 1; }

[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -r requirements.txt -q
echo "Python packages installed."

if ! command -v ollama >/dev/null; then
  echo "Ollama runs the AI model locally and is not installed."
  if [[ "$(uname)" == "Darwin" ]]; then
    ask "Install Ollama with Homebrew now?" && brew install ollama
  else
    echo "Install it with the official script (review it first at https://ollama.com/install.sh):"
    echo "  curl -fsSL https://ollama.com/install.sh | sh"
    ask "Run that now?" && curl -fsSL https://ollama.com/install.sh | sh
  fi
fi

.venv/bin/python -m agent.hardware
MODEL="$(.venv/bin/python -m agent.hardware --model)"
if command -v ollama >/dev/null && ask "Download $MODEL now? (several GB)"; then
  pgrep -x ollama >/dev/null || (ollama serve >/dev/null 2>&1 &) ; sleep 2
  ollama pull "$MODEL"
fi

echo "LAYA safety layer (optional): double-checks commands and scans web pages for prompt-injection (~2-3 GB)."
if ask "Install LAYA?"; then
  .venv/bin/python -m pip install -r requirements-laya.txt
  .venv/bin/python -c "from agent.config import load_config; from agent.laya_guard import LayaGuard; g=LayaGuard(load_config()); g._load(); print('LAYA:', g.status())"
fi

if ask "Install the optional headless browser (Playwright + Chromium, ~150 MB)?"; then
  .venv/bin/python -m pip install playwright -q
  .venv/bin/python -m playwright install chromium
fi

echo -e "\nDone! Start LocalPilot with:  bash scripts/start.sh"
