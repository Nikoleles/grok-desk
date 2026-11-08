"""Night report as JSON and markdown. No scheduler — the parent turns this into a Page.

Clears db.skips after the report is written. A PAPER night counts toward the
LIVE gate. More than 10 websocket reconnects in the hour is flagged.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from desk.links import SIGNUP_URL, coin_url
from desk.util import iso, money, parse_iso, pct_signed


def _coin_line(db, mint: str) -> str:
    coin = db.get_coin(mint)
    label = mint[:4]
    if coin is not None and (coin["symbol"] or coin["name"]):
        label = coin["symbol"] or coin["name"]
    return f"[{label}]({coin_url(mint)})"


def build_report(db, clock) -> dict:
    raw_night = db.settings_map().get("desk.night_started") or iso(clock.now())
    night = str(raw_night)[:10]
    mode = str(db.get_setting("desk.mode"))
    positions = [p for p in db.positions(night)]
    trades = []
    for pos in positions:
        if not pos["exit_reason"]:
            continue
        entry = float(pos["entry_price"])
        exit_price = float(pos["exit_price"] or entry)
        price_pct = (exit_price / entry - 1.0) * 100.0 if entry else 0
        trades.append({
            "mint": pos["mint"],
            "symbol": (db.get_coin(pos["mint"]) or {}).get("symbol") if False else None,
            "entry": entry,
            "exit": exit_price,
            "pnl": pos["pnl_usd"],
            "pct": price_pct,
            "rule": pos["exit_reason"],
            "stopPct": None,
            "url": coin_url(pos["mint"]),
            "fees": pos["fees_usd"] or 0,
        })
    for trade, pos in zip(trades, [p for p in positions if p["exit_reason"]]):
        coin = db.get_coin(pos["mint"])
        trade["symbol"] = coin["symbol"] if coin else pos["mint"][:4]
    red = [t for t in trades if (t["pnl"] or 0) < 0 or t["rule"] in ("stop", "pulled")]
    green = [t for t in trades if t not in red]
    # A pulled winner should stay green. Recompute: red is pnl < 0.
    red = [t for t in trades if (t["pnl"] or 0) < 0]
    green = [t for t in trades if (t["pnl"] or 0) >= 0]

    kills = defaultdict(int)
    for row in db.decisions("scanner", limit=500, night=night):
        if row["verdict"] == "killed":
            kills[row["reason"] or "unknown"] += 1

    refusals = defaultdict(list)
    for row in db._all(
        "SELECT mint, layer, name, value_text, ts FROM audits WHERE night=? AND pass=0",
        (night,),
    ):
        refusals[f"{row['layer']} {row['name']}"].append({
            "mint": row["mint"],
            "time": row["ts"],
            "value": row["value_text"],
            "url": coin_url(row["mint"]),
        })

    analyst = []
    tokens = 0
    cost = 0.0
    for row in db.verdicts(night):
        tokens += int(row["input_tokens"] or 0)
        cost += float(row["cost"] or 0)
        if not row["pass"]:
            analyst.append({
                "mint": row["mint"],
                "rule": row["reason"],
                "url": coin_url(row["mint"]),
                "mode": row["mode"],
            })
    if str(db.get_setting("analyst.mode")) == "rules":
        tokens = 0
        cost = 0.0

    fees = sum(float(p["fees_usd"] or 0) for p in positions)
    hour_ago = iso(clock.now() - timedelta(hours=1))
    reconnects = db.ws_reconnects_since(hour_ago)
    seen = db.coins(night)
    layer8 = int(db.get_setting("desk.layer8_down") or 0)
    return {
        "night": night,
        "mode": mode,
        "flag": "more than 10 websocket reconnects in the last hour" if reconnects > 10 else None,
        "reconnects": reconnects,
        "layer8Down": bool(layer8),
        "layer8Killed": sum(1 for mint in {r["mint"] for rows in refusals.values() for r in rows} if any(
            item["value"] == "no data: delegations" for item in rows for rows in refusals.values()
        )) if layer8 else 0,
        "coinsSeen": len(seen),
        "coins": [{"mint": c["mint"], "symbol": c["symbol"], "url": coin_url(c["mint"])} for c in seen],
        "red": red,
        "green": green,
        "scannerKills": [{"reason": k, "count": v} for k, v in sorted(kills.items(), key=lambda kv: -kv[1])],
        "auditorRefusals": [
            {"layer": layer, "times": rows} for layer, rows in refusals.items()
        ],
        "analystRejects": analyst,
        "jevTokens": tokens,
        "jevCost": cost,
        "fees": fees,
        "signupUrl": SIGNUP_URL,
    }


def render_markdown(data: dict) -> str:
    lines = [f"# Morning page · {data['night']}", ""]
    if data.get("layer8Down"):
        lines.append(f"Layer 8 is down. Every coin is red until rpc_url changes. Killed {data.get('layer8Killed', 0)}.")
        lines.append("")
    if data.get("flag"):
        lines.append(f"Flag: {data['flag']} ({data['reconnects']}).")
        lines.append("")
    lines.append("## Red")
    if not data["red"]:
        lines.append("None.")
    for trade in data["red"]:
        lines.append(
            f"- {_coin_line_from(trade)} entry {trade['entry']} exit {trade['exit']} "
            f"{money(trade['pnl'] or 0, signed=True)} {pct_signed(trade['pct'])} · {trade['rule']}"
        )
    lines.append("")
    lines.append("## Green")
    if not data["green"]:
        lines.append("None.")
    for trade in data["green"]:
        lines.append(
            f"- {_coin_line_from(trade)} entry {trade['entry']} exit {trade['exit']} "
            f"{money(trade['pnl'] or 0, signed=True)} {pct_signed(trade['pct'])} · {trade['rule']}"
        )
    lines.append("")
    lines.append(f"## Coins seen · {data['coinsSeen']}")
    for coin in data["coins"]:
        label = coin["symbol"] or coin["mint"][:4]
        lines.append(f"- [{label}]({coin['url']})")
    lines.append("")
    lines.append("## Scanner kills")
    if not data["scannerKills"]:
        lines.append("None.")
    for row in data["scannerKills"]:
        lines.append(f"- {row['count']} · {row['reason']}")
    lines.append("")
    lines.append("## Auditor refusals")
    if not data["auditorRefusals"]:
        lines.append("None.")
    for block in data["auditorRefusals"]:
        lines.append(f"### {block['layer']}")
        for item in block["times"]:
            lines.append(f"- {item['time']} [{item['mint'][:4]}]({item['url']}) · {item['value']}")
    lines.append("")
    lines.append("## Analyst rejects")
    if not data["analystRejects"]:
        lines.append("None.")
    for row in data["analystRejects"]:
        who = "Analyst (rules)" if row.get("mode") == "rules" else "Analyst"
        lines.append(f"- [{row['mint'][:4]}]({row['url']}) · {who} · {row['rule']}")
    lines.append("")
    lines.append(f"Jev tokens {data['jevTokens']} · cost {money(data['jevCost'])}")
    lines.append(f"Fees paid {money(data['fees'])}")
    lines.append("")
    lines.append(f"New to FOMO? {data['signupUrl']}")
    lines.append("")
    return "\n".join(lines)


def _coin_line_from(trade: dict) -> str:
    label = trade.get("symbol") or trade["mint"][:4]
    return f"[{label}]({trade['url']})"


def write_report(db, clock, directory: str | Path = "reports") -> dict:
    data = build_report(db, clock)
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    json_path = path / "night.json"
    md_path = path / "night.md"
    import json
    json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(data), encoding="utf-8")
    db.clear_skips()
    if str(db.get_setting("desk.mode")) == "PAPER":
        nights = int(db.get_setting("desk.paper_nights") or 0)
        db.set_setting("desk.paper_nights", nights + 1, who="report", ts=iso(clock.now()))
    return {"json": str(json_path), "markdown": str(md_path), "data": data}


def main():
    import os
    import sys

    from desk.config import Config
    from desk.db import DB
    from desk.util import Clock

    cfg = Config()
    if not Path(cfg.db_path).exists():
        print("no database yet", file=sys.stderr)
        sys.exit(1)
    db = DB(cfg.db_path)
    paths = write_report(db, Clock(), os.environ.get("DESK_REPORTS", "reports"))
    print(paths["markdown"])


if __name__ == "__main__":
    main()
