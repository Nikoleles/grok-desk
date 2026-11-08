#!/usr/bin/env python3
"""Status line for the one desk process. Restarts it when the process or an agent is dead.

Prints one line and exits. Wire it to a minute timer if you want that; this file
does not install one.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from desk.util import parse_iso

AGENTS = ("scanner", "auditor", "analyst", "executor", "risk")
ROOT = Path(__file__).resolve().parent
PID_FILE = ROOT / "run" / "desk.pid"


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def assess(pid: int | None, heartbeats: dict, now, stale_s: float = 180, booting_s: float = 90) -> tuple[str, str]:
    """Return (action, line). action is ok | start | restart."""
    parts = []
    stale = []
    for agent in AGENTS:
        ts = heartbeats.get(agent)
        if not ts:
            parts.append(f"{agent}=down")
            stale.append(agent)
            continue
        try:
            age = (now - parse_iso(ts)).total_seconds()
        except Exception:
            age = stale_s + 1
        if age > stale_s:
            parts.append(f"{agent}=down {int(age)}s")
            stale.append(agent)
        else:
            parts.append(f"{agent}=ok {int(age)}s")
    body = " ".join(parts)
    if not pid or not pid_alive(pid):
        return "start", f"down process {body}"
    # A fresh process may not have written heartbeats yet.
    try:
        started = float((PID_FILE.read_text().splitlines()[1:2] or ["0"])[0]) if False else None
    except Exception:
        started = None
    if stale and len(stale) == len(AGENTS):
        return "restart", f"stale {body}"
    if stale:
        return "restart", f"stale {body}"
    return "ok", f"ok pid={pid} {body}"


def read_pid() -> tuple[int | None, float | None]:
    if not PID_FILE.exists():
        return None, None
    lines = PID_FILE.read_text().splitlines()
    try:
        pid = int(lines[0])
    except (ValueError, IndexError):
        return None, None
    started = None
    if len(lines) > 1:
        try:
            started = float(lines[1])
        except ValueError:
            started = None
    return pid, started


def spawn():
    env = os.environ.copy()
    log = open(ROOT / "logs" / "health-restart.log", "a", encoding="utf-8")
    subprocess.Popen(
        [sys.executable, "-m", "desk.main"],
        cwd=str(ROOT),
        env=env,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )


def main():
    from datetime import datetime, timezone

    from desk.config import Config
    from desk.db import DB

    now = datetime.now(timezone.utc)
    cfg = Config()
    pid, started = read_pid()
    beats = {}
    if Path(cfg.db_path).exists():
        beats = DB(cfg.db_path).heartbeats()
    # Don't restart a process that is still in its first minute.
    if pid and pid_alive(pid) and started and (now.timestamp() - started) < 60:
        print(f"ok pid={pid} starting")
        return
    action, line = assess(pid if pid and pid_alive(pid) else None, beats, now)
    if action == "ok":
        print(line)
        return
    if action == "restart" and pid and pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    spawn()
    print(f"restarted reason={line}")


if __name__ == "__main__":
    main()
