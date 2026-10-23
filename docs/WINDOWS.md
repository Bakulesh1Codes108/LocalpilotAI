# Install LocalPilot on Windows 10 / 11 (step by step)

You need: Windows 10 or 11, about **10 GB free disk**, **8 GB RAM minimum** (16 GB recommended), internet for
the one-time downloads. No coding knowledge needed.

## 1. Download

**Option A – no git:** on the GitHub page click the green **Code** button → **Download ZIP**. Right-click the
ZIP → **Extract All…** → choose a folder such as `C:\Users\<you>\LocalPilot`.

**Option B – with git:**
```powershell
git clone https://github.com/Bakulesh1Codes108/LocalpilotAI.git
cd LocalpilotAI
```

## 2. Install (one time, ~10–20 minutes)

Open the folder and **double-click `install.bat`**. It walks you through 7 steps and asks before each install:

| Step | What it does | Size |
|---|---|---|
| 1 | Finds Python 3.10+, or offers to install Python 3.12 with winget | ~30 MB |
| 2 | Installs LocalPilot's packages into a private `.venv` folder | ~60 MB |
| 3 | Offers to install **Ollama** (runs the AI model) | ~1 GB |
| 4 | Detects your RAM/GPU and downloads the right **Qwen3** model | 1.4–19 GB |
| 5 | *Optional:* **LAYA** safety layer | ~2–3 GB |
| 6 | *Optional:* headless browser for JavaScript sites | ~150 MB |
| 7 | Creates a Desktop shortcut | – |

> **Windows SmartScreen** may say "Windows protected your PC" for the `.bat` file. Click **More info → Run
> anyway**. You can open `install.bat` in Notepad first to see exactly what it runs.

> If step 1 installs Python, **close the window and double-click `install.bat` again** so Windows picks up the
> new PATH.

## 3. Start

Double-click **`LocalPilot.bat`** (or the Desktop shortcut). A black window opens (keep it open, since it
*is* LocalPilot) and your browser opens **http://127.0.0.1:7860**. Close the black window to stop LocalPilot.

## 4. First things to try

- *Check my PC specs and tell me which model fits best*
- *Install Git, Node.js LTS and VS Code*
- *Clone https://github.com/... into my workspace and tell me how to run it*
- *Move all PDFs from Downloads into Documents\PDFs*
- *Every weekday at 9am search top AI news and save a summary to news.md*

New files go to `C:\Users\<you>\LocalPilot-Workspace` unless you name another folder.

## Troubleshooting

| Problem | Fix |
|---|---|
| "model offline — start Ollama" | Start **Ollama** from the Start menu, or run `ollama serve` in a terminal |
| "Model 'qwen3:8b' is not installed" | System tab → **Download**, or run `ollama pull qwen3:8b` |
| Very slow answers | Pick a smaller model in the System tab (e.g. `qwen3:4b`) |
| `python` not found after installing | Close all windows and run `install.bat` again; or reinstall Python with "Add to PATH" ticked |
| Port 7860 already in use | Change `server.port` in `config.yaml` |
| LAYA shows "failed" | It needs internet the first time to download weights; LocalPilot works fine without it |
| Antivirus warns about PowerShell | LocalPilot runs commands only after you click Allow; check the Activity tab to see everything it did |

## Update

```powershell
git pull                      # or download the ZIP again and copy your config.yaml over
.venv\Scripts\python -m pip install -r requirements.txt
```

## Uninstall

Delete the LocalPilot folder and the Desktop shortcut. Optionally uninstall Ollama from *Settings → Apps*
and delete `%USERPROFILE%\.ollama` (downloaded models) and `%USERPROFILE%\LocalPilot-Workspace`.
