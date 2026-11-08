"""Auditor. Eight layers, stop at the first red. 7 of 8 is a no.

Raw RugCheck, RPC, and the insider graph are read only through fields.py.
LP percent is labeled locked, never burned. Graph links are the edges.
"""

from __future__ import annotations

import asyncio

from desk import fields
from desk.log import get_logger
from desk.util import fmt_pct, iso, parse_iso

log = get_logger("auditor")

TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

LAYER_NAMES = {
    1: "Mint authority",
    2: "Freeze authority",
    3: "Dev wallet",
    4: "Top 10",
    5: "Liquidity",
    6: "Pool age",
    7: "Bundled wallets",
    8: "Delegations",
}
LAYER_SLUG = {
    1: "mint authority",
    2: "freeze",
    3: "dev",
    4: "top 10",
    5: "liquidity",
    6: "pool age",
    7: "bundles",
    8: "delegations",
}


class Auditor:
    def __init__(self, db, bus, sources, clock, notifier=None):
        self.db = db
        self.bus = bus
        self.sources = sources
        self.clock = clock
        self.notifier = notifier
        self._layer8_announced = False

    def _night(self) -> str:
        raw = self.db.settings_map().get("desk.night_started") or iso(self.clock.now())
        return str(raw)[:10]

    def _paused(self) -> bool:
        return bool(int(self.db.get_setting("paused.auditor") or 0))

    async def _sleep(self, seconds: float):
        if seconds > 0:
            await asyncio.sleep(seconds)

    async def _retry(self, fn):
        settings = self.db.settings()
        n = max(1, int(settings["auditor.retry_n"]))
        delay = float(settings["auditor.retry_s"])
        last = None
        for i in range(n):
            try:
                result = await fn()
            except Exception as exc:
                last = exc
                result = None
            else:
                if not (isinstance(result, dict) and result.get("error")):
                    return result
                last = result
            if i < n - 1:
                await self._sleep(delay)
        return last

    def _flag_layer8(self, rpc_url: str):
        self.db.set_setting("desk.layer8_down", 1, who="auditor", ts=iso(self.clock.now()))
        self.db.set_setting("desk.layer8_rpc", rpc_url, who="auditor", ts=iso(self.clock.now()))
        if self._layer8_announced:
            return
        self._layer8_announced = True
        log.info("layer 8 down rpc refused")

    async def _announce_layer8(self):
        if not self.notifier:
            return
        from desk.links import SIGNUP_URL
        from desk.telegram import Push

        await self.notifier.push(Push(
            text="layer 8 down, every coin is red until rpc_url changes",
            url=SIGNUP_URL,
            url_label="Open FOMO",
            callbacks=[],
            paper=self.db.get_setting("desk.mode") == "PAPER",
        ))

    async def audit(self, item: dict, handoff: bool = True) -> dict:
        mint = item["mint"]
        night = self._night()
        now = self.clock.now()
        ts = iso(now)
        symbol = item.get("symbol") or mint[:4]
        name = item.get("name") or symbol
        if self.db.rejected_tonight(mint, night):
            return self._cached(mint, night, symbol)
        existing = self.db.audits(mint, night)
        if existing and any(r["pass"] == 0 or r["layer"] == 8 for r in existing):
            return self._cached(mint, night, symbol)

        settings = self.db.settings()
        rpc_url = str(settings["desk.rpc_url"])
        if str(self.db.settings_map().get("desk.layer8_rpc") or "") not in ("", rpc_url):
            if str(self.db.settings_map().get("desk.layer8_rpc") or "") != rpc_url:
                self.db.set_setting("desk.layer8_down", 0, who="auditor", ts=ts)
                self._layer8_announced = False

        self.db.upsert_coin(
            mint=mint,
            name=name,
            symbol=symbol,
            pool=item.get("pool"),
            migrated_at=item.get("migratedAt"),
            creator=item.get("creator"),
            creator_balance_at_migration=item.get("creatorBalance"),
            image=item.get("image"),
            liq_at_scan=item.get("liquidityUsd"),
            night=night,
            status=self.db.get_coin(mint)["status"] if self.db.get_coin(mint) else "passed",
        )
        started = self.clock.now()
        layers = []
        stopped = False
        reason = None
        holders = None
        mint_info = None

        async def finish(n, ok, value, source, raw=""):
            nonlocal stopped, reason
            ms = int((self.clock.now() - started).total_seconds() * 1000)
            log.info("layer mint=%s n=%s value=%s ms=%s raw=%s", mint, n, value, ms, raw)
            layer = {
                "n": n,
                "name": LAYER_NAMES[n],
                "pass": ok,
                "value": value,
                "source": source,
            }
            layers.append(layer)
            self.db.add_audit(mint, iso(self.clock.now()), n, LAYER_NAMES[n], value, ok, source, night)
            await self.bus.emit({"type": "audit_layer", "mint": mint, "layer": layer})
            if ok is False:
                stopped = True
                reason = value
            return layer

        # 1–2 share one mint account read.
        fetched = await self._retry(
            lambda: self.sources.rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
        )
        if isinstance(fetched, dict) and not fetched.get("error"):
            mint_info = fields.read_mint_account(fetched)
        else:
            mint_info = {"mint_authority": fields.MISSING, "freeze_authority": fields.MISSING,
                         "supply": fields.MISSING, "decimals": fields.MISSING, "error": True}

        auth = mint_info.get("mint_authority")
        if fields.is_missing(auth) or mint_info.get("error"):
            await finish(1, False, "no data: mint authority", "rpc")
        elif auth in (None, ""):
            await finish(1, True, "Revoked", "rpc", raw="null")
        else:
            await finish(1, False, "mint not revoked", "rpc", raw=str(auth))

        if not stopped:
            freeze = mint_info.get("freeze_authority")
            if fields.is_missing(freeze):
                await finish(2, False, "no data: freeze", "rpc")
            elif freeze in (None, ""):
                await finish(2, True, "Revoked", "rpc", raw="null")
            else:
                await finish(2, False, "freeze not revoked", "rpc", raw=str(freeze))

        if not stopped:
            await self._layer_dev(item, mint_info, settings, finish)

        if not stopped:
            await self._layer_top10(item, mint_info, settings, finish)

        if not stopped:
            await self._layer_liquidity(item, settings, finish)

        if not stopped:
            await self._layer_age(item, settings, finish)

        if not stopped:
            await self._layer_bundles(mint, settings, finish)

        if not stopped:
            holders = await self._layer_delegations(mint, settings, finish)

        for n in range(len(layers) + 1, 9):
            layers.append({
                "n": n,
                "name": LAYER_NAMES[n],
                "pass": None,
                "value": "not checked",
                "source": "",
            })
            self.db.add_audit(mint, ts, n, LAYER_NAMES[n], "not checked", None, "", night)

        passed = reason is None and len([l for l in layers if l["pass"] is True]) == 8
        if holders is not None:
            self.db.upsert_coin(mint=mint, holders=holders)
        if not passed:
            self.db.upsert_coin(mint=mint, status="rejected", kill_reason=reason)
            self.db.add_activity(
                ts, "rejected", mint, "Auditor",
                f"Rejected {symbol}",
                f"Auditor · {reason}",
                "Skipped",
                night,
            )
            self.db.add_decision("auditor", ts, mint, symbol, "rejected", reason or "", night)
        else:
            self.db.add_decision("auditor", ts, mint, symbol, "passed", "8/8 → Executor", night)
            if handoff:
                item = dict(item)
                item["holders"] = holders
                item["audit"] = {"layers": layers, "holders": holders}
                await self.bus.analyst.put(item)
        out = {
            "agent": "auditor",
            "mint": mint,
            "ts": ts,
            "passed": passed,
            "layersChecked": len([l for l in layers if l["pass"] is not None]),
            "layers": layers,
            "holders": holders,
            "reason": reason,
        }
        log.info("verdict mint=%s passed=%s reason=%s", mint, passed, reason)
        self.db.heartbeat("auditor", iso(self.clock.now()))
        return out

    def _cached(self, mint, night, symbol) -> dict:
        rows = self.db.audits(mint, night)
        layers = [{
            "n": r["layer"],
            "name": r["name"],
            "pass": None if r["pass"] is None else bool(r["pass"]),
            "value": r["value_text"],
            "source": r["source"] or "",
        } for r in rows]
        reason = next((l["value"] for l in layers if l["pass"] is False), None)
        coin = self.db.get_coin(mint)
        return {
            "agent": "auditor",
            "mint": mint,
            "ts": rows[-1]["ts"] if rows else iso(self.clock.now()),
            "passed": reason is None and any(l["pass"] for l in layers),
            "layersChecked": len([l for l in layers if l["pass"] is not None]),
            "layers": layers,
            "holders": None if coin is None else coin["holders"],
            "reason": reason,
        }

    async def _layer_dev(self, item, mint_info, settings, finish):
        supply_raw = mint_info.get("supply")
        try:
            supply = int(supply_raw)
        except (TypeError, ValueError):
            supply = 0
        if fields.is_missing(supply_raw) or supply <= 0:
            await finish(3, False, "no supply", "rpc")
            return
        creator = item.get("creator")
        if not creator:
            coin = self.db.get_coin(item["mint"])
            creator = coin["creator"] if coin else None
        if not creator:
            await finish(3, False, "no data: dev", "rpc")
            return
        fetched = await self._retry(lambda: self.sources.rpc(
            "getTokenAccountsByOwner",
            [creator, {"mint": item["mint"]}, {"encoding": "jsonParsed"}],
        ))
        if not isinstance(fetched, dict) or fetched.get("error"):
            await finish(3, False, "no data: dev", "rpc")
            return
        current = fields.creator_balance_raw(fetched)
        if current is None:
            await finish(3, False, "no data: dev", "rpc")
            return
        baseline_raw = item.get("creatorBalance")
        if baseline_raw is None:
            coin = self.db.get_coin(item["mint"])
            baseline_raw = coin["creator_balance_at_migration"] if coin else None
        if baseline_raw is None:
            await finish(3, False, "no data: dev", "rpc")
            return
        try:
            baseline = int(baseline_raw)
        except (TypeError, ValueError):
            await finish(3, False, "no data: dev", "rpc")
            return
        pct = current / supply * 100.0
        if current < baseline:
            await finish(3, False, "dev sold", "rpc", raw=str(current))
            return
        limit = float(settings["auditor.dev_max_pct"])
        if pct >= limit:
            shown = f"{pct:.0f}" if pct >= 10 else f"{pct:.1f}"
            await finish(3, False, f"dev holds {shown}%", "rpc", raw=str(current))
            return
        await finish(3, True, f"{pct:.1f}% · sold 0", "rpc", raw=str(current))

    async def _layer_top10(self, item, mint_info, settings, finish):
        mint = item["mint"]
        pool = item.get("pool")
        fetched = await self._retry(lambda: self.sources.rpc("getTokenLargestAccounts", [mint]))
        if not isinstance(fetched, dict) or fetched.get("error"):
            await finish(4, False, "no data: top 10", "rpc")
            return
        rows = fields.read_largest(fetched)
        addrs = [r["address"] for r in rows if r.get("address")]
        owners = {}
        if addrs:
            multi = await self._retry(lambda: self.sources.rpc(
                "getMultipleAccounts", [addrs, {"encoding": "jsonParsed"}]
            ))
            if isinstance(multi, dict) and not multi.get("error"):
                owners = fields.read_owners(multi, addrs)
        pct = fields.top10_pct(rows, owners, mint_info.get("supply"), pool)
        if pct is None:
            await finish(4, False, "no data: top 10", "rpc")
            return
        limit = float(settings["auditor.top10_max_pct"])
        if pct >= limit:
            shown = f"{pct:.0f}" if pct >= 10 else f"{pct:.1f}"
            await finish(4, False, f"top 10 hold {shown}%", "rpc", raw=f"{pct:.2f}")
            return
        await finish(4, True, fmt_pct(pct) if pct < 10 else f"{pct:.1f}%", "rpc", raw=f"{pct:.2f}")

    async def _layer_liquidity(self, item, settings, finish):
        pool = item.get("pool")
        mint = item["mint"]
        if not pool:
            await finish(5, False, "no data: liquidity", "rugcheck")
            return
        report = await self._retry(lambda: self.sources.rug_report(mint))
        if not isinstance(report, dict):
            await finish(5, False, "no data: liquidity", "rugcheck")
            return
        locked = fields.read_lp_locked_pct(report, pool)
        if locked is None:
            await finish(5, False, "no data: liquidity", "rugcheck")
            return
        need = float(settings["auditor.lp_locked_min_pct"])
        if locked < need:
            await finish(5, False, "liquidity unlocked", "rugcheck", raw=str(locked))
            return
        try:
            raw_dex = await self.sources.dex_pairs([mint])
            pair = fields.assign_pairs(raw_dex, [mint]).get(mint)
        except Exception:
            pair = None
        coin = self.db.get_coin(mint)
        baseline = item.get("liquidityUsd")
        if baseline is None and coin is not None:
            baseline = coin["liq_at_scan"]
        if pair and pair.get("liquidity_usd") is not None and baseline:
            now_liq = pair["liquidity_usd"]
            drop = float(settings["auditor.liq_drop_max_pct"]) / 100.0
            if now_liq < float(baseline) * (1 - drop):
                await finish(5, False, "liquidity pulled", "dexscreener", raw=str(now_liq))
                return
            self.db.add_price(mint, iso(self.clock.now()), pair["price_usd"], pair.get("volume_m5"))
        label = f"{locked:.0f}% locked" if abs(locked - round(locked)) < 0.05 else f"{locked:.1f}% locked"
        await finish(5, True, label, "rugcheck", raw=str(locked))

    async def _layer_age(self, item, settings, finish):
        raw = item.get("migratedAt")
        if not raw:
            coin = self.db.get_coin(item["mint"])
            raw = coin["migrated_at"] if coin else None
        if not raw:
            await finish(6, False, "no data: pool age", "scanner")
            return
        try:
            migrated = parse_iso(raw)
        except Exception:
            await finish(6, False, "no data: pool age", "scanner")
            return
        minutes = (self.clock.now() - migrated).total_seconds() / 60.0
        need = float(settings["auditor.min_pool_age_min"])
        if minutes < need:
            await finish(6, False, "pool too young", "scanner", raw=f"{minutes:.1f}")
            return
        if minutes < 60:
            text = f"{int(minutes)}m"
        elif minutes < 60 * 48:
            text = f"{int(minutes // 60)}h"
        else:
            text = f"{int(minutes // 1440)}d"
        await finish(6, True, text, "scanner", raw=f"{minutes:.1f}")

    async def _layer_bundles(self, mint, settings, finish):
        graph_raw = await self._retry(lambda: self.sources.rug_graph(mint))
        if isinstance(graph_raw, Exception) or graph_raw is None or (
            isinstance(graph_raw, dict) and graph_raw.get("error")
        ):
            await finish(7, False, "no data: bundles", "rugcheck")
            return
        graph = fields.read_insider_graph(graph_raw)
        if graph["missing_links"]:
            await finish(7, False, "no data: bundles", "rugcheck")
            return
        size = fields.largest_holder_component(graph)
        limit = int(settings["auditor.bundle_max"])
        if size >= limit:
            await finish(7, False, f"{size} bundled wallets", "rugcheck", raw=str(size))
            return
        await finish(7, True, "None", "rugcheck", raw=str(size))

    async def _layer_delegations(self, mint, settings, finish):
        if int(self.db.get_setting("desk.layer8_down") or 0):
            await finish(8, False, "no data: delegations", "rpc")
            return None
        limit = int(settings["auditor.delegate_max"])

        async def pull(program):
            return await self.sources.rpc("getProgramAccounts", [
                program,
                {"encoding": "jsonParsed", "filters": [{"memcmp": {"offset": 0, "bytes": mint}}]},
            ])

        token_raw = await self._retry(lambda: pull(TOKEN))
        token22_raw = await self._retry(lambda: pull(TOKEN_2022))
        refused = False
        for raw in (token_raw, token22_raw):
            if isinstance(raw, dict) and fields.rpc_refused(raw):
                refused = True
        if refused or not isinstance(token_raw, dict) or not isinstance(token22_raw, dict):
            if refused:
                self._flag_layer8(str(settings["desk.rpc_url"]))
                await self._announce_layer8()
            await finish(8, False, "no data: delegations", "rpc")
            return None
        if token_raw.get("error") or token22_raw.get("error"):
            await finish(8, False, "no data: delegations", "rpc")
            return None
        rows = fields.read_token_accounts(token_raw) + fields.read_token_accounts(token22_raw)
        seen = set()
        unique = []
        for row in rows:
            key = row.get("address") or id(row)
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
        holders = []
        for row in unique:
            try:
                amount = int(row.get("amount") or 0)
            except (TypeError, ValueError):
                amount = 0
            if amount > 0:
                holders.append(row)
        groups: dict[str, int] = {}
        for row in holders:
            delegate = row.get("delegate")
            if delegate:
                groups[delegate] = groups.get(delegate, 0) + 1
        if groups:
            worst = max(groups, key=groups.get)
            count = groups[worst]
            if count > limit:
                await finish(
                    8, False,
                    f"{count} wallets → {fields.short_addr(worst)}",
                    "rpc",
                    raw=worst,
                )
                self.db.upsert_coin(mint=mint, holders=len(holders))
                return len(holders)
        await finish(8, True, "None", "rpc", raw=str(len(holders)))
        self.db.upsert_coin(mint=mint, holders=len(holders))
        return len(holders)

    async def run(self):
        while True:
            self.db.heartbeat("auditor", iso(self.clock.now()))
            if self._paused():
                await asyncio.sleep(0.5)
                continue
            if not int(self.db.get_setting("auditor.enabled") or 0):
                # Taking coins apart is off: hand the scanner payload straight to Analyst.
                try:
                    item = await asyncio.wait_for(self.bus.auditor.get(), timeout=1.0)
                except TimeoutError:
                    continue
                await self.bus.analyst.put(item)
                continue
            try:
                item = await asyncio.wait_for(self.bus.auditor.get(), timeout=1.0)
            except TimeoutError:
                continue
            try:
                await self.audit(item, handoff=True)
            except Exception as exc:
                log.info("audit crash mint=%s err=%s", item.get("mint"), type(exc).__name__)
