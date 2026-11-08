"""JSON shapes shared by plan A (HTTP) and plan B (static files).

Plan B files carry no token. Plan A rejects a call that lacks APP_TOKEN.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from desk import links
from desk.agents.analyst import analyst_name
from desk.agents.executor import ticket_warning
from desk.agents.risk import position_stop_pct
from desk.config import CATALOG
from desk.portfolio import equity
from desk.util import age_text, cents, fmt_pct, iso, money, parse_iso, pct_signed, price_text, stop_label

RANGES = ("LIVE", "1H", "4H", "1D", "1W", "ALL")
AGENT_COLOR = {
    "scanner": "#64d2ff",
    "auditor": "#64d2ff",
    "analyst": "#ffd60a",
    "executor": "#00d35a",
    "risk": "#ff6b9a",
}


def _night(db, clock) -> str:
    raw = db.settings_map().get("desk.night_started") or iso(clock.now())
    return str(raw)[:10]


def _display(kind: str, value) -> str:
    if kind == "usd":
        return money(float(value))
    if kind == "pct":
        return f"{float(value):g}%"
    if kind == "seconds":
        return f"{value}s"
    if kind == "minutes":
        return f"{value}m"
    if kind == "hours":
        return f"{value}h"
    if kind == "bool":
        return "On" if int(value) else "Off"
    return str(value)


def settings_groups(db) -> list:
    current = db.settings()
    groups = []
    for gid, title, rows in CATALOG:
        out = []
        for key, label, kind, lo, hi in rows:
            value = current.get(key)
            out.append({
                "key": key,
                "label": label,
                "kind": kind,
                "min": lo,
                "max": hi,
                "value": value,
                "display": _display(kind, value),
            })
        groups.append({"id": gid, "title": title, "rows": out})
    return groups


def _decisions(db, agent, night):
    rows = []
    for row in db.decisions(agent, limit=20, night=night):
        mint = row["mint"]
        rows.append({
            "ts": row["ts"],
            "time": (row["ts"] or "")[11:16],
            "coin": row["coin"],
            "verdict": row["verdict"],
            "reason": row["reason"],
            "mint": mint,
            "coinUrl": links.coin_url(mint) if mint else None,
            "strike": row["verdict"] in ("killed", "rejected", "skipped"),
        })
    return rows


def _audit_strips(db, night):
    mints = db.audited_mints(night)
    if not mints:
        return ["idle"] * 8, None
    latest = mints[-1]
    layers = {r["layer"]: r for r in db.audits(latest, night)}
    strips = []
    for n in range(1, 9):
        row = layers.get(n)
        if row is None or row["pass"] is None:
            strips.append("idle")
        elif row["pass"]:
            strips.append("green")
        else:
            strips.append("red")
    return strips, latest


def _agent_cards(db, clock) -> list:
    night = _night(db, clock)
    settings = db.settings()
    coins = db.coins(night)
    scans_seen = len(coins)
    waiting = len(db.waiting_coins(night))
    open_pos = list(db.open_positions())
    strips, _latest = _audit_strips(db, night)
    audits = db.audited_mints(night)
    rejected = 0
    passed = 0
    running = False
    last_reason = None
    for mint in audits:
        layers = db.audits(mint, night)
        reds = [r for r in layers if r["pass"] == 0]
        greens = [r for r in layers if r["pass"] == 1]
        if reds:
            rejected += 1
            last_reason = reds[0]["value_text"]
        elif len(greens) == 8:
            passed += 1
        else:
            running = True
    verdicts = db.verdicts(night)
    score = verdicts[-1]["score"] if verdicts else None
    bought = [s for s in db.signals(night) if s["status"] == "bought"]
    last_sig = db.signals(night)
    last_sig = last_sig[-1] if last_sig else None
    if last_sig and last_sig["status"] == "bought":
        exec_line = "✓ Filled"
        progress = 100
    elif last_sig and last_sig["status"] == "sent":
        exec_line = "Executing..."
        progress = 60
    else:
        exec_line = "Waiting"
        progress = 0
    if bought:
        size_text = f"{money(bought[-1]['size_usd'])} · {bought[-1]['stack_pct']}% of stack"
    else:
        size_text = f"{settings['executor.stack_pct']}% of stack"
    warning = ticket_warning(float(settings["desk.stack"]), settings)
    positions = db.positions(night)
    closed = [p for p in positions if p["closed_at"]]
    last_exit = closed[-1]["exit_reason"] if closed else None
    if open_pos:
        risk_status = "Guarding"
    elif last_exit == "trail":
        risk_status = "Trailed out"
    elif last_exit == "stop":
        risk_status = "Stopped"
    else:
        risk_status = "Clear"
    last_word = None
    if last_exit == "trail":
        last_word = "Trailed out"
    elif last_exit == "stop":
        last_word = "Stopped"
    elif last_exit == "timeout":
        last_word = "Timed out"
    elif last_exit == "pulled":
        last_word = "Pulled"
    stop_pill = f"stop -{stop_label(settings['risk.stop_pct'])}%"
    trail_pill = f"trail {stop_label(settings['risk.trail_pct'])}%"
    scanner_status = "Listening for graduations" if scans_seen == 0 else f"{scans_seen} pools"
    auditor_line = "taking apart..." if running else (
        last_reason if rejected and not passed else ("8/8 → Executor" if passed else "Waiting")
    )
    if running:
        auditor_line = "taking apart..."
    elif passed and (not last_reason or passed >= rejected):
        auditor_line = "8/8 → Executor"
    elif last_reason:
        auditor_line = last_reason
    cards = [
        {
            "id": "scanner",
            "name": "Scanner",
            "color": AGENT_COLOR["scanner"],
            "count": scans_seen,
            "countLabel": f"{scans_seen} pools",
            "status": scanner_status,
            "waiting": waiting,
            "paused": bool(int(settings["paused.scanner"])),
            "pauseLocked": False,
            "line": scanner_status,
            "lines": _decisions(db, "scanner", night),
        },
        {
            "id": "auditor",
            "name": "Auditor",
            "color": AGENT_COLOR["auditor"],
            "count": len(audits),
            "countLabel": f"{len(audits)} taken apart",
            "status": auditor_line,
            "paused": bool(int(settings["paused.auditor"])),
            "pauseLocked": False,
            "line": auditor_line,
            "takenApart": len(audits),
            "rejected": rejected,
            "strips": strips,
            "lines": _decisions(db, "auditor", night),
        },
        {
            "id": "analyst",
            "name": analyst_name(str(settings["analyst.mode"])),
            "color": AGENT_COLOR["analyst"],
            "count": len(verdicts),
            "countLabel": "Scoring",
            "status": "Scoring",
            "paused": bool(int(settings["paused.analyst"])),
            "pauseLocked": False,
            "line": "Scoring",
            "score": score,
            "lines": _decisions(db, "analyst", night),
        },
        {
            "id": "executor",
            "name": "Executor",
            "color": AGENT_COLOR["executor"],
            "count": len(bought),
            "countLabel": f"{len(bought)} trades",
            "status": exec_line,
            "paused": bool(int(settings["paused.executor"])),
            "pauseLocked": False,
            "line": exec_line,
            "sizeText": size_text,
            "progress": progress,
            "warning": warning,
            "lines": _decisions(db, "executor", night),
        },
        {
            "id": "risk",
            "name": "Risk",
            "color": AGENT_COLOR["risk"],
            "count": len(closed),
            "countLabel": risk_status,
            "status": risk_status,
            "paused": bool(int(settings["paused.risk"])),
            "pauseLocked": bool(open_pos),
            "line": risk_status,
            "last": last_word,
            "pills": [
                {"text": stop_pill, "tone": "red"},
                {"text": trail_pill, "tone": "green"},
            ],
            "lines": _decisions(db, "risk", night),
        },
    ]
    return cards


def _activity_row(db, row) -> dict:
    coin = db.get_coin(row["mint"]) if row["mint"] else None
    return {
        "id": row["id"],
        "ts": row["ts"],
        "time": (row["ts"] or "")[11:16],
        "kind": row["kind"],
        "mint": row["mint"],
        "agent": row["agent"],
        "title": row["title"],
        "subtitle": row["subtitle"],
        "value": row["value"],
        "coinUrl": links.coin_url(row["mint"]) if row["mint"] else None,
        "symbol": None if coin is None else coin["symbol"],
    }


def activity_payload(db, before: int | None = None, limit: int = 30, night: str | None = None) -> dict:
    rows = db.activity(before=before, limit=limit, night=night)
    payload = [_activity_row(db, r) for r in rows]
    next_before = payload[-1]["id"] if len(payload) == limit else None
    return {"rows": payload, "nextBefore": next_before}


def _marks(db, rows) -> list:
    marks = []
    for row in rows:
        kind = row["kind"]
        if kind == "bought":
            label = row["title"]
            tone = "buy"
        elif kind == "stopped" and "%" in (row["subtitle"] or ""):
            import re
            found = re.search(r"-(\d+(?:\.\d+)?)%", row["subtitle"])
            label = f"-{found.group(1)}% · {row['value']}" if found else row["value"]
            tone = "down"
        elif kind == "sold":
            label = row["value"].split("·")[0].strip()
            tone = "up"
        else:
            continue
        marks.append({"t": row["ts"], "kind": tone, "label": label, "mint": row["mint"]})
    return marks


def _slice_range(points, marks, now, key):
    windows = {
        "LIVE": timedelta(minutes=30),
        "1H": timedelta(hours=1),
        "4H": timedelta(hours=4),
        "1D": timedelta(days=1),
        "1W": timedelta(days=7),
    }
    if key == "ALL" or key not in windows:
        return points, marks
    start = now - windows[key]
    def keep(ts):
        try:
            return parse_iso(ts) >= start
        except Exception:
            return True
    return [p for p in points if keep(p["t"])], [m for m in marks if keep(m["t"])]


def chart_payload(db, clock) -> dict:
    balances = [{"t": r["ts"], "v": r["value"]} for r in db.balances()]
    night = _night(db, clock)
    activity = list(reversed(db.activity(limit=200, night=night)))
    marks = _marks(db, activity)
    now = clock.now()
    if balances:
        try:
            now = parse_iso(balances[-1]["t"])
        except Exception:
            pass
    start_raw = db.settings_map().get("desk.night_start")
    try:
        start_value = float(start_raw) if start_raw not in (None, "") else (balances[0]["v"] if balances else float(db.get_setting("desk.cash")))
    except (TypeError, ValueError):
        start_value = balances[0]["v"] if balances else 0
    ranges = {}
    for key in RANGES:
        pts, mks = _slice_range(balances, marks, now, key)
        ranges[key] = {"points": pts, "marks": mks, "empty": len(pts) < 2 and not mks}
    return {"startValue": start_value, "ranges": ranges}


def state_payload(db, clock) -> dict:
    settings = db.settings()
    value = equity(db)
    start_raw = db.settings_map().get("desk.night_start")
    try:
        start = float(start_raw) if start_raw not in (None, "") else float(settings["desk.cash"])
    except (TypeError, ValueError):
        start = float(settings["desk.cash"])
    today = 0.0 if start == 0 else (value - start) / start * 100.0
    mult = 0.0 if start == 0 else value / start
    handle = str(settings["desk.fomo_handle"] or "")
    owner = str(db.settings_map().get("desk.owner_chat_id") or "")
    offline = bool(int(settings["desk.offline"] or 0))
    night = _night(db, clock)
    rows = activity_payload(db, limit=5, night=night)["rows"]
    coins = []
    for coin in db.coins(night):
        last = db.last_price(coin["mint"])
        price = None if last is None else last["price"]
        coins.append({
            "mint": coin["mint"],
            "name": coin["name"],
            "symbol": coin["symbol"],
            "price": price,
            "priceText": price_text(price),
            "coinUrl": links.coin_url(coin["mint"]),
        })
    return {
        "balance": cents(value),
        "balanceText": money(value),
        "todayPct": round(today, 2),
        "todayText": f"{today:+.2f}%",
        "multiplier": round(mult, 2),
        "multiplierText": f"{mult:.2f}×",
        "mode": settings["desk.mode"],
        "plan": "B",
        "offline": offline,
        "retryIn": 8,
        "updatedAt": iso(clock.now()),
        "lastUpdateText": None,
        "unread": int(settings["desk.unread"] or 0),
        "nightStart": start,
        "stack": float(settings["desk.stack"]),
        "stackText": money(settings["desk.stack"]),
        "fomoHandle": handle,
        "profileUrl": links.profile_url(handle) if handle else None,
        "signupUrl": links.SIGNUP_URL,
        "telegram": {
            "connected": bool(owner),
            "detail": "Connected" if owner else "Not connected",
        },
        "auditorEnabled": bool(int(settings["auditor.enabled"])),
        "paused": {name: bool(int(settings[f"paused.{name}"])) for name in ("scanner", "auditor", "analyst", "executor", "risk")},
        "settings": settings_groups(db),
        "agents": _agent_cards(db, clock),
        "activity": rows,
        "coins": coins,
        "warning": ticket_warning(float(settings["desk.stack"]), settings),
        "running": sum(1 for name in ("scanner", "auditor", "analyst", "executor", "risk") if not int(settings[f"paused.{name}"])),
    }


def _candles(db, mint: str) -> list:
    rows = db.prices(mint)
    buckets: dict[str, dict] = {}
    order: list[str] = []
    prev = None
    for row in rows:
        minute = row["ts"][:16]
        price = float(row["price"])
        vol = row["volume_m5"]
        delta = 0.0
        if vol is not None and prev is not None:
            delta = max(0.0, float(vol) - float(prev))
        if vol is not None:
            prev = float(vol)
        if minute not in buckets:
            buckets[minute] = {"t": minute, "o": price, "h": price, "l": price, "c": price, "v": 0.0}
            order.append(minute)
        cell = buckets[minute]
        cell["h"] = max(cell["h"], price)
        cell["l"] = min(cell["l"], price)
        cell["c"] = price
        cell["v"] += delta
    return [buckets[k] for k in order]


def _audit_block(db, mint, night, symbol, size_usd):
    layers_raw = db.audits(mint, night)
    if not layers_raw:
        return None
    layers = []
    reds = 0
    greens = 0
    for row in layers_raw:
        flag = None if row["pass"] is None else bool(row["pass"])
        if flag is True:
            greens += 1
        elif flag is False:
            reds += 1
        layers.append({
            "n": row["layer"],
            "name": row["name"],
            "pass": flag,
            "value": row["value_text"],
            "source": row["source"] or "",
        })
    if reds:
        reason = next(l["value"] for l in layers if l["pass"] is False)
        return {
            "status": "rejected",
            "headline": f"Rejected · {reason}",
            "sub": f"Grok Bot won't buy {symbol}",
            "shield": f"{greens}/8",
            "layers": layers,
        }
    if greens == 8:
        paid = money(size_usd) if size_usd else None
        sub = f"Sent to Executor · {paid}" if paid else "Sent to Executor"
        return {
            "status": "cleared",
            "headline": "Cleared · 8/8 parts",
            "sub": sub,
            "shield": "8/8",
            "layers": layers,
        }
    return {
        "status": "running",
        "headline": "Taking apart...",
        "sub": f"{greens}/8",
        "shield": f"{greens}/8",
        "layers": layers,
    }


def coin_payload(db, mint: str, clock) -> dict | None:
    coin = db.get_coin(mint)
    if coin is None:
        return None
    settings = db.settings()
    night = _night(db, clock)
    now = clock.now()
    last = db.last_price(mint)
    price = None if last is None else float(last["price"])
    pos = db.open_position_for(mint)
    closed = None if pos else db.position_for(mint)
    active = pos
    since = None
    since_text = None
    position = None
    exit_price = None
    if active:
        entry = float(active["entry_price"])
        if price:
            since = (price / entry - 1.0) * 100.0
            since_text = f"{'▲' if since >= 0 else '▼'} {abs(since):.1f}% since entry" if abs(since) < 10 else f"{'▲' if since >= 0 else '▼'} {abs(since):.0f}% since entry"
        stop_pct = position_stop_pct(entry, active["stop_price"])
        peak = float(active["peak_price"] or entry)
        trail_pct = float(settings["risk.trail_pct"])
        trail_price = peak * (1.0 - trail_pct / 100.0)
        exit_price = trail_price if active["trail_active"] else float(active["stop_price"])
        opened = parse_iso(active["opened_at"])
        remain = float(settings["risk.timeout_h"]) * 3600 - (now - opened).total_seconds()
        if remain < 0:
            remain = 0
        hours = int(remain // 3600)
        mins = int((remain % 3600) // 60)
        sig = db.latest_signal(mint, night)
        position = {
            "size": active["size_usd"],
            "sizeText": money(active["size_usd"]),
            "entry": entry,
            "stopText": f"-{stop_label(stop_pct)}%",
            "stopPrice": float(active["stop_price"]),
            "stopPct": round(stop_pct, 2),
            "trailText": f"-{stop_label(trail_pct)}% from the peak",
            "trailPrice": trail_price,
            "trailActive": bool(active["trail_active"]),
            "exitPrice": exit_price,
            "timeoutText": f"{hours}h {mins}m",
            "agent": "Executor",
        }
    migrated = coin["migrated_at"]
    age = None
    if migrated:
        try:
            age = age_text(parse_iso(migrated), now)
        except Exception:
            age = None
    holders = coin["holders"]
    sig = db.latest_signal(mint, night)
    size = None if sig is None else sig["size_usd"]
    audit = _audit_block(db, mint, night, coin["symbol"] or mint[:4], size)
    shield = audit["shield"] if audit else None
    symbol = coin["symbol"] or ""
    return {
        "mint": mint,
        "name": coin["name"] or symbol or mint[:4],
        "symbol": symbol,
        "chain": "Solana",
        "ageText": age,
        "subhead": " · ".join(p for p in (symbol, "Solana", age) if p),
        "shield": shield,
        "price": price,
        "priceText": price_text(price),
        "sinceEntry": since,
        "sinceText": since_text,
        "candles": _candles(db, mint),
        "exitPrice": exit_price,
        "marketCap": coin["market_cap"],
        "liquidity": coin["liquidity_usd"] if coin["liquidity_usd"] is not None else coin["liq_at_scan"],
        "volume24h": coin["volume_h24"],
        "holders": holders,
        "holdersText": "n/a" if holders is None else str(holders),
        "marketCapText": "n/a" if coin["market_cap"] is None else money(coin["market_cap"]),
        "liquidityText": "n/a" if (coin["liquidity_usd"] or coin["liq_at_scan"]) is None else money(coin["liquidity_usd"] or coin["liq_at_scan"]),
        "volumeText": "n/a" if coin["volume_h24"] is None else money(coin["volume_h24"]),
        "position": position,
        "coinUrl": links.coin_url(mint),
        "mode": settings["desk.mode"],
        "audit": audit,
        "image": coin["image"],
        "closed": None if not closed or not closed["closed_at"] else {
            "reason": closed["exit_reason"],
            "pnl": closed["pnl_usd"],
        },
    }


def dumps(payload) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


async def poll_chart_prices(db, sources, clock):
    """DexScreener prices for coins the scanner and Risk are not already polling."""
    night = _night(db, clock)
    covered = {c["mint"] for c in db.waiting_coins(night)}
    covered |= {p["mint"] for p in db.open_positions()}
    want = []
    for coin in db.coins(night):
        if coin["mint"] in covered:
            continue
        if coin["status"] in ("passed", "rejected", "killed") or db.latest_signal(coin["mint"], night) or db.audits(coin["mint"], night):
            want.append(coin["mint"])
    if not want:
        return
    from desk import fields
    raw = await sources.dex_pairs(want)
    mapped = fields.assign_pairs(raw, want)
    ts = iso(clock.now())
    for mint, pair in mapped.items():
        if pair.get("price_usd") is None:
            continue
        db.add_price(mint, ts, pair["price_usd"], pair.get("volume_m5"))
        db.upsert_coin(
            mint=mint,
            liquidity_usd=pair.get("liquidity_usd"),
            market_cap=pair.get("market_cap"),
            volume_h24=pair.get("volume_h24"),
        )


def build_app(token: str | None):
    from fastapi import FastAPI, Header, HTTPException, Query

    app = FastAPI(docs_url=None, redoc_url=None)
    holder = {"db": None, "clock": None}

    def bind(db, clock):
        holder["db"] = db
        holder["clock"] = clock

    def guard(authorization: str | None, x_token: str | None):
        if not token:
            raise HTTPException(401, "unauthorized")
        presented = x_token or ""
        if authorization and authorization.lower().startswith("bearer "):
            presented = authorization.split(" ", 1)[1].strip()
        if presented != token:
            raise HTTPException(401, "unauthorized")

    @app.get("/api/state")
    def state(authorization: str | None = Header(default=None), x_app_token: str | None = Header(default=None)):
        guard(authorization, x_app_token)
        return state_payload(holder["db"], holder["clock"])

    @app.get("/api/chart")
    def chart(range: str = Query(default="1D"), authorization: str | None = Header(default=None), x_app_token: str | None = Header(default=None)):
        guard(authorization, x_app_token)
        payload = chart_payload(holder["db"], holder["clock"])
        key = range if range in payload["ranges"] else "1D"
        return {"startValue": payload["startValue"], "range": key, **payload["ranges"][key]}

    @app.get("/api/activity")
    def activity(before: int | None = None, authorization: str | None = Header(default=None), x_app_token: str | None = Header(default=None)):
        guard(authorization, x_app_token)
        return activity_payload(holder["db"], before=before)

    @app.get("/api/coin/{mint}")
    def coin(mint: str, authorization: str | None = Header(default=None), x_app_token: str | None = Header(default=None)):
        guard(authorization, x_app_token)
        payload = coin_payload(holder["db"], mint, holder["clock"])
        if payload is None:
            raise HTTPException(404, "unknown mint")
        return payload

    app.bind = bind
    return app


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(payload), encoding="utf-8")
