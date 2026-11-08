"""Stops, trails, timeouts, and a stop that never moves down."""

import asyncio

from desk.agents.executor import Executor
from desk.agents.risk import (
    Risk,
    apply_stop_pct,
    poll_position,
    position_stop_pct,
)
from desk.api import chart_payload, state_payload
from desk.sources import FakeSources
from desk.telegram import parse_command
from desk.tests.fakes import fresh_db, install
from desk.util import iso, parse_iso

MINT = "RiskMint111111111111111111111111111111111"
OTHER = "OldMint1111111111111111111111111111111111"


def _open(db, mint, entry, when, stop_pct):
    db.upsert_coin(mint=mint, name=mint[:4], symbol=mint[:4], status="passed", night=iso(parse_iso(when))[:10])
    db.add_position(
        mint=mint, opened_at=when, entry_price=entry, size_usd=80,
        peak_price=entry, stop_price=entry * (1 - stop_pct / 100),
        trail_active=0, mode="PAPER", night=when[:10],
    )


def _risk(db, clock, sources):
    notifier = __import__("desk.telegram", fromlist=["RecordingNotifier"]).RecordingNotifier()
    return Risk(db, None, sources, clock, notifier), notifier


def test_trail_stop_and_timeout(tmp_path):
    db, clock = fresh_db(tmp_path / "desk.db")
    sources = FakeSources()
    install(sources, MINT, price=1.25, liq=80000)
    _open(db, MINT, 1.0, iso(clock.now()), 6)
    risk, notifier = _risk(db, clock, sources)

    asyncio.run(risk.poll_once())
    pos = db.open_position_for(MINT)
    assert pos["trail_active"] == 1
    assert pos["exit_reason"] is None
    assert abs(pos["peak_price"] - 1.25) < 1e-9

    sources.pairs[MINT] = install_price = None
    install(sources, MINT, price=1.25 * 0.87, liq=80000)
    asyncio.run(risk.poll_once())
    closed = db.position_for(MINT)
    assert closed["exit_reason"] == "trail"
    assert closed["closed_at"]
    assert any("Sold" in p.text for p in notifier.pushes)

    # Fresh position for the stop and the timeout, one at a time.
    db2, clock2 = fresh_db(tmp_path / "stop.db")
    sources2 = FakeSources()
    install(sources2, MINT, price=0.939, liq=80000)
    _open(db2, MINT, 1.0, iso(clock2.now()), 6)
    risk2, note2 = _risk(db2, clock2, sources2)
    asyncio.run(risk2.poll_once())
    assert db2.position_for(MINT)["exit_reason"] == "stop"

    db3, clock3 = fresh_db(tmp_path / "time.db")
    sources3 = FakeSources()
    install(sources3, MINT, price=1.04, liq=80000)
    opened = iso(clock3.now())
    from datetime import timedelta
    earlier = iso(clock3.now() - timedelta(hours=3))
    _open(db3, MINT, 1.0, earlier, 6)
    risk3, _note3 = _risk(db3, clock3, sources3)
    asyncio.run(risk3.poll_once())
    assert db3.position_for(MINT)["exit_reason"] == "timeout"
    # The direct rule matches the agent.
    result = poll_position(1, 1, 0.94, False, parse_iso(earlier), 1.04, clock3.now(), db3.settings())
    assert result.action == "timeout"
    assert opened  # silence unused in a way that still documents the clock


def test_looser_stop_does_not_move_an_open_position(tmp_path):
    db, clock = fresh_db(tmp_path / "desk.db")
    now = iso(clock.now())
    _open(db, OTHER, 1.0, now, 6)
    assert parse_command("set stop to -8%") == ("set", "risk.stop_pct", 8.0)
    old, new = apply_stop_pct(db, 8, now, who="telegram")
    assert old == 6 and new == 8
    kept = db.open_position_for(OTHER)
    assert abs(position_stop_pct(kept["entry_price"], kept["stop_price"]) - 6) < 0.05

    sources = FakeSources()
    install(sources, MINT, price=0.919, symbol="NEW", name="New")
    install(sources, OTHER, price=1.0, symbol="OLD", name="Old")
    executor = Executor(db, None, sources, clock, None)
    executor.open_position(MINT, 1.0, 50, "PAPER", now[:10], now)
    fresh = db.open_position_for(MINT)
    assert abs(position_stop_pct(fresh["entry_price"], fresh["stop_price"]) - 8) < 0.05

    risk, notifier = _risk(db, clock, sources)
    asyncio.run(risk.poll_once())
    assert db.open_position_for(OTHER)["stop_price"] == kept["stop_price"]
    assert abs(position_stop_pct(1.0, db.open_position_for(OTHER)["stop_price"]) - 6) < 0.05
    closed = db.position_for(MINT)
    assert closed["exit_reason"] == "stop"
    blob = " ".join(p.text for p in notifier.pushes)
    assert "-8%" in blob
    activity = db.activity()
    stopped = next(r for r in activity if r["kind"] == "stopped" and r["mint"] == MINT)
    assert "-8%" in stopped["subtitle"]
    chart = chart_payload(db, clock)
    labels = [m["label"] for key in chart["ranges"] for m in chart["ranges"][key]["marks"]]
    assert any("-8%" in label for label in labels)
    pill = next(a for a in state_payload(db, clock)["agents"] if a["id"] == "risk")
    assert pill["pills"][0]["text"] == "stop -8%"
