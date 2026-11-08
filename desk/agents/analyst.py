"""Analyst. Build a state, score it, let the gate decide.

rules mode (the default) turns DexScreener numbers into the four noul
scores. No Jev call. jev mode POSTs the same state and reads
answers.{name}.noul. The gate is code either way.

Rules, all clamped to 0..1 and rounded to 4 decimals:
  alive_6h = (h1 buys + h1 sells) / 1000
  organic  = min(m5 buys, m5 sells) / max(m5 buys, m5 sells)
  chase     = max(0, m5 price change) / 100
  flush     = m5 volume / h1 volume
"""

from __future__ import annotations

import asyncio
from decimal import Decimal, ROUND_HALF_UP

from desk import fields
from desk.log import get_logger
from desk.util import iso, parse_iso

log = get_logger("analyst")

JEV_URL = "https://api.typesafe.ai/v1/systemone"
QUESTIONS = {
    "alive_6h": {"type": "noul", "instructions": "This coin is still actively traded 6 hours from now"},
    "organic": {"type": "noul", "instructions": "Most buyers in the last 30 minutes are separate real wallets"},
    "flush": {"type": "noul", "instructions": "Price just flushed down and buyers are absorbing it"},
    "chase": {"type": "noul", "instructions": "This entry is chasing a green candle that already ran"},
}


def analyst_name(mode: str) -> str:
    if (mode or "rules") == "rules":
        return "Analyst (rules)"
    return "Analyst"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def rules_scores(dex: dict) -> dict:
    buys_h1 = dex.get("buys_h1") or 0
    sells_h1 = dex.get("sells_h1") or 0
    buys_m5 = dex.get("buys_m5") or 0
    sells_m5 = dex.get("sells_m5") or 0
    alive = _clamp((buys_h1 + sells_h1) / 1000.0)
    peak = max(buys_m5, sells_m5)
    organic = 0.0 if peak == 0 else _clamp(min(buys_m5, sells_m5) / peak)
    change = dex.get("change_m5") or 0
    chase = _clamp(max(0.0, float(change)) / 100.0)
    v5 = dex.get("volume_m5") or 0
    v1 = dex.get("volume_h1") or 0
    if v1 <= 0:
        flush = 1.0 if v5 > 0 else 0.0
    else:
        flush = _clamp(float(v5) / float(v1))
    return {
        "alive_6h": round(alive, 4),
        "organic": round(organic, 4),
        "flush": round(flush, 4),
        "chase": round(chase, 4),
    }


def _noul_text(value) -> str:
    return f"{float(value):.4f}".rstrip("0").rstrip(".")


def score_of(answers: dict) -> int:
    parts = [Decimal(str(answers[k])) for k in ("alive_6h", "organic", "flush")]
    chase = Decimal(str(answers["chase"]))
    val = (sum(parts, Decimal(0)) + (Decimal(1) - chase)) / Decimal(4) * Decimal(100)
    return int(val.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def apply_gate(answers: dict, settings: dict) -> dict:
    failed = []
    if float(answers["alive_6h"]) < float(settings["analyst.alive_min"]):
        failed.append(f"alive_6h {_noul_text(answers['alive_6h'])}")
    if float(answers["organic"]) < float(settings["analyst.organic_min"]):
        failed.append(f"organic {_noul_text(answers['organic'])}")
    if float(answers["chase"]) > float(settings["analyst.chase_max"]):
        failed.append(f"chase {_noul_text(answers['chase'])}")
    score = score_of(answers)
    if score < int(settings["analyst.score_min"]):
        failed.append(f"score {score}")
    return {"passed": not failed, "failed": failed, "score": score, "answers": dict(answers)}


def build_state(item: dict, dex: dict, settings: dict) -> str:
    migrated = item.get("migratedAt")
    age = "?"
    if migrated:
        try:
            mins = int((parse_iso(item.get("ts") or migrated) - parse_iso(migrated)).total_seconds() // 60)
            # Age is "now", and ts on the scanner payload is the promotion time.
            age = str(max(0, mins))
        except Exception:
            age = "?"
    audit = item.get("audit") or {}
    layers = {l["n"]: l for l in audit.get("layers") or []}
    dev = layers.get(3, {}).get("value") or "n/a"
    top = item.get("top10Pct")
    top_txt = "n/a" if top is None else f"{float(top):.1f}"
    lp = layers.get(5, {}).get("value") or "n/a"
    bund = layers.get(7, {}).get("value") or "n/a"
    dele = layers.get(8, {}).get("value") or "n/a"
    # Pull a bare percent out of "0.9% · sold 0" when we can.
    dev_pct = dev.split("%")[0] if isinstance(dev, str) and "%" in dev else dev
    sold = "sold 0" if isinstance(dev, str) and "sold 0" in dev else ("sold" if dev == "dev sold" else "sold 0")
    text = (
        f"Coin {item.get('name') or item.get('symbol') or 'coin'} on Solana, graduated {age} min ago. "
        f"Liquidity ${float(dex.get('liquidity_usd') or item.get('liquidityUsd') or 0):.0f}. "
        f"Market cap ${float(dex.get('market_cap') or item.get('marketCap') or 0):.0f}. "
        f"Last 5 min: {dex.get('buys_m5') or 0} buys, {dex.get('sells_m5') or 0} sells, "
        f"volume ${float(dex.get('volume_m5') or 0):.0f}, price {dex.get('change_m5') or 0}%. "
        f"Last 1h: {dex.get('buys_h1') or 0} buys, {dex.get('sells_h1') or 0} sells, "
        f"volume ${float(dex.get('volume_h1') or 0):.0f}, price {dex.get('change_h1') or 0}%. "
        f"Top 10 hold {top_txt}%. Dev holds {dev_pct}%, {sold}. LP {lp}. Bundled {bund}. Delegations {dele}."
    )
    limit = int(settings["analyst.state_max_chars"])
    return text[:limit]


class Analyst:
    def __init__(self, db, bus, sources, clock, notifier=None, http=None):
        self.db = db
        self.bus = bus
        self.sources = sources
        self.clock = clock
        self.notifier = notifier
        self.http = http

    def _night(self) -> str:
        raw = self.db.settings_map().get("desk.night_started") or iso(self.clock.now())
        return str(raw)[:10]

    def _paused(self) -> bool:
        return bool(int(self.db.get_setting("paused.analyst") or 0))

    async def _fresh(self, mint: str) -> dict | None:
        raw = await self.sources.dex_pairs([mint])
        return fields.assign_pairs(raw, [mint]).get(mint)

    async def call_jev(self, state: str, key: str) -> tuple[dict, str | None, dict]:
        import httpx

        client = self.http or httpx.AsyncClient(timeout=30)
        owns = self.http is None
        try:
            response = await client.post(
                JEV_URL,
                headers={"Authorization": f"Bearer {key}"},
                json={"model": "jev-latest", "state": state, "questions": QUESTIONS},
            )
            response.raise_for_status()
            data = response.json()
        finally:
            if owns:
                await client.aclose()
        answers = {}
        for name in ("alive_6h", "organic", "flush", "chase"):
            answers[name] = float(data["answers"][name]["noul"])
        usage = data.get("usage") or {}
        return answers, data.get("model"), usage

    async def judge(self, item: dict) -> dict:
        settings = self.db.settings()
        mode = str(settings["analyst.mode"] or "rules")
        name = analyst_name(mode)
        mint = item["mint"]
        symbol = item.get("symbol") or mint[:4]
        night = self._night()
        ts = iso(self.clock.now())
        try:
            dex = await self._fresh(mint)
        except Exception as exc:
            log.info("dex miss mint=%s err=%s", mint, type(exc).__name__)
            dex = None
        if dex and dex.get("price_usd") is not None:
            self.db.add_price(mint, ts, dex["price_usd"], dex.get("volume_m5"))
        if not dex:
            gate = {"passed": False, "failed": ["no data: dex"], "score": 0,
                    "answers": {"alive_6h": 0, "organic": 0, "flush": 0, "chase": 1}}
            tokens = 0
            cost = 0
            model = None
            state = ""
        else:
            state = build_state({**item, "ts": ts}, dex, settings)
            tokens = 0
            cost = 0.0
            model = None
            if mode == "jev":
                key = None
                # The key is injected by main via self.jev_key so it is not read from the db.
                key = getattr(self, "jev_key", None)
                if not key:
                    gate = {"passed": False, "failed": ["no data: jev"], "score": 0,
                            "answers": {"alive_6h": 0, "organic": 0, "flush": 0, "chase": 1}}
                else:
                    try:
                        answers, model, usage = await self.call_jev(state, key)
                        tokens = int(usage.get("total_tokens") or usage.get("input_tokens") or 0)
                        cost = float(usage.get("cost") or 0)
                        gate = apply_gate(answers, settings)
                    except Exception as exc:
                        log.info("jev miss mint=%s err=%s", mint, type(exc).__name__)
                        gate = {"passed": False, "failed": ["no data: jev"], "score": 0,
                                "answers": {"alive_6h": 0, "organic": 0, "flush": 0, "chase": 1}}
            else:
                answers = rules_scores(dex)
                gate = apply_gate(answers, settings)
        self.db.add_verdict(
            mint=mint, ts=ts,
            alive_6h=gate["answers"].get("alive_6h"),
            organic=gate["answers"].get("organic"),
            flush=gate["answers"].get("flush"),
            chase=gate["answers"].get("chase"),
            score=gate["score"],
            **{"pass": gate["passed"]},
            reason="; ".join(gate["failed"]),
            input_tokens=tokens,
            model=model,
            cost=cost,
            mode=mode,
            night=night,
        )
        if gate["passed"]:
            self.db.add_decision("analyst", ts, mint, symbol, "passed", f"score {gate['score']}", night)
            payload = dict(item)
            payload.update({
                "score": gate["score"],
                "answers": gate["answers"],
                "analystPrice": None if not dex else dex.get("price_usd"),
                "liquidityUsd": None if not dex else dex.get("liquidity_usd"),
                "ts": ts,
            })
            await self.bus.executor.put(payload)
            log.info("pass mint=%s score=%s mode=%s", mint, gate["score"], mode)
        else:
            failed = gate["failed"][0] if gate["failed"] else "reject"
            self.db.add_activity(
                ts, "rejected", mint, name,
                f"Rejected {symbol}",
                f"{name} · {failed}",
                "Skipped",
                night,
            )
            self.db.add_decision("analyst", ts, mint, symbol, "rejected", failed, night)
            log.info("reject mint=%s failed=%s mode=%s", mint, gate["failed"], mode)
        self.db.heartbeat("analyst", iso(self.clock.now()))
        return {"agent": "analyst", "mint": mint, "name": name, **gate, "state": state}

    async def run(self):
        while True:
            self.db.heartbeat("analyst", iso(self.clock.now()))
            if self._paused():
                await asyncio.sleep(0.5)
                continue
            try:
                item = await asyncio.wait_for(self.bus.analyst.get(), timeout=1.0)
            except TimeoutError:
                continue
            try:
                await self.judge(item)
            except Exception as exc:
                log.info("judge crash %s", type(exc).__name__)
