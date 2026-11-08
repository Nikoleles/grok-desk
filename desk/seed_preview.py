"""Preview night for the iPhone UI. Not used by the live agents.

Numbers are run through fees.py so the balance matches a real PAPER fill.
"""

from __future__ import annotations

import math

from desk.fees import one_side, pnl
from desk.links import coin_url
from desk.util import iso

JASON = "DDDfDJGreM6pYbKEiB8NqkZ3RhgG9Q1zKK58SvcHpump"
MIRA = "MiraMint111111111111111111111111111111pump"
POGO = "PogoMint111111111111111111111111111111pump"
NOPE = "NopeMint111111111111111111111111111111pump"
CHASE = "ChaseMint11111111111111111111111111111pump"
DUST = "DustMint111111111111111111111111111111pump"
WHALE = "WhaleMint11111111111111111111111111111pump"
MOON = "MoonMint111111111111111111111111111111pump"


def _at(now, minutes: float) -> str:
    from datetime import timedelta
    return iso(now + timedelta(minutes=minutes))


def seed(db, clock) -> None:
    if db.get_coin(JASON):
        return
    now = clock.now()
    night = iso(now)[:10]
    db.set_setting("desk.night_started", iso(now), who="preview", ts=iso(now))
    db.set_setting("desk.mode", "PAPER", who="preview", ts=iso(now))
    db.set_setting("desk.fomo_handle", "desk", who="preview", ts=iso(now))
    db.set_setting("desk.stack", 1000, who="preview", ts=iso(now))
    db.set_setting("desk.unread", 2, who="preview", ts=iso(now))
    db.set_setting("analyst.mode", "rules", who="preview", ts=iso(now))
    db.set_setting("desk.owner_chat_id", "1", who="preview", ts=iso(now))
    discount = 0.10
    cash = 1000.0

    def buy(size):
        nonlocal cash
        cash -= size + one_side(size, discount)

    def sell(size, entry, exit_price):
        nonlocal cash
        cash += size * (exit_price / entry) - one_side(size, discount)
        return pnl(size, entry, exit_price, discount)

    # POGO, stopped at -6.1%.
    pogo_entry, pogo_exit, pogo_size = 0.01, 0.01 * 0.939, 50.0
    buy(pogo_size)
    pogo_net, pogo_fees = sell(pogo_size, pogo_entry, pogo_exit)
    # MIRA, trailed out at +80%.
    mira_entry, mira_exit, mira_size = 0.002, 0.002 * 1.80, 80.0
    buy(mira_size)
    mira_net, mira_fees = sell(mira_size, mira_entry, mira_exit)
    # JASON, still open, +14% with a +25% peak so the trail line sits under price.
    jason_entry, jason_size = 0.00018, 80.0
    jason_now = jason_entry * 1.14
    jason_peak = jason_entry * 1.25
    buy(jason_size)
    db.set_setting("desk.cash", f"{cash:.4f}", who="preview", ts=iso(now))
    db.set_setting("desk.night_start", 1000, who="preview", ts=iso(now))
    db.set_setting("desk.stack", 1000, who="preview", ts=iso(now))

    def coin(mint, name, symbol, status, reason, migrated_min, holders=None, mcap=None, liq=None, vol=None, pool="Pool111111111111111111111111111111111111111"):
        db.upsert_coin(
            mint=mint, name=name, symbol=symbol, pool=pool, migrated_at=_at(now, -migrated_min),
            creator="Creator111111111111111111111111111111111111",
            creator_balance_at_migration="500000",
            holders=holders, status=status, kill_reason=reason, night=night,
            liq_at_scan=liq, price_at_scan=None, market_cap=mcap, volume_h24=vol,
            liquidity_usd=liq,
        )

    coin(JASON, "Jason Duval", "JASON", "passed", None, 11 * 60, 1908, 180787, 82000, 193237)
    coin(MIRA, "Mira", "MIRA", "passed", None, 5 * 60, 840, 240000, 96000, 410000)
    coin(POGO, "Pogo", "POGO", "passed", None, 4 * 60, 410, 90000, 61000, 120000)
    coin(NOPE, "Nope", "NOPE", "rejected", "dev holds 31%", 3 * 60, 120, 40000, 52000, 30000)
    coin(CHASE, "Chase", "CHASE", "passed", None, 2 * 60, 300, 150000, 70000, 88000)
    coin(DUST, "Dust", "DUST", "killed", "liq $12.4K", 90, liq=12400, mcap=20000, vol=8000)
    coin(WHALE, "Whale", "WHALE", "killed", "top 10 hold 41%", 80, liq=88000, mcap=300000, vol=50000)
    coin(MOON, "Moon", "MOON", "killed", "h1 +96%", 70, liq=76000, mcap=500000, vol=220000)

    db.add_position(
        mint=POGO, opened_at=_at(now, -200), entry_price=pogo_entry, size_usd=pogo_size,
        peak_price=pogo_entry, stop_price=pogo_entry * 0.94, trail_active=0,
        closed_at=_at(now, -170), exit_price=pogo_exit, exit_reason="stop",
        pnl_usd=pogo_net, fees_usd=pogo_fees, mode="PAPER", night=night,
    )
    db.add_position(
        mint=MIRA, opened_at=_at(now, -150), entry_price=mira_entry, size_usd=mira_size,
        peak_price=mira_exit, stop_price=mira_entry * 0.94, trail_active=1,
        closed_at=_at(now, -90), exit_price=mira_exit, exit_reason="trail",
        pnl_usd=mira_net, fees_usd=mira_fees, mode="PAPER", night=night,
    )
    db.add_position(
        mint=JASON, opened_at=_at(now, -40), entry_price=jason_entry, size_usd=jason_size,
        peak_price=jason_peak, stop_price=jason_entry * 0.94, trail_active=1,
        mode="PAPER", night=night,
    )

    # Candles: a rise to the peak, then a settle at +14%.
    for i in range(48):
        phase = i / 47
        if phase < 0.7:
            level = 1 + 0.25 * (phase / 0.7)
        else:
            level = 1.25 - 0.11 * ((phase - 0.7) / 0.3)
        wick = 0.01 * math.sin(i * 1.3)
        base = jason_entry * level
        for k, mult in enumerate((1 + wick, 1 + abs(wick) * 0.4, 1 - abs(wick) * 0.5, 1.0)):
            price = base * mult
            vol = 1000 + i * 40 + k * 15
            db.add_price(JASON, _at(now, -47 + i)[:-1] + f"{k}", price, vol)
    db.add_price(JASON, iso(now), jason_now, 3200)
    for i in range(30):
        px = mira_entry * (1 + 0.8 * (i / 29))
        db.add_price(MIRA, _at(now, -120 + i * 1), px, 500 + i * 20)

    layers_ok = [
        (1, "Mint authority", "Revoked", "rpc"),
        (2, "Freeze authority", "Revoked", "rpc"),
        (3, "Dev wallet", "0.9% · sold 0", "rpc"),
        (4, "Top 10", "16.8%", "rpc"),
        (5, "Liquidity", "100% locked", "rugcheck"),
        (6, "Pool age", "43m", "scanner"),
        (7, "Bundled wallets", "None", "rugcheck"),
        (8, "Delegations", "None", "rpc"),
    ]
    for n, name, value, source in layers_ok:
        db.add_audit(JASON, _at(now, -50), n, name, value, True, source, night)
        db.add_audit(MIRA, _at(now, -160), n, name, value, True, source, night)
    db.add_audit(NOPE, _at(now, -100), 1, "Mint authority", "Revoked", True, "rpc", night)
    db.add_audit(NOPE, _at(now, -100), 2, "Freeze authority", "Revoked", True, "rpc", night)
    db.add_audit(NOPE, _at(now, -100), 3, "Dev wallet", "dev holds 31%", False, "rpc", night)
    for n, name in ((4, "Top 10"), (5, "Liquidity"), (6, "Pool age"), (7, "Bundled wallets"), (8, "Delegations")):
        db.add_audit(NOPE, _at(now, -100), n, name, "not checked", None, "", night)

    db.add_verdict(
        mint=CHASE, ts=_at(now, -80), alive_6h=0.8, organic=0.7, flush=0.4, chase=0.31,
        score=65, **{"pass": False}, reason="chase 0.31", input_tokens=0, model=None, cost=0,
        mode="rules", night=night,
    )
    db.add_verdict(
        mint=JASON, ts=_at(now, -48), alive_6h=0.82, organic=0.74, flush=0.61, chase=0.18,
        score=75, **{"pass": True}, reason="", input_tokens=0, model=None, cost=0,
        mode="rules", night=night,
    )
    db.add_signal(
        mint=POGO, ts=_at(now, -200), price=pogo_entry, size_usd=pogo_size, stack_pct=5,
        link=coin_url(POGO), mode="PAPER", status="bought", score=74, night=night,
    )
    db.add_signal(
        mint=MIRA, ts=_at(now, -150), price=mira_entry, size_usd=mira_size, stack_pct=8,
        link=coin_url(MIRA), mode="PAPER", status="bought", score=81, night=night,
    )
    db.add_signal(
        mint=JASON, ts=_at(now, -40), price=jason_entry, size_usd=jason_size, stack_pct=8,
        link=coin_url(JASON), mode="PAPER", status="bought", score=75, night=night,
    )

    def act(ts, kind, mint, agent, title, subtitle, value):
        db.add_activity(ts, kind, mint, agent, title, subtitle, value, night)

    act(_at(now, -200), "bought", POGO, "Executor", "Bought POGO", "Executor · " + _at(now, -200)[11:16], "$50.00 · 5% of stack")
    act(_at(now, -170), "stopped", POGO, "Risk", "Stopped POGO", "Risk · -6% stop", f"-${abs(pogo_net):.2f}")
    act(_at(now, -150), "bought", MIRA, "Executor", "Bought MIRA", "Executor · " + _at(now, -150)[11:16], "$80.00 · 8% of stack")
    act(_at(now, -90), "sold", MIRA, "Risk", "Sold MIRA", "Risk · trailing stop", f"+${mira_net:.2f} · +80%")
    act(_at(now, -100), "rejected", NOPE, "Auditor", "Rejected NOPE", "Auditor · dev holds 31%", "Skipped")
    act(_at(now, -80), "rejected", CHASE, "Analyst (rules)", "Rejected CHASE", "Analyst (rules) · chase 0.31", "Skipped")
    act(_at(now, -40), "bought", JASON, "Executor", "Bought JASON", "Executor · " + _at(now, -40)[11:16], "$80.00 · 8% of stack")

    def decide(agent, ts, mint, coin, verdict, reason):
        db.add_decision(agent, ts, mint, coin, verdict, reason, night)

    decide("scanner", _at(now, -90), DUST, "DUST", "killed", "liq $12.4K")
    decide("scanner", _at(now, -80), WHALE, "WHALE", "killed", "top 10 hold 41%")
    decide("scanner", _at(now, -70), MOON, "MOON", "killed", "h1 +96%")
    decide("scanner", _at(now, -50), JASON, "JASON", "passed", "→ auditor")
    decide("scanner", _at(now, -160), MIRA, "MIRA", "passed", "→ auditor")
    decide("auditor", _at(now, -50), JASON, "JASON", "passed", "8/8 → Executor")
    decide("auditor", _at(now, -100), NOPE, "NOPE", "rejected", "dev holds 31%")
    decide("analyst", _at(now, -48), JASON, "JASON", "passed", "score 75")
    decide("analyst", _at(now, -80), CHASE, "CHASE", "rejected", "chase 0.31")
    decide("executor", _at(now, -40), JASON, "JASON", "bought", "$80.00")
    decide("executor", _at(now, -150), MIRA, "MIRA", "bought", "$80.00")
    decide("executor", _at(now, -200), POGO, "POGO", "bought", "$50.00")
    decide("risk", _at(now, -90), MIRA, "MIRA", "trail", "Risk · trailing stop")
    decide("risk", _at(now, -170), POGO, "POGO", "stop", "Risk · -6% stop")

    from desk.portfolio import equity as equity_fn
    end = equity_fn(db)
    # A 6 hour curve that finishes on the live equity.
    steps = 40
    for i in range(steps + 1):
        phase = i / steps
        if phase < 0.35:
            level = 1000 - 18 * math.sin(phase / 0.35 * math.pi)
        elif phase < 0.7:
            level = 982 + (end - 982) * ((phase - 0.35) / 0.35) * 0.85
        else:
            level = (982 + (end - 982) * 0.85) + (end - (982 + (end - 982) * 0.85)) * ((phase - 0.7) / 0.3)
        wobble = 1.4 * math.sin(phase * 18)
        value = level + wobble
        if i == steps:
            value = end
        db.add_balance(_at(now, -360 + (360 * phase)), value)
    for name in ("scanner", "auditor", "analyst", "executor", "risk"):
        db.heartbeat(name, iso(now))
