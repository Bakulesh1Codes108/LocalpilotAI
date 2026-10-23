"""Start LocalPilot:  python run.py   (then open http://127.0.0.1:7860)"""
import logging
import threading
import webbrowser

import uvicorn

from agent.config import load_config
from agent.server import create_app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    host, port = cfg["server"]["host"], cfg["server"]["port"]
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: LocalPilot can control your PC. Binding to a non-local address exposes it to your network.")
    url = f"http://127.0.0.1:{port}"
    print(f"\n  LocalPilot is starting at {url}\n")
    if cfg["server"]["open_browser"]:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
