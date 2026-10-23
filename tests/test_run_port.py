"""Port selection: never open another app (e.g. WanGP on a busy port) by mistake."""
import socket

import run


def test_free_port_is_used():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert run.pick_port("127.0.0.1", free) == (free, "free")


def test_busy_port_by_other_app_moves_on():
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        got, situation = run.pick_port("127.0.0.1", port)
        assert situation == "moved" and got != port and got > port


def test_already_running_localpilot_is_reused(monkeypatch):
    monkeypatch.setattr(run, "port_free", lambda h, p: False)
    monkeypatch.setattr(run, "is_localpilot", lambda p: p == 9001)
    assert run.pick_port("127.0.0.1", 9000) == (9001, "running")
