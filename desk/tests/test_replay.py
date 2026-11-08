"""Replay a 2-hour migration window through PAPER without touching the network.

    python -m desk.tests.test_replay --hours 2
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta

from desk.agents.analyst import Analyst
from desk.agents.auditor import Auditor
from desk.agents.executor import Executor
from desk.agents.risk import Risk
from desk.agents.scanner import Scanner, evaluate
from desk.bus import Bus
from desk.config import merged
from desk.sources import FakeSources
from desk.telegram import RecordingNotifier
from desk.tests.fakes import fresh_db, install, rpc_factory
from desk.util import FakeClock, iso


def test_scanner_kill_order_and_the_waiting_room():
    settings = merged({})
    thin = {"liquidity_usd": 31200, "change_h1": 10, "change_h24": 10}
    assert evaluate(thin, 10, 30, settings) == ("kill", "liq $31.2K")
    deep = {"liquidity_usd": 80000, "change_h1": 10, "change_h24": 10}
    assert evaluate(deep, 38, 30, settings)[0] == "kill"
    assert evaluate(deep, 38, 30, settings)[1].startswith("top 10 hold")
    hot = {"liquidity_usd": 80000, "change_h1": 92, "change_h24": 10}
    assert evaluate(hot, 10, 30, settings)[1].startswith("h1 +")
    runner = {"liquidity_usd": 80000, "change_h1": 10, "change_h24": 428}
    assert evaluate(runner, 10, 30, settings)[1].startswith("runner +")
    assert evaluate(deep, 10, 5, settings) == ("wait", "too young")
    assert evaluate(deep, 10, 15, settings) == ("pass", "→ auditor")
    assert evaluate(deep, 10, 90, settings)[0] == "kill"
    assert evaluate(None, None, 5, settings)[0] == "wait"
    assert evaluate(None, None, 90, settings) == ("kill", "no pool")


async def run_replay(db_path, hours: float = 2):
    """Feed four scripted graduations across `hours` of fake time."""
    db, _clock = fresh_db(db_path)
    start = _clock.now()
    clock = FakeClock(start)
    db.set_setting("desk.night_started", iso(start), who="test", ts=iso(start))
    sources = FakeSources()
    sources.rpc_fn = rpc_factory()
    bus = Bus()
    notifier = RecordingNotifier()
    scanner = Scanner(db, bus, sources, clock)
    auditor = Auditor(db, bus, sources, clock, notifier)
    analyst = Analyst(db, bus, sources, clock, notifier)
    executor = Executor(db, bus, sources, clock, notifier)
    Risk(db, bus, sources, clock, notifier)

    span = timedelta(hours=hours)
    plan = [
        ("low", "DUST", 0.00, dict(liq=12400, symbol="DUST", name="Dust", c1=5, c24=5)),
        ("freeze", "FRZ", 0.15, dict(liq=90000, symbol="FRZ", name="Frozen", c1=8, c24=12)),
        ("chase", "CHASE", 0.40, dict(
            liq=90000, symbol="CHASE", name="Chase", c1=12, c24=20,
            c5=31, b5=40, s5=40, b1=500, s1=500, v5=100, v1=100,
        )),
        ("pass", "PASS", 0.70, dict(
            liq=90000, symbol="PASS", name="Pass", c1=8, c24=15,
            c5=0, b5=50, s5=50, b1=500, s1=500, v5=20000, v1=20000,
        )),
    ]
    # The freeze coin shares the rpc factory; swap it in only while that coin is audited.
    base_rpc = sources.rpc_fn

    async def drain():
        while not bus.auditor.empty():
            await auditor.audit(bus.auditor.get_nowait())
        while not bus.analyst.empty():
            await analyst.judge(bus.analyst.get_nowait())
        while not bus.executor.empty():
            await executor.handle(bus.executor.get_nowait())

    for kind, mint, frac, opts in plan:
        clock.t = start + span * frac
        install(sources, mint, **opts)
        if kind == "freeze":
            sources.rpc_fn = rpc_factory(freeze="FreezeAuth111111111111111111111111111111")
        else:
            sources.rpc_fn = base_rpc
        raw = {"signature": "sig" + mint, "mint": mint, "txType": "migrate", "pool": "pump-amm"}
        await scanner.ingest(raw)
        await drain()
        # Let the waiting room age past the 10 minute pool gate, still inside the window.
        clock.advance(16 * 60)
        await scanner.poll_once()
        await drain()

    rows = list(reversed(db.activity(limit=50)))
    return rows, notifier


def test_replay_two_hour_paper_window(tmp_path, capsys):
    rows, notifier = asyncio.run(run_replay(tmp_path / "replay.db", hours=2))
    titles = " | ".join(f"{r['title']} / {r['subtitle']} / {r['value']}" for r in rows)
    print(titles)
    assert any(r["kind"] == "bought" for r in rows)
    assert any("Auditor" in (r["subtitle"] or "") for r in rows)
    assert any("Analyst (rules)" in (r["subtitle"] or "") for r in rows)
    assert notifier.pushes
    assert all("r=savipww" in p.url for p in notifier.pushes)
    captured = capsys.readouterr().out
    assert "Bought" in captured


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay a PAPER window and print Activity")
    parser.add_argument("--hours", type=float, default=2)
    args = parser.parse_args(argv)
    import tempfile
    from pathlib import Path
    path = Path(tempfile.mkdtemp()) / "replay.db"
    rows, _notifier = asyncio.run(run_replay(path, hours=args.hours))
    print(f"{args.hours:g}h PAPER replay")
    for row in rows:
        print(f"{row['title']} / {row['subtitle']} / {row['value']}")


if __name__ == "__main__":
    main()
