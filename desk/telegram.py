"""Telegram long polling. Owner only. Buttons carry the exact links.py URL."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

import httpx

from desk.config import ALIASES
from desk.links import SIGNUP_URL, coin_url
from desk.log import get_logger, redact
from desk.portfolio import equity
from desk.util import money, stop_label

log = get_logger("telegram")

HELP = (
    "buy X — resend tonight's push, or audit the mint now\n"
    "sell X — sell link for an open position\n"
    "why X — which agent stopped it\n"
    "pause Scanner — pause an agent\n"
    "resume Scanner — resume an agent\n"
    "set stop to -8% — change a setting\n"
    "stack 1240 — set the LIVE stack\n"
    "status — mode, balance, open positions\n"
    "paper — switch to PAPER\n"
    "live — switch to LIVE (then type LIVE)\n"
    "invite — FOMO signup link\n"
    "report — last morning page\n"
    "test — send a test push"
)

AGENTS = ("scanner", "auditor", "analyst", "executor", "risk")


@dataclass
class Push:
    text: str
    url: str
    url_label: str
    callbacks: list = field(default_factory=list)
    paper: bool = False


def render_message(push: Push) -> dict:
    text = push.text
    if push.paper and not text.startswith("PAPER · "):
        text = "PAPER · " + text
    row = [{"text": push.url_label, "url": push.url}]
    for label, data in push.callbacks:
        row.append({"text": label, "callback_data": data})
    return {
        "text": text,
        "reply_markup": {"inline_keyboard": [row]},
        "disable_web_page_preview": True,
    }


def parse_command(text: str):
    raw = (text or "").strip()
    if not raw:
        return ("help",)
    body = raw[1:] if raw.startswith("/") else raw
    body = re.sub(r"^(\S+)@\S+", r"\1", body).strip()
    if body == "LIVE":
        return ("live_confirm",)
    low = body.lower()
    if low in ("start",):
        return ("start",)
    if low in ("help", "?"):
        return ("help",)
    if low == "status":
        return ("status",)
    if low == "paper":
        return ("paper",)
    if low == "live":
        return ("live",)
    if low == "invite":
        return ("invite",)
    if low == "report":
        return ("report",)
    if low == "test":
        return ("test",)
    match = re.match(r"(?i)buy\s+(.+)$", body)
    if match:
        return ("buy", match.group(1).strip())
    match = re.match(r"(?i)sell\s+(.+)$", body)
    if match:
        return ("sell", match.group(1).strip())
    match = re.match(r"(?i)why\s+(.+)$", body)
    if match:
        return ("why", match.group(1).strip())
    match = re.match(r"(?i)resume\s+(.+)$", body)
    if match:
        return ("resume", match.group(1).strip())
    match = re.match(r"(?i)pause\s+(.+)$", body)
    if match:
        return ("pause", match.group(1).strip())
    match = re.match(r"(?i)stack\s+\$?([\d.]+)$", body)
    if match:
        return ("stack", float(match.group(1)))
    match = re.match(r"(?i)set\s+stop\s+to\s+-?([\d.]+)\s*%?$", body)
    if match:
        return ("set", "risk.stop_pct", float(match.group(1)))
    match = re.match(r"(?i)set\s+(.+?)\s+to\s+(.+)$", body)
    if match:
        alias = match.group(1).strip().lower()
        key = ALIASES.get(alias)
        if not key:
            return ("help",)
        return ("set", key, _parse_value(key, match.group(2).strip()))
    return ("help",)


def _parse_value(key: str, raw: str):
    raw = raw.strip().rstrip("%").lstrip("$")
    if key == "executor.below_min":
        return raw.lower()
    if key in ("analyst.mode", "desk.fomo_handle", "desk.rpc_url", "desk.mode"):
        return raw
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def _agent_name(text: str) -> str | None:
    name = text.strip().lower()
    if name in AGENTS:
        return name
    return None


class RecordingNotifier:
    def __init__(self):
        self.pushes: list[Push] = []
        self.replies: list[tuple] = []

    async def push(self, push: Push, chat_id=None):
        self.pushes.append(push)
        return True

    async def reply(self, chat_id, text, markup=None):
        self.replies.append((text, markup))
        return True


class DeskSession:
    """Command side of the desk. Transport-agnostic so tests don't need Telegram."""

    def __init__(self, db, clock, notifier, auditor=None, executor=None, risk=None):
        self.db = db
        self.clock = clock
        self.notifier = notifier
        self.auditor = auditor
        self.executor = executor
        self.risk = risk
        self.pending_live = False

    def _owner(self):
        return str(self.db.settings_map().get("desk.owner_chat_id") or "")

    def _night(self) -> str:
        raw = self.db.settings_map().get("desk.night_started") or ""
        return str(raw)[:10]

    async def handle_text(self, chat_id, text: str):
        from desk.util import iso
        cmd = parse_command(text)
        owner = self._owner()
        if not owner:
            if cmd[0] == "start":
                self.db.set_setting("desk.owner_chat_id", str(chat_id), who="telegram", ts=iso(self.clock.now()))
                await self.notifier.reply(chat_id, "You're the owner. The desk is in PAPER.")
            return
        if str(chat_id) != owner:
            return
        name = cmd[0]
        if name == "start":
            await self.notifier.reply(chat_id, "Desk is on. " + HELP.split("\n")[0])
        elif name == "help":
            await self.notifier.reply(chat_id, HELP)
        elif name == "status":
            await self._status(chat_id)
        elif name == "paper":
            self.pending_live = False
            self.db.set_setting("desk.mode", "PAPER", who="telegram", ts=iso(self.clock.now()))
            await self.notifier.reply(chat_id, "PAPER")
        elif name == "live":
            self.pending_live = True
            await self.notifier.reply(chat_id, "Type LIVE to confirm. LIVE buys only when you tap Buy on FOMO.")
        elif name == "live_confirm":
            await self._confirm_live(chat_id)
        elif name == "invite":
            await self.notifier.reply(chat_id, SIGNUP_URL)
        elif name == "report":
            await self._report(chat_id)
        elif name == "test":
            await self._test(chat_id)
        elif name == "stack":
            old = float(self.db.get_setting("desk.stack"))
            self.db.set_setting("desk.stack", cmd[1], who="telegram", ts=iso(self.clock.now()))
            await self.notifier.reply(chat_id, f"stack {money(old)} -> {money(cmd[1])}")
        elif name == "set":
            await self._set(chat_id, cmd[1], cmd[2])
        elif name == "pause":
            await self._pause(chat_id, cmd[1], True)
        elif name == "resume":
            await self._pause(chat_id, cmd[1], False)
        elif name == "buy":
            await self._buy(chat_id, cmd[1])
        elif name == "sell":
            await self._sell(chat_id, cmd[1])
        elif name == "why":
            await self._why(chat_id, cmd[1])
        else:
            await self.notifier.reply(chat_id, HELP)

    async def handle_callback(self, chat_id, data: str):
        if str(chat_id) != self._owner():
            return
        kind, _, mint = (data or "").partition(":")
        if kind == "bought" and self.executor:
            await self.executor.confirm_bought(mint)
            await self.notifier.reply(chat_id, "Filled.")
        elif kind == "skip" and self.executor:
            await self.executor.confirm_skip(mint)
            await self.notifier.reply(chat_id, "Skipped until the morning report.")
        elif kind == "sold" and self.risk:
            result = await self.risk.confirm_sold(mint)
            await self.notifier.reply(chat_id, "Closed." if result == "closed" else result)

    async def _confirm_live(self, chat_id):
        from desk.util import iso
        if not self.pending_live:
            await self.notifier.reply(chat_id, "Type live first, then LIVE.")
            return
        self.pending_live = False
        nights = int(self.db.get_setting("desk.paper_nights") or 0)
        if nights < 1:
            await self.notifier.reply(chat_id, "Need 1 full PAPER night before LIVE. Run stays PAPER.")
            return
        self.db.set_setting("desk.mode", "LIVE", who="telegram", ts=iso(self.clock.now()))
        await self.notifier.reply(chat_id, "LIVE. Buys happen only when you tap Buy on FOMO.")

    async def _set(self, chat_id, key, value):
        from desk.agents.risk import apply_stop_pct
        from desk.util import iso
        ts = iso(self.clock.now())
        if key == "risk.stop_pct":
            old, new = apply_stop_pct(self.db, float(value), ts, who="telegram")
            await self.notifier.reply(
                chat_id, f"stop -{stop_label(old)}% -> -{stop_label(new)}%"
            )
            return
        old = self.db.get_setting(key)
        self.db.set_setting(key, value, who="telegram", ts=ts)
        await self.notifier.reply(chat_id, f"{key} {old} -> {value}")

    async def _pause(self, chat_id, text, pause: bool):
        from desk.util import iso
        agent = _agent_name(text)
        if not agent:
            await self.notifier.reply(chat_id, "Name Scanner, Auditor, Analyst, Executor, or Risk.")
            return
        if agent == "risk" and pause and (self.db.open_positions() or self.db.exiting_positions()):
            await self.notifier.reply(chat_id, "Risk stays on while a position is open.")
            return
        self.db.set_setting(f"paused.{agent}", 1 if pause else 0, who="telegram", ts=iso(self.clock.now()))
        title = agent.capitalize()
        await self.notifier.reply(chat_id, f"{title} {'paused' if pause else 'resumed'}.")

    def _one_coin(self, query: str):
        hits = self.db.resolve_coins(query)
        if not hits:
            return None, f"Send the mint. I don't have a coin named {query}."
        if len(hits) > 1:
            lines = [f"{h['symbol'] or h['mint'][:4]} {coin_url(h['mint'])}" for h in hits]
            return None, "Which coin?\n" + "\n".join(lines)
        return hits[0], None

    async def _buy(self, chat_id, query: str):
        coin, err = self._one_coin(query)
        if err:
            await self.notifier.reply(chat_id, err)
            return
        mint = coin["mint"]
        sig = self.db.latest_signal(mint, self._night())
        paper = self.db.get_setting("desk.mode") != "LIVE"
        if sig and sig["status"] in ("sent", "bought", "missed"):
            symbol = coin["symbol"] or mint[:4]
            text = f"Buy {symbol} · ${float(sig['size_usd']):.2f} · {sig['stack_pct']}% of stack"
            if paper:
                text = "PAPER · " + text
            await self.notifier.push(Push(
                text=text, url=sig["link"], url_label="Buy on FOMO",
                callbacks=[("Bought", f"bought:{mint}"), ("Skip", f"skip:{mint}")],
                paper=paper,
            ))
            return
        if self.auditor is None:
            await self.notifier.reply(chat_id, f"Not sized, not on the desk.\n{coin_url(mint)}")
            return
        item = {
            "mint": mint,
            "symbol": coin["symbol"],
            "name": coin["name"],
            "pool": coin["pool"],
            "migratedAt": coin["migrated_at"],
            "creator": coin["creator"],
            "creatorBalance": coin["creator_balance_at_migration"],
            "liquidityUsd": coin["liq_at_scan"],
        }
        result = await self.auditor.audit(item, handoff=False)
        lines = []
        for layer in result["layers"]:
            flag = "pass" if layer["pass"] is True else ("red" if layer["pass"] is False else "—")
            lines.append(f"{layer['n']} {layer['name']}: {layer['value']} ({flag})")
        lines.append("not sized, not on the desk")
        lines.append(coin_url(mint))
        await self.notifier.reply(chat_id, "\n".join(lines))
        await self.notifier.push(Push(
            text="not sized, not on the desk",
            url=coin_url(mint),
            url_label="Buy on FOMO",
            callbacks=[],
            paper=paper,
        ))

    async def _sell(self, chat_id, query: str):
        coin, err = self._one_coin(query)
        if err:
            await self.notifier.reply(chat_id, err)
            return
        pos = self.db.open_position_for(coin["mint"])
        symbol = coin["symbol"] or coin["mint"][:4]
        url = coin_url(coin["mint"])
        if pos is None:
            await self.notifier.reply(chat_id, f"No open position on {symbol}\n{url}")
            return
        paper = (pos["mode"] or self.db.get_setting("desk.mode")) != "LIVE"
        text = f"Sell {symbol}"
        if paper:
            text = "PAPER · " + text
        await self.notifier.push(Push(
            text=text, url=url, url_label="Sell on FOMO",
            callbacks=[("Sold", f"sold:{coin['mint']}")],
            paper=paper,
        ))

    async def _why(self, chat_id, query: str):
        coin, err = self._one_coin(query)
        if err:
            await self.notifier.reply(chat_id, err)
            return
        mint = coin["mint"]
        symbol = coin["symbol"] or mint[:4]
        url = coin_url(mint)
        pos = self.db.position_for(mint)
        if pos and pos["exit_reason"]:
            from desk.agents.risk import position_stop_pct
            pct = position_stop_pct(pos["entry_price"], pos["stop_price"])
            when = pos["closed_at"] or pos["exit_started_at"] or pos["opened_at"]
            await self.notifier.reply(
                chat_id,
                f"Risk stopped {symbol} · {pos['exit_reason']} · stop -{stop_label(pct)}% · {when}\n{url}",
            )
            return
        audits = self.db.audits(mint, self._night())
        red = next((a for a in audits if a["pass"] == 0), None)
        if red:
            await self.notifier.reply(
                chat_id,
                f"Auditor stopped {symbol} at layer {red['layer']} · {red['value_text']} · {red['ts']}\n{url}",
            )
            return
        verdict = self.db.latest_verdict(mint)
        if verdict and not verdict["pass"]:
            who = "Analyst (rules)" if verdict["mode"] == "rules" else "Analyst"
            await self.notifier.reply(
                chat_id, f"{who} stopped {symbol} · {verdict['reason']} · {verdict['ts']}\n{url}"
            )
            return
        if coin["status"] == "killed":
            await self.notifier.reply(
                chat_id, f"Scanner stopped {symbol} · {coin['kill_reason']}\n{url}"
            )
            return
        await self.notifier.reply(chat_id, f"Nothing has stopped {symbol} yet.\n{url}")

    async def _status(self, chat_id):
        mode = self.db.get_setting("desk.mode")
        lines = [str(mode), f"Balance {money(equity(self.db))}"]
        await self.notifier.reply(chat_id, "\n".join(lines))
        for pos in self.db.open_positions():
            coin = self.db.get_coin(pos["mint"])
            symbol = coin["symbol"] if coin and coin["symbol"] else pos["mint"][:4]
            from desk.agents.risk import position_stop_pct
            pct = position_stop_pct(pos["entry_price"], pos["stop_price"])
            paper = (pos["mode"] or mode) != "LIVE"
            text = f"{symbol} · {money(pos['size_usd'])} · stop -{stop_label(pct)}%"
            if paper:
                text = "PAPER · " + text
            await self.notifier.push(Push(
                text=text,
                url=coin_url(pos["mint"]),
                url_label="Sell on FOMO",
                callbacks=[("Sold", f"sold:{pos['mint']}")],
                paper=paper,
            ))

    async def _test(self, chat_id):
        paper = self.db.get_setting("desk.mode") != "LIVE"
        text = "Test · Grok Bot is connected"
        if paper:
            text = "PAPER · " + text
        await self.notifier.push(Push(
            text=text, url=SIGNUP_URL, url_label="Open FOMO", callbacks=[], paper=paper,
        ))

    async def _report(self, chat_id):
        from desk.report import write_report
        paths = write_report(self.db, self.clock)
        await self.notifier.reply(chat_id, f"Morning page written to {paths['markdown']}\nNew to FOMO? {SIGNUP_URL}")


class Telegram:
    def __init__(self, token: str | None, session: DeskSession, client: httpx.AsyncClient | None = None):
        self.token = token
        self.session = session
        self.client = client or httpx.AsyncClient(timeout=40)
        self._owns = client is None

    async def push(self, push: Push, chat_id=None):
        target = chat_id or self.session._owner()
        if not self.token or not target:
            return False
        payload = render_message(push)
        payload["chat_id"] = target
        return await self._post("sendMessage", payload)

    async def reply(self, chat_id, text, markup=None):
        if not self.token:
            return False
        payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if markup:
            payload["reply_markup"] = markup
        return await self._post("sendMessage", payload)

    async def _post(self, method: str, payload: dict) -> bool:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        try:
            response = await self.client.post(url, json=payload)
            data = response.json()
            return bool(data.get("ok"))
        except Exception as exc:
            log.info("telegram %s failed %s", method, redact(str(type(exc).__name__)))
            return False

    async def run(self):
        if not self.token:
            log.info("Telegram off (TG_TOKEN unset)")
            return
        # The session notifier is this object when we are the transport.
        offset = 0
        while True:
            try:
                url = f"https://api.telegram.org/bot{self.token}/getUpdates"
                response = await self.client.post(url, json={"offset": offset, "timeout": 25})
                data = response.json()
                for upd in data.get("result") or []:
                    offset = int(upd["update_id"]) + 1
                    await self._dispatch(upd)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.info("telegram poll error")
                await asyncio.sleep(2)

    async def _dispatch(self, upd: dict):
        if "message" in upd:
            msg = upd["message"]
            chat = (msg.get("chat") or {}).get("id")
            text = msg.get("text") or ""
            if chat is not None and text:
                await self.session.handle_text(chat, text)
        elif "callback_query" in upd:
            cb = upd["callback_query"]
            chat = ((cb.get("message") or {}).get("chat") or {}).get("id")
            if self.token:
                await self._post("answerCallbackQuery", {"callback_query_id": cb.get("id")})
            if chat is not None:
                await self.session.handle_callback(chat, cb.get("data") or "")
