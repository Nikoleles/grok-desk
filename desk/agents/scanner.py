"""Scanner. Count the graduations, cut the ones that already failed a gate.

Waiting room: a coin sits until it is old enough to promote. "Too young" is a
wait, not a kill — otherwise nothing would survive the 10 minute pool-age
gate. The kill order is liquidity, top 10, 1h change, 24h runner, then the
wait deadline. Pool address is the DexScreener pair, never the migration label.
Creator is RugCheck's creator, never the migration message.
"""

from __future__ import annotations

import asyncio
import json

import websockets

from desk import fields
from desk.log import get_logger
from desk.sources import PUMP_WS
from desk.util import compact_usd, iso, parse_iso

log = get_logger("scanner")


def evaluate(pair: dict | None, top10, age_min: float, settings: dict) -> tuple[str, str]:
    """Return (kill|wait|pass, reason)."""
    max_wait = float(settings["scanner.max_wait_min"])
    min_age = float(settings["scanner.min_pool_age_min"])
    if pair is None:
        if age_min > max_wait:
            return "kill", "no pool"
        return "wait", "no pool"
    liq = pair.get("liquidity_usd")
    if liq is None:
        if age_min > max_wait:
            return "kill", "no pool"
        return "wait", "no pool"
    if liq < float(settings["scanner.min_liquidity_usd"]):
        return "kill", f"liq {compact_usd(liq)}"
    if top10 is None:
        if age_min > max_wait:
            return "kill", "no data: top 10"
        return "wait", "no data: top 10"
    if top10 > float(settings["scanner.max_top10_pct"]):
        shown = f"{top10:.0f}" if top10 >= 10 else f"{top10:.1f}"
        return "kill", f"top 10 hold {shown}%"
    h1 = pair.get("change_h1")
    if h1 is not None and h1 > float(settings["scanner.max_h1_change_pct"]):
        return "kill", f"h1 +{h1:.0f}%"
    h24 = pair.get("change_h24")
    if h24 is not None and h24 > float(settings["scanner.runner_h24_pct"]):
        return "kill", f"runner +{h24:.0f}%"
    if age_min > max_wait:
        return "kill", f"waited {int(age_min)}m"
    if age_min < min_age:
        return "wait", "too young"
    return "pass", "→ auditor"


class Scanner:
    def __init__(self, db, bus, sources, clock, notifier=None):
        self.db = db
        self.bus = bus
        self.sources = sources
        self.clock = clock
        self.notifier = notifier
        self._backoff = 1

    def _night(self) -> str:
        raw = self.db.settings_map().get("desk.night_started") or iso(self.clock.now())
        return str(raw)[:10]

    def _paused(self) -> bool:
        return bool(int(self.db.get_setting("paused.scanner") or 0))

    async def ingest(self, raw: dict):
        mig = fields.read_migration(raw if isinstance(raw, dict) else {})
        mint = mig.get("mint")
        if not mint:
            return None
        if self.db.get_coin(mint):
            return None
        now = self.clock.now()
        night = self._night()
        creator = None
        report = None
        try:
            report = await self.sources.rug_report(mint)
            creator = fields.read_creator(report)
        except Exception as exc:
            log.info("rugcheck creator miss mint=%s err=%s", mint, type(exc).__name__)
        balance = None
        if creator:
            try:
                resp = await self.sources.rpc(
                    "getTokenAccountsByOwner",
                    [creator, {"mint": mint}, {"encoding": "jsonParsed"}],
                )
                balance = fields.creator_balance_raw(resp)
            except Exception as exc:
                log.info("creator balance miss mint=%s err=%s", mint, type(exc).__name__)
        self.db.upsert_coin(
            mint=mint,
            pool_label=mig.get("pool_label"),
            migrated_at=iso(now),
            creator=creator,
            creator_balance_at_migration=None if balance is None else str(balance),
            status="waiting",
            night=night,
            signature=mig.get("signature"),
        )
        await self.evaluate_mint(mint)
        return mint

    async def _pair(self, mint: str):
        raw = await self.sources.dex_pairs([mint])
        return fields.assign_pairs(raw, [mint]).get(mint)

    async def _top10(self, mint: str, pool: str | None):
        try:
            info = await self.sources.rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
        except Exception:
            return None
        parsed = fields.read_mint_account(info)
        if parsed.get("error") or fields.is_missing(parsed.get("supply")):
            return None
        try:
            largest = await self.sources.rpc("getTokenLargestAccounts", [mint])
        except Exception:
            return None
        if not isinstance(largest, dict) or largest.get("error"):
            return None
        rows = fields.read_largest(largest)
        addrs = [r["address"] for r in rows if r.get("address")]
        owners = {}
        if addrs:
            try:
                multi = await self.sources.rpc(
                    "getMultipleAccounts", [addrs, {"encoding": "jsonParsed"}]
                )
                owners = fields.read_owners(multi, addrs)
            except Exception:
                owners = {}
        return fields.top10_pct(rows, owners, parsed["supply"], pool)

    async def evaluate_mint(self, mint: str):
        coin = self.db.get_coin(mint)
        if coin is None or coin["status"] != "waiting":
            return None
        now = self.clock.now()
        migrated = parse_iso(coin["migrated_at"])
        age_min = (now - migrated).total_seconds() / 60.0
        try:
            pair = await self._pair(mint)
        except Exception as exc:
            log.info("dex miss mint=%s err=%s", mint, type(exc).__name__)
            pair = None
        settings = self.db.settings()
        top10 = None
        if pair and pair.get("liquidity_usd") is not None:
            if pair["liquidity_usd"] >= float(settings["scanner.min_liquidity_usd"]):
                top10 = await self._top10(mint, pair.get("pair_address"))
        action, reason = evaluate(pair, top10, age_min, settings)
        ts = iso(now)
        if pair and pair.get("price_usd") is not None:
            self.db.add_price(mint, ts, pair["price_usd"], pair.get("volume_m5"))
            self.db.upsert_coin(
                mint=mint,
                name=pair.get("name"),
                symbol=pair.get("symbol"),
                pool=pair.get("pair_address"),
                image=pair.get("image"),
                liq_at_scan=pair.get("liquidity_usd"),
                price_at_scan=pair.get("price_usd"),
                top10_pct=top10,
            )
        self.db.add_scan(
            mint=mint, ts=ts,
            liquidity_usd=None if not pair else pair.get("liquidity_usd"),
            top10_pct=top10,
            h1_change_pct=None if not pair else pair.get("change_h1"),
            age_min=age_min,
            verdict=action,
            reason=reason,
        )
        symbol = (pair or {}).get("symbol") or coin["symbol"] or mint[:4]
        name = (pair or {}).get("name") or coin["name"] or symbol
        if action == "kill":
            self.db.upsert_coin(mint=mint, status="killed", kill_reason=reason, symbol=symbol, name=name)
            self.db.add_decision("scanner", ts, mint, symbol, "killed", reason, self._night())
            log.info("kill mint=%s reason=%s age=%.1f", mint, reason, age_min)
            return action
        if action == "pass" and pair:
            self.db.upsert_coin(mint=mint, status="passed", symbol=symbol, name=name)
            self.db.add_decision("scanner", ts, mint, symbol, "passed", "→ auditor", self._night())
            payload = {
                "agent": "scanner",
                "mint": mint,
                "pool": pair.get("pair_address"),
                "name": name,
                "symbol": symbol,
                "ts": ts,
                "migratedAt": coin["migrated_at"],
                "creator": coin["creator"],
                "creatorBalance": coin["creator_balance_at_migration"],
                "liquidityUsd": pair.get("liquidity_usd"),
                "price_usd": pair.get("price_usd"),
                "marketCap": pair.get("market_cap"),
                "top10Pct": top10,
                "changeM5": pair.get("change_m5"),
                "changeH1": pair.get("change_h1"),
                "changeH24": pair.get("change_h24"),
                "volumeM5": pair.get("volume_m5"),
                "volumeH1": pair.get("volume_h1"),
                "volumeH24": pair.get("volume_h24"),
                "buysM5": pair.get("buys_m5"),
                "sellsM5": pair.get("sells_m5"),
                "buysH1": pair.get("buys_h1"),
                "sellsH1": pair.get("sells_h1"),
                "image": pair.get("image"),
            }
            await self.bus.auditor.put(payload)
            log.info("pass mint=%s pool=%s liq=%s", mint, pair.get("pair_address"), pair.get("liquidity_usd"))
        return action

    async def poll_once(self):
        if self._paused():
            return
        night = self._night()
        waiting = list(self.db.waiting_coins(night))
        if not waiting:
            self.db.heartbeat("scanner", iso(self.clock.now()))
            return
        mints = [c["mint"] for c in waiting]
        try:
            raw = await self.sources.dex_pairs(mints)
            mapped = fields.assign_pairs(raw, mints)
        except Exception as exc:
            log.info("batch dex miss err=%s", type(exc).__name__)
            mapped = {}
        now = iso(self.clock.now())
        for coin in waiting:
            pair = mapped.get(coin["mint"])
            if pair and pair.get("price_usd") is not None:
                self.db.add_price(coin["mint"], now, pair["price_usd"], pair.get("volume_m5"))
            await self.evaluate_mint(coin["mint"])
        self.db.heartbeat("scanner", iso(self.clock.now()))

    async def _poll_loop(self):
        while True:
            try:
                await self.poll_once()
            except Exception as exc:
                log.info("poll error %s", type(exc).__name__)
            seconds = float(self.db.get_setting("scanner.poll_s") or 60)
            await asyncio.sleep(max(1.0, seconds))

    async def _ws_loop(self):
        while True:
            try:
                async with websockets.connect(PUMP_WS, ping_interval=20) as ws:
                    await ws.send(json.dumps({"method": "subscribeMigration"}))
                    self._backoff = 1
                    log.info("pumpportal connected")
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        if isinstance(msg, dict):
                            await self.ingest(msg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                now = iso(self.clock.now())
                self.db.add_ws(now, "reconnect")
                log.info("pumpportal reconnect err=%s", type(exc).__name__)
                await asyncio.sleep(self._backoff)
                self._backoff = min(30, self._backoff * 2)

    async def run(self):
        self.db.heartbeat("scanner", iso(self.clock.now()))
        await asyncio.gather(self._poll_loop(), self._ws_loop())
