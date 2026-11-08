"""Executor. Arithmetic and one button. Never places an order."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from desk import fields, links
from desk.fees import one_side
from desk.log import get_logger
from desk.util import cents, iso

log = get_logger("executor")


@dataclass
class SizeResult:
    ok: bool
    size: float | None
    pct: float | None
    reason: str | None
    capped: bool
    bumped: bool


def compute_size(stack: float, free: float, pool_liq, settings: dict) -> SizeResult:
    stack_pct = float(settings["executor.stack_pct"])
    minimum = float(settings["executor.min_ticket_usd"])
    below = str(settings["executor.below_min"])
    raw = cents(float(stack) * stack_pct / 100.0)
    size = raw
    capped = False
    if pool_liq is not None:
        cap = cents(float(pool_liq) * float(settings["executor.liq_cap_pct"]) / 100.0)
        if size > cap:
            size = cap
            capped = True
    bumped = False
    if size < minimum - 1e-9:
        if below == "bump" and float(free) + 1e-9 >= minimum:
            size = cents(minimum)
            bumped = True
        else:
            return SizeResult(False, None, None, "under $47.50", capped, False)
    if size > float(free) + 1e-9:
        return SizeResult(False, None, None, "no free stack", capped, bumped)
    pct = (size / float(stack) * 100.0) if float(stack) else 0.0
    return SizeResult(True, size, pct, None, capped, bumped)


def ticket_warning(stack: float, settings: dict) -> str | None:
    raw = float(stack) * float(settings["executor.stack_pct"]) / 100.0
    minimum = float(settings["executor.min_ticket_usd"])
    below = str(settings["executor.below_min"])
    if raw < minimum and below == "skip":
        return "every coin will be skipped"
    if below == "bump" and float(stack) + 1e-9 < minimum:
        return "every coin will be skipped"
    return None


def recheck(scan_liq, now_liq, analyst_price, now_price, settings: dict) -> str | None:
    if now_price is None:
        return "no price"
    if scan_liq and now_liq is not None:
        limit = float(settings["executor.liq_drop_skip_pct"]) / 100.0
        if float(now_liq) < float(scan_liq) * (1.0 - limit):
            return "liquidity dropping"
    if analyst_price and now_price is not None:
        limit = float(settings["executor.ran_skip_pct"]) / 100.0
        if float(now_price) > float(analyst_price) * (1.0 + limit):
            return "ran before the push"
    return None


def _pct_text(pct: float) -> str:
    return f"{pct:.2f}".rstrip("0").rstrip(".")


def buy_text(name: str, size: float, pct: float, score, stop_pct, trail_pct) -> str:
    from desk.util import stop_label
    return (
        f"Buy {name} · ${size:.2f} · {_pct_text(pct)}% of stack\n"
        f"Auditor 8/8 · Analyst {score} · stop -{stop_label(stop_pct)}% · trail {stop_label(trail_pct)}%"
    )


class Executor:
    def __init__(self, db, bus, sources, clock, notifier=None):
        self.db = db
        self.bus = bus
        self.sources = sources
        self.clock = clock
        self.notifier = notifier
        self._warned = False

    def _night(self) -> str:
        raw = self.db.settings_map().get("desk.night_started") or iso(self.clock.now())
        return str(raw)[:10]

    def _paused(self) -> bool:
        return bool(int(self.db.get_setting("paused.executor") or 0))

    def equity(self) -> float:
        from desk.portfolio import equity
        return equity(self.db)

    def stack_and_free(self) -> tuple[float, float]:
        settings = self.db.settings()
        stack = float(settings["desk.stack"])
        cash = float(settings["desk.cash"])
        open_cost = sum(float(p["size_usd"]) for p in self.db.open_positions())
        open_cost += sum(float(p["size_usd"]) for p in self.db.exiting_positions())
        free = min(cash, stack - open_cost)
        return stack, max(0.0, free)

    def warn_if_needed(self) -> str | None:
        stack, _free = self.stack_and_free()
        warning = ticket_warning(stack, self.db.settings())
        if warning and not self._warned:
            self._warned = True
            log.info("ticket %s", warning)
        return warning

    async def _send(self, push) -> bool:
        if not self.notifier:
            log.info("push failed")
            return False
        last = False
        for _ in range(3):
            try:
                last = await self.notifier.push(push)
                if last:
                    return True
            except Exception as exc:
                log.info("push error %s", type(exc).__name__)
                last = False
        log.info("push failed")
        return bool(last)

    def open_position(self, mint: str, price: float, size: float, mode: str, night: str, ts: str):
        settings = self.db.settings()
        stop_pct = float(settings["risk.stop_pct"])
        stop_price = float(price) * (1.0 - stop_pct / 100.0)
        discount = float(settings["desk.fees_ref_discount"])
        fee = one_side(size, discount)
        cash = float(settings["desk.cash"]) - float(size) - fee
        self.db.set_setting("desk.cash", f"{cash:.4f}", who="executor", ts=ts)
        self.db.add_position(
            mint=mint,
            opened_at=ts,
            entry_price=price,
            size_usd=size,
            peak_price=price,
            stop_price=stop_price,
            trail_active=0,
            mode=mode,
            night=night,
        )
        coin = self.db.get_coin(mint)
        symbol = coin["symbol"] if coin and coin["symbol"] else mint[:4]
        sig = self.db.latest_signal(mint, night)
        pct = sig["stack_pct"] if sig and sig["stack_pct"] is not None else settings["executor.stack_pct"]
        hhmm = ts[11:16]
        self.db.add_activity(
            ts, "bought", mint, "Executor",
            f"Bought {symbol}",
            f"Executor · {hhmm}",
            f"${float(size):.2f} · {_pct_text(float(pct))}% of stack",
            night,
        )
        self.db.add_decision("executor", ts, mint, symbol, "bought", f"${float(size):.2f}", night)
        self.db.add_balance(ts, self.equity())

    async def handle(self, item: dict) -> dict:
        settings = self.db.settings()
        mode = str(settings["desk.mode"])
        paper = mode != "LIVE"
        mint = item["mint"]
        night = self._night()
        ts = iso(self.clock.now())
        symbol = item.get("symbol") or mint[:4]
        name = item.get("name") or symbol
        if self.db.is_skipped(mint):
            return {"agent": "executor", "mint": mint, "status": "skipped", "reason": "skipped"}
        existing = self.db.latest_signal(mint, night)
        if existing:
            return {"agent": "executor", "mint": mint, "status": existing["status"], "reason": "already"}
        try:
            raw = await self.sources.dex_pairs([mint])
            pair = fields.assign_pairs(raw, [mint]).get(mint)
        except Exception:
            pair = None
        price = None if not pair else pair.get("price_usd")
        liq = None if not pair else pair.get("liquidity_usd")
        if price is not None:
            self.db.add_price(mint, ts, price, None if not pair else pair.get("volume_m5"))
        problem = recheck(
            item.get("liquidityUsd"),
            liq,
            item.get("analystPrice") or item.get("price_usd"),
            price,
            settings,
        )
        stack, free = self.stack_and_free()
        sized = compute_size(stack, free, liq, settings) if problem is None else None
        if problem or sized is None or not sized.ok:
            reason = problem or (sized.reason if sized else "no price")
            link = links.coin_url(mint, "solana")
            self.db.add_signal(
                mint=mint, ts=ts, price=price, size_usd=None, stack_pct=None, link=link,
                mode=mode, status="skipped", reason=reason, score=item.get("score"), night=night,
            )
            self.db.add_decision("executor", ts, mint, symbol, "skipped", reason, night)
            log.info(
                "skip mint=%s stack=%s raw=%s cap=%s size=%s mode=%s status=skipped reason=%s link=%s",
                mint, stack, None if not sized else sized.size, sized.capped if sized else False,
                None, mode, reason, link,
            )
            return {"agent": "executor", "mint": mint, "status": "skipped", "reason": reason, "link": link}
        link = links.coin_url(mint, "solana")
        text = buy_text(name, sized.size, sized.pct, item.get("score"), settings["risk.stop_pct"], settings["risk.trail_pct"])
        if paper:
            text = "PAPER · " + text
        from desk.telegram import Push
        push = Push(
            text=text,
            url=link,
            url_label="Buy on FOMO",
            callbacks=[("Bought", f"bought:{mint}"), ("Skip", f"skip:{mint}")],
            paper=paper,
        )
        delivered = await self._send(push)
        status = "sent"
        self.db.add_signal(
            mint=mint, ts=ts, price=price, size_usd=sized.size, stack_pct=sized.pct, link=link,
            mode=mode, status="sent", reason=None, score=item.get("score"), night=night,
        )
        sig = self.db.latest_signal(mint, night)
        if paper:
            status = "bought"
            self.db.update_signal(sig["id"], status="bought")
            self.open_position(mint, price, sized.size, mode, night, ts)
            if not delivered:
                log.info("push failed")
        elif not delivered:
            status = "sent"
            log.info("push failed")
        log.info(
            "mint=%s stack=%s raw_capped=%s size=%s mode=%s status=%s link=%s",
            mint, stack, sized.capped, sized.size, mode, status, link,
        )
        self.db.heartbeat("executor", iso(self.clock.now()))
        return {
            "agent": "executor",
            "mint": mint,
            "signalPrice": price,
            "sizeUsd": sized.size,
            "stackPct": sized.pct,
            "link": link,
            "mode": mode,
            "status": status,
            "reason": None,
        }

    async def confirm_bought(self, mint: str) -> str:
        night = self._night()
        sig = self.db.latest_signal(mint, night)
        if sig is None or sig["status"] not in ("sent",):
            return "nothing to fill"
        if self.db.open_position_for(mint):
            return "already open"
        ts = iso(self.clock.now())
        self.db.update_signal(sig["id"], status="bought")
        self.open_position(mint, sig["price"], sig["size_usd"], sig["mode"], night, ts)
        return "opened"

    async def confirm_skip(self, mint: str) -> str:
        night = self._night()
        sig = self.db.latest_signal(mint, night)
        self.db.add_skip(mint, "report")
        if sig and sig["status"] == "sent":
            self.db.update_signal(sig["id"], status="skipped", reason="skip")
        return "skipped"

    async def expire_live(self):
        settings = self.db.settings()
        if settings["desk.mode"] != "LIVE":
            return
        timeout = float(settings["executor.fill_timeout_min"]) * 60
        now = self.clock.now()
        for sig in self.db.signals(self._night()):
            if sig["status"] != "sent":
                continue
            from desk.util import parse_iso
            age = (now - parse_iso(sig["ts"])).total_seconds()
            if age >= timeout:
                self.db.update_signal(sig["id"], status="missed", reason="missed")
                self.db.add_decision(
                    "executor", iso(now), sig["mint"], sig["mint"][:4], "missed", "no tap", self._night()
                )

    async def run(self):
        self.warn_if_needed()
        while True:
            self.db.heartbeat("executor", iso(self.clock.now()))
            await self.expire_live()
            if self._paused():
                await asyncio.sleep(0.5)
                continue
            try:
                item = await asyncio.wait_for(self.bus.executor.get(), timeout=1.0)
            except TimeoutError:
                continue
            try:
                await self.handle(item)
            except Exception as exc:
                log.info("handle crash %s", type(exc).__name__)
