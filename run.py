"""Start LocalPilot:  python run.py   (opens http://127.0.0.1:8484, or the next free port)"""
import json
import logging
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from agent.config import load_config
from agent.server import create_app


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def is_localpilot(port: int) -> bool:
    """True if the program already listening on this port is LocalPilot (not another app)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
            return "installed_models" in json.loads(r.read().decode("utf-8"))
    except Exception:
        return False


def pick_port(host: str, preferred: int, tries: int = 30) -> tuple[int, str]:
    """Returns (port, situation): 'free', 'running' (LocalPilot already open there) or 'moved' (port busy)."""
    for port in range(preferred, preferred + tries):
        if port_free(host, port):
            return port, ("free" if port == preferred else "moved")
        if is_localpilot(port):
            return port, "running"
    raise SystemExit(f"No free port between {preferred} and {preferred + tries - 1}. Set server.port in config.yaml.")


def open_when_ready(url: str, timeout: float = 60):
    """Open the browser only after OUR server answers, so we never open some other app by mistake."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/status", timeout=2) as r:
                if "installed_models" in r.read().decode("utf-8"):
                    webbrowser.open(url)
                    return
        except Exception:
            time.sleep(0.5)
    print(f"  LocalPilot did not become ready in {timeout:.0f}s; open {url} yourself once it is.")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    host, preferred = cfg["server"]["host"], int(cfg["server"]["port"])
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: LocalPilot can control your PC. Binding to a non-local address exposes it to your network.")

    port, situation = pick_port(host, preferred)
    url = f"http://127.0.0.1:{port}"
    if situation == "running":
        print(f"\n  LocalPilot is already running at {url} - opening it.\n")
        if cfg["server"]["open_browser"]:
            webbrowser.open(url)
        return
    if situation == "moved":
        print(f"\n  Port {preferred} is used by another program, so LocalPilot is using port {port} instead.")
        print(f"  (To pick a fixed port, set server.port in config.yaml.)")
    print(f"\n  LocalPilot is starting at {url}  (keep this window open; close it to stop)\n")
    if cfg["server"]["open_browser"]:
        threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    if sys.platform == "win32":
        import os
        os.system("")  # ANSI colors in old Windows consoles
    main()
