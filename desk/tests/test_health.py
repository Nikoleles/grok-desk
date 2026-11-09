"""health.py must not restart a live desk that is only catching up after the box resumed."""

from datetime import datetime, timedelta, timezone

import health
from desk.db import DB
from desk.util import iso

AGENTS = health.AGENTS


def _beats(db, now, ages):
    for agent in AGENTS:
        db.heartbeat(agent, iso(now - timedelta(seconds=ages.get(agent, 0))))


def test_resume_catch_up_is_not_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "pid_alive", lambda pid: True)
    path = str(tmp_path / "d.db")
    db = DB(path).init("")
    now = datetime(2026, 11, 9, 3, 18, 32, tzinfo=timezone.utc)
    # Just after a box resume: Scanner and Risk look ~9 min old, the 1s agents are fresh.
    _beats(db, now, {"scanner": 557, "risk": 506})
    action, line = health.assess(4242, db.heartbeats(), now)
    assert action == "restart"

    ticks = []

    def sleep(seconds):
        ticks.append(seconds)
        if len(ticks) == 3:  # Scanner and Risk tick again 15s later
            _beats(db, now + timedelta(seconds=15), {})

    clock = lambda: now + timedelta(seconds=5 * len(ticks))
    action, line = health.recheck(4242, path, action, line, grace_s=150, poll_s=5, sleep=sleep, clock=clock)
    assert action == "ok"
    assert len(ticks) == 3


def test_really_stuck_agent_still_restarts(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "pid_alive", lambda pid: True)
    path = str(tmp_path / "d.db")
    db = DB(path).init("")
    now = datetime(2026, 11, 9, 3, 18, 32, tzinfo=timezone.utc)
    _beats(db, now, {"scanner": 900})
    action, line = health.assess(4242, db.heartbeats(), now)
    ticks = []
    clock = lambda: now + timedelta(seconds=5 * len(ticks))
    action, line = health.recheck(4242, path, action, line, grace_s=30, poll_s=5,
                                  sleep=ticks.append, clock=clock)
    assert action == "restart"
    assert "scanner=down" in line
    assert len(ticks) == 6
