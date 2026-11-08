"""Risk. Every open position, every poll, no waiting for a bounce.

A stop only moves up. A looser setting applies to new positions and leaves
the open ones where they were.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from desk import fields, links
from desk.fees import one_side, pnl
from desk.log import get_logger
from desk.sources import RateLimit
from desk.util import iso, money, parse_iso, pct_signed, stop_label

log = get_logger("risk")


@dataclass
class Poll:
    peak: float
    trail_active: bool
    action: str | None
    stop_line: float
    trail_line: float


def poll_position(entry, peak, stop_price, trail_active, opened_at: datetime, price, now: datetime, settings) -> Poll:
    peak = max(float(peak), float(price))
    arm = float(entry) * (1.0 + float(settings["risk.trail_arm_pct"]) / 100.0)
    active = bool(trail_active) or float(price) >= arm - 1e-12
    stop_line = float(stop_price)
    trail_line = peak * (1.0 - float(settings["risk.trail_pct"]) / 100.0)
    action = None
    if float(price) <= stop_line + 1e-12:
        action = "stop"
    elif active and float(price) <= trail_line + 1e-12:
        action = "trail"
    else:
        held = (now - opened_at).total_seconds()
        timeout = float(settings["risk.timeout_h"]) * 3600.0
        ceiling = float(entry) * (1.0 + float(settings["risk.timeout_min_gain_pct"]) / 100.0)
        if held >= timeout and float(price) < ceiling:
            action = "timeout"
    return Poll(peak, active, action, stop_line, trail_line)


def ratchet(entry, stop_price, new_stop_pct: float) -> float:
    """Tighter (higher) stop line only. A looser setting does not move this one."""
    new_line = float(entry) * (1.0 - float(new_stop_pct) / 100.0)
    if new_line > float(stop_price) + 1e-12:
        return new_line
    return float(stop_price)


def position_stop_pct(entry, stop_price) -> float:
    return (1.0 - float(stop_price) / float(entry)) * 100.0


def apply_stop_pct(db, new_pct: float, ts: str, who: str = "telegram"):
    old = float(db.get_setting("risk.stop_pct"))
    db.set_setting("risk.stop_pct", new_pct, who=who, ts=ts)
    for pos in db.open_positions():
        updated = ratchet(pos["entry_price"], pos["stop_price"], new_pct)
        if updated > float(pos["stop_price"]) + 1e-12:
            db.update_position(pos["id"], stop_price=updated)
    return old, float(new_pct)


def describe_exit(name: str, reason: str, stop_pct: float, net: float, price_pct: float, timeout_h: float) -> dict:
    loss = abs(net)
    if reason == "stop":
        label = stop_label(stop_pct)
        return {
            "text": f"{name} hit the -{label}% stop · -${loss:.2f}",
            "title": f"Stopped {name}",
            "subtitle": f"Risk · -{label}% stop",
            "value": f"-${loss:.2f}",
            "kind": "stopped",
            "mark": f"-{label}% · -${loss:.2f}",
        }
    if reason == "trail":
        return {
            "text": f"Sold {name} · {money(net, signed=True)} ({pct_signed(price_pct)})",
            "title": f"Sold {name}",
            "subtitle": "Risk · trailing stop",
            "value": f"{money(net, signed=True)} · {pct_signed(price_pct)}",
            "kind": "sold",
            "mark": money(net, signed=True),
        }
    if reason == "timeout":
        return {
            "text": f"Sell {name}, dead money · {pct_signed(price_pct)} in {stop_label(timeout_h)}h",
            "title": f"Sell {name}",
            "subtitle": f"Risk · dead money {stop_label(timeout_h)}h",
            "value": money(net, signed=True),
            "kind": "sold" if net >= 0 else "stopped",
            "mark": money(net, signed=True),
        }
    return {
        "text": f"Sell {name}, liquidity pulled · {money(net, signed=True)}",
        "title": f"Sell {name}",
        "subtitle": "Risk · liquidity pulled",
        "value": money(net, signed=True),
        "kind": "sold" if net >= 0 else "stopped",
        "mark": money(net, signed=True),
    }


class Risk:
    def __init__(self, db, bus, sources, clock, notifier=None):
        self.db = db
        self.bus = bus
        self.sources = sources
        self.clock = clock
        self.notifier = notifier
        self.backoff_until = None

    def _night(self) -> str:
        raw = self.db.settings_map().get("desk.night_started") or iso(self.clock.now())
        return str(raw)[:10]

    def _symbol(self, mint: str) -> str:
        coin = self.db.get_coin(mint)
        if coin and coin["symbol"]:
            return coin["symbol"]
        return mint[:4]

    async def _push(self, text: str, mint: str, paper: bool, extra=None):
        from desk.telegram import Push
        callbacks = [("Sold", f"sold:{mint}")]
        if extra:
            callbacks = extra
        push = Push(
            text=("PAPER · " + text) if paper and not text.startswith("PAPER · ") else text,
            url=links.coin_url(mint, "solana"),
            url_label="Sell on FOMO",
            callbacks=callbacks,
            paper=paper,
        )
        if not self.notifier:
            log.info("push failed")
            return False
        for _ in range(3):
            try:
                if await self.notifier.push(push):
                    return True
            except Exception as exc:
                log.info("push error %s", type(exc).__name__)
        log.info("push failed")
        return False

    def _close_cash(self, pos, exit_price: float, ts: str):
        settings = self.db.settings()
        discount = float(settings["desk.fees_ref_discount"])
        size = float(pos["size_usd"])
        entry = float(pos["entry_price"])
        exit_fee = one_side(size, discount)
        proceeds = size * (float(exit_price) / entry)
        cash = float(settings["desk.cash"]) + proceeds - exit_fee
        self.db.set_setting("desk.cash", f"{cash:.4f}", who="risk", ts=ts)
        net, fees = pnl(size, entry, exit_price, discount)
        return net, fees

    async def exit_position(self, pos, reason: str, exit_price: float, now: datetime):
        settings = self.db.settings()
        ts = iso(now)
        night = self._night()
        mode = pos["mode"] or settings["desk.mode"]
        paper = mode != "LIVE"
        name = self._symbol(pos["mint"])
        stop_pct = position_stop_pct(pos["entry_price"], pos["stop_price"])
        price_pct = (float(exit_price) / float(pos["entry_price"]) - 1.0) * 100.0
        net, fees = pnl(float(pos["size_usd"]), pos["entry_price"], exit_price, float(settings["desk.fees_ref_discount"]))
        view = describe_exit(name, reason, stop_pct, net, price_pct, float(settings["risk.timeout_h"]))
        if paper:
            net, fees = self._close_cash(pos, exit_price, ts)
            view = describe_exit(name, reason, stop_pct, net, price_pct, float(settings["risk.timeout_h"]))
            self.db.update_position(
                pos["id"],
                peak_price=pos["peak_price"],
                trail_active=pos["trail_active"],
                closed_at=ts,
                exit_price=exit_price,
                exit_reason=reason,
                pnl_usd=net,
                fees_usd=fees,
            )
            self.db.add_activity(
                ts, view["kind"], pos["mint"], "Risk", view["title"], view["subtitle"], view["value"], night
            )
            self.db.add_decision("risk", ts, pos["mint"], name, reason, view["subtitle"], night)
            self.db.add_balance(ts, self._equity())
            await self._push(view["text"], pos["mint"], True)
        else:
            self.db.update_position(
                pos["id"],
                exit_price=exit_price,
                exit_reason=reason,
                exit_started_at=ts,
                pnl_usd=net,
                fees_usd=fees,
                peak_price=pos["peak_price"],
                trail_active=pos["trail_active"],
            )
            await self._push(view["text"], pos["mint"], False)
        log.info(
            "exit mint=%s reason=%s price=%s pnl=%s mode=%s",
            pos["mint"], reason, exit_price, net, mode,
        )
        return view

    def _equity(self) -> float:
        from desk.portfolio import equity
        return equity(self.db)

    async def confirm_sold(self, mint: str) -> str:
        pos = self.db.open_position_for(mint)
        if pos is None or not pos["exit_reason"]:
            return "nothing exiting"
        ts = iso(self.clock.now())
        if pos["mode"] == "LIVE":
            self._close_cash(pos, pos["exit_price"], ts)
        self.db.update_position(pos["id"], closed_at=ts)
        name = self._symbol(mint)
        view = describe_exit(
            name,
            pos["exit_reason"],
            position_stop_pct(pos["entry_price"], pos["stop_price"]),
            float(pos["pnl_usd"] or 0),
            (float(pos["exit_price"]) / float(pos["entry_price"]) - 1) * 100,
            float(self.db.get_setting("risk.timeout_h")),
        )
        self.db.add_activity(
            ts, view["kind"], mint, "Risk", view["title"], view["subtitle"], view["value"], self._night()
        )
        self.db.add_balance(ts, self._equity())
        return "closed"

    async def poll_once(self):
        now = self.clock.now()
        if self.backoff_until and now < self.backoff_until:
            return
        settings = self.db.settings()
        positions = list(self.db.open_positions())
        if not positions:
            self.db.heartbeat("risk", iso(now))
            return
        mints = [p["mint"] for p in positions]
        try:
            raw = await self.sources.dex_pairs(mints)
        except RateLimit:
            from datetime import timedelta
            self.backoff_until = now + timedelta(seconds=float(settings["risk.http_backoff_s"]))
            log.info("429 backoff missed polls n=%s", len(positions))
            return
        except Exception as exc:
            log.info("dex miss err=%s", type(exc).__name__)
            raw = []
        mapped = fields.assign_pairs(raw, mints) if raw is not None else {}
        ts = iso(now)
        for pos in positions:
            pair = mapped.get(pos["mint"])
            price = None if not pair else pair.get("price_usd")
            liq = None if not pair else pair.get("liquidity_usd")
            if liq is not None and liq <= 0:
                last = self.db.last_price(pos["mint"])
                exit_price = float(last["price"]) if last else float(pos["entry_price"])
                log.info(
                    "poll mint=%s price=%s peak=%s stop=%s trail=%s action=pulled",
                    pos["mint"], exit_price, pos["peak_price"], pos["stop_price"], "",
                )
                await self.exit_position(pos, "pulled", exit_price, now)
                continue
            if price is None:
                misses = int(pos["no_price_misses"] or 0) + 1
                pushed = int(pos["no_price_pushed"] or 0)
                self.db.update_position(pos["id"], no_price_misses=misses)
                limit = int(settings["risk.no_price_polls"])
                if misses >= limit and not pushed:
                    self.db.update_position(pos["id"], no_price_pushed=1)
                    name = self._symbol(pos["mint"])
                    await self._push(f"{name}: no price for 1 min", pos["mint"], pos["mode"] != "LIVE")
                log.info("poll mint=%s price=none misses=%s action=hold", pos["mint"], misses)
                continue
            self.db.add_price(pos["mint"], ts, price, pair.get("volume_m5"))
            opened = parse_iso(pos["opened_at"])
            result = poll_position(
                pos["entry_price"], pos["peak_price"], pos["stop_price"],
                pos["trail_active"], opened, price, now, settings,
            )
            self.db.update_position(
                pos["id"],
                peak_price=result.peak,
                trail_active=1 if result.trail_active else 0,
                no_price_misses=0,
            )
            log.info(
                "poll mint=%s price=%s peak=%s stop=%s trail=%s action=%s",
                pos["mint"], price, result.peak, result.stop_line, result.trail_line, result.action or "hold",
            )
            if result.action:
                fresh = self.db.open_position_for(pos["mint"])
                fresh_dict = dict(fresh)
                fresh_dict["peak_price"] = result.peak
                fresh_dict["trail_active"] = 1 if result.trail_active else 0
                await self.exit_position(fresh_dict, result.action, price, now)
        await self._remind(now)
        self.db.heartbeat("risk", iso(self.clock.now()))

    async def _remind(self, now: datetime):
        settings = self.db.settings()
        wait = float(settings["risk.sold_remind_min"]) * 60
        for pos in self.db.exiting_positions():
            if int(pos["reminded"] or 0) or not pos["exit_started_at"]:
                continue
            if (now - parse_iso(pos["exit_started_at"])).total_seconds() < wait:
                continue
            self.db.update_position(pos["id"], reminded=1)
            name = self._symbol(pos["mint"])
            await self._push(f"{name}: still open", pos["mint"], pos["mode"] != "LIVE")

    async def run(self):
        while True:
            now = self.clock.now()
            open_count = len(self.db.open_positions()) + len(self.db.exiting_positions())
            paused = bool(int(self.db.get_setting("paused.risk") or 0))
            if paused and open_count == 0:
                self.db.heartbeat("risk", iso(now))
                await asyncio.sleep(0.5)
                continue
            try:
                await self.poll_once()
            except Exception as exc:
                log.info("poll crash %s", type(exc).__name__)
            seconds = float(self.db.get_setting("risk.poll_s") or 15)
            await asyncio.sleep(max(0.05, seconds))
