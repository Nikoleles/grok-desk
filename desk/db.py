"""SQLite in WAL mode. One connection, guarded by a lock."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from desk.config import DEFAULTS, coerce, merged
from desk.util import iso


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS coins (
  mint TEXT PRIMARY KEY,
  name TEXT,
  symbol TEXT,
  pool TEXT,
  pool_label TEXT,
  migrated_at TEXT,
  creator TEXT,
  creator_balance_at_migration TEXT,
  holders INTEGER,
  image TEXT,
  status TEXT,
  kill_reason TEXT,
  night TEXT,
  liq_at_scan REAL,
  price_at_scan REAL,
  top10_pct REAL,
  signature TEXT,
  market_cap REAL,
  volume_h24 REAL,
  liquidity_usd REAL
);

CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY,
  mint TEXT,
  ts TEXT,
  liquidity_usd REAL,
  top10_pct REAL,
  h1_change_pct REAL,
  age_min REAL,
  verdict TEXT,
  reason TEXT
);

CREATE TABLE IF NOT EXISTS audits (
  id INTEGER PRIMARY KEY,
  mint TEXT,
  ts TEXT,
  layer INTEGER,
  name TEXT,
  value_text TEXT,
  pass INTEGER,
  source TEXT,
  night TEXT
);

CREATE TABLE IF NOT EXISTS verdicts (
  id INTEGER PRIMARY KEY,
  mint TEXT,
  ts TEXT,
  alive_6h REAL,
  organic REAL,
  flush REAL,
  chase REAL,
  score INTEGER,
  pass INTEGER,
  reason TEXT,
  input_tokens INTEGER,
  model TEXT,
  cost REAL,
  mode TEXT,
  night TEXT
);

CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY,
  mint TEXT,
  ts TEXT,
  price REAL,
  size_usd REAL,
  stack_pct REAL,
  link TEXT,
  mode TEXT,
  status TEXT,
  reason TEXT,
  score INTEGER,
  night TEXT
);

CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY,
  mint TEXT,
  opened_at TEXT,
  entry_price REAL,
  size_usd REAL,
  peak_price REAL,
  stop_price REAL,
  trail_active INTEGER,
  closed_at TEXT,
  exit_price REAL,
  exit_reason TEXT,
  pnl_usd REAL,
  fees_usd REAL,
  mode TEXT,
  exit_started_at TEXT,
  reminded INTEGER DEFAULT 0,
  no_price_misses INTEGER DEFAULT 0,
  no_price_pushed INTEGER DEFAULT 0,
  night TEXT
);

CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY,
  ts TEXT,
  kind TEXT,
  mint TEXT,
  agent TEXT,
  title TEXT,
  subtitle TEXT,
  value TEXT,
  night TEXT
);

CREATE TABLE IF NOT EXISTS prices (
  id INTEGER PRIMARY KEY,
  mint TEXT,
  ts TEXT,
  price REAL,
  volume_m5 REAL
);

CREATE TABLE IF NOT EXISTS balance (
  id INTEGER PRIMARY KEY,
  ts TEXT,
  value REAL
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS change_log (
  id INTEGER PRIMARY KEY,
  ts TEXT,
  key TEXT,
  old TEXT,
  new TEXT,
  who TEXT
);

CREATE TABLE IF NOT EXISTS skips (
  mint TEXT PRIMARY KEY,
  until TEXT
);

CREATE TABLE IF NOT EXISTS heartbeats (
  agent TEXT PRIMARY KEY,
  ts TEXT
);

CREATE TABLE IF NOT EXISTS push_subs (
  endpoint TEXT PRIMARY KEY,
  p256dh TEXT,
  auth TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY,
  agent TEXT,
  ts TEXT,
  mint TEXT,
  coin TEXT,
  verdict TEXT,
  reason TEXT,
  night TEXT
);

CREATE TABLE IF NOT EXISTS ws_events (
  id INTEGER PRIMARY KEY,
  ts TEXT,
  kind TEXT
);

CREATE INDEX IF NOT EXISTS idx_prices_mint ON prices(mint, ts);
CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity(ts);
CREATE INDEX IF NOT EXISTS idx_audits_mint ON audits(mint, layer);
"""


class DB:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row

    def close(self):
        with self._lock:
            self.conn.close()

    def init(self, now_iso: str | None = None):
        with self._lock:
            self.conn.executescript(SCHEMA)
            for key, value in DEFAULTS.items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES(?,?,?)",
                    (key, str(value), now_iso or ""),
                )
        return self

    def _one(self, sql, args=()):
        with self._lock:
            cur = self.conn.execute(sql, args)
            return cur.fetchone()

    def _all(self, sql, args=()):
        with self._lock:
            cur = self.conn.execute(sql, args)
            return cur.fetchall()

    def _exec(self, sql, args=()):
        with self._lock:
            cur = self.conn.execute(sql, args)
            return cur.lastrowid

    def settings_map(self) -> dict:
        rows = self._all("SELECT key, value FROM settings")
        return {r["key"]: r["value"] for r in rows}

    def settings(self) -> dict:
        return merged(self.settings_map())

    def get_setting(self, key, default=None):
        row = self._one("SELECT value FROM settings WHERE key=?", (key,))
        if row is None:
            if key in DEFAULTS:
                return DEFAULTS[key]
            return default
        if key in DEFAULTS:
            try:
                return coerce(key, row["value"])
            except (TypeError, ValueError):
                return row["value"]
        return row["value"]

    def set_setting(self, key, value, who="system", ts: str | None = None):
        old = self._one("SELECT value FROM settings WHERE key=?", (key,))
        old_v = old["value"] if old else None
        new_v = str(value)
        stamp = ts or ""
        with self._lock:
            self.conn.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, new_v, stamp),
            )
            self.conn.execute(
                "INSERT INTO change_log(ts, key, old, new, who) VALUES(?,?,?,?,?)",
                (stamp, key, old_v, new_v, who),
            )

    def heartbeat(self, agent: str, ts: str):
        self._exec(
            "INSERT INTO heartbeats(agent, ts) VALUES(?,?) "
            "ON CONFLICT(agent) DO UPDATE SET ts=excluded.ts",
            (agent, ts),
        )

    def heartbeats(self) -> dict:
        return {r["agent"]: r["ts"] for r in self._all("SELECT agent, ts FROM heartbeats")}

    def upsert_coin(self, **kw):
        mint = kw["mint"]
        cols = [
            "name", "symbol", "pool", "pool_label", "migrated_at", "creator",
            "creator_balance_at_migration", "holders", "image", "status",
            "kill_reason", "night", "liq_at_scan", "price_at_scan", "top10_pct",
            "signature", "market_cap", "volume_h24", "liquidity_usd",
        ]
        existing = self.get_coin(mint)
        if existing is None:
            values = [kw.get(c) for c in cols]
            self._exec(
                f"INSERT INTO coins(mint, {', '.join(cols)}) VALUES({', '.join('?' for _ in range(len(cols)+1))})",
                [mint, *values],
            )
        else:
            sets = []
            args = []
            for c in cols:
                if c in kw and kw[c] is not None:
                    sets.append(f"{c}=?")
                    args.append(kw[c])
            if sets:
                args.append(mint)
                self._exec(f"UPDATE coins SET {', '.join(sets)} WHERE mint=?", args)

    def get_coin(self, mint: str):
        return self._one("SELECT * FROM coins WHERE mint=?", (mint,))

    def coins(self, night: str | None = None):
        if night:
            return self._all("SELECT * FROM coins WHERE night=? ORDER BY migrated_at", (night,))
        return self._all("SELECT * FROM coins ORDER BY migrated_at")

    def waiting_coins(self, night: str):
        return self._all(
            "SELECT * FROM coins WHERE night=? AND status='waiting' ORDER BY migrated_at",
            (night,),
        )

    def add_price(self, mint, ts, price, volume_m5):
        self._exec(
            "INSERT INTO prices(mint, ts, price, volume_m5) VALUES(?,?,?,?)",
            (mint, ts, price, volume_m5),
        )

    def prices(self, mint: str):
        return self._all(
            "SELECT ts, price, volume_m5 FROM prices WHERE mint=? ORDER BY ts, id",
            (mint,),
        )

    def last_price(self, mint: str):
        return self._one(
            "SELECT ts, price, volume_m5 FROM prices WHERE mint=? ORDER BY ts DESC, id DESC LIMIT 1",
            (mint,),
        )

    def add_balance(self, ts, value):
        self._exec("INSERT INTO balance(ts, value) VALUES(?,?)", (ts, value))

    def balances(self):
        return self._all("SELECT ts, value FROM balance ORDER BY ts, id")

    def add_activity(self, ts, kind, mint, agent, title, subtitle, value, night) -> int:
        return self._exec(
            "INSERT INTO activity(ts, kind, mint, agent, title, subtitle, value, night) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (ts, kind, mint, agent, title, subtitle, value, night),
        )

    def activity(self, before: int | None = None, limit: int = 30, night: str | None = None):
        sql = "SELECT * FROM activity WHERE 1=1"
        args: list = []
        if night:
            sql += " AND night=?"
            args.append(night)
        if before:
            sql += " AND id<?"
            args.append(before)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return self._all(sql, args)

    def add_scan(self, **kw):
        self._exec(
            "INSERT INTO scans(mint, ts, liquidity_usd, top10_pct, h1_change_pct, age_min, verdict, reason) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                kw.get("mint"), kw.get("ts"), kw.get("liquidity_usd"), kw.get("top10_pct"),
                kw.get("h1_change_pct"), kw.get("age_min"), kw.get("verdict"), kw.get("reason"),
            ),
        )

    def clear_audits(self, mint: str, night: str):
        self._exec("DELETE FROM audits WHERE mint=? AND night=?", (mint, night))

    def add_audit(self, mint, ts, layer, name, value_text, passed, source, night):
        flag = None if passed is None else (1 if passed else 0)
        self._exec(
            "INSERT INTO audits(mint, ts, layer, name, value_text, pass, source, night) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (mint, ts, layer, name, value_text, flag, source, night),
        )

    def audits(self, mint: str, night: str | None = None):
        if night:
            return self._all(
                "SELECT * FROM audits WHERE mint=? AND night=? ORDER BY layer, id",
                (mint, night),
            )
        return self._all("SELECT * FROM audits WHERE mint=? ORDER BY layer, id", (mint,))

    def rejected_tonight(self, mint: str, night: str) -> bool:
        row = self._one(
            "SELECT 1 FROM audits WHERE mint=? AND night=? AND pass=0 LIMIT 1",
            (mint, night),
        )
        return row is not None

    def audited_mints(self, night: str):
        return [r["mint"] for r in self._all(
            "SELECT DISTINCT mint FROM audits WHERE night=?", (night,)
        )]

    def add_verdict(self, **kw):
        self._exec(
            "INSERT INTO verdicts(mint, ts, alive_6h, organic, flush, chase, score, pass, reason, "
            "input_tokens, model, cost, mode, night) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                kw["mint"], kw["ts"], kw.get("alive_6h"), kw.get("organic"), kw.get("flush"),
                kw.get("chase"), kw.get("score"), 1 if kw.get("pass") else 0, kw.get("reason"),
                kw.get("input_tokens") or 0, kw.get("model"), kw.get("cost") or 0,
                kw.get("mode"), kw.get("night"),
            ),
        )

    def verdicts(self, night: str | None = None):
        if night:
            return self._all("SELECT * FROM verdicts WHERE night=? ORDER BY id", (night,))
        return self._all("SELECT * FROM verdicts ORDER BY id")

    def latest_verdict(self, mint: str):
        return self._one("SELECT * FROM verdicts WHERE mint=? ORDER BY id DESC LIMIT 1", (mint,))

    def add_signal(self, **kw) -> int:
        return self._exec(
            "INSERT INTO signals(mint, ts, price, size_usd, stack_pct, link, mode, status, reason, score, night) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                kw["mint"], kw["ts"], kw.get("price"), kw.get("size_usd"), kw.get("stack_pct"),
                kw.get("link"), kw.get("mode"), kw.get("status"), kw.get("reason"),
                kw.get("score"), kw.get("night"),
            ),
        )

    def update_signal(self, sid: int, **kw):
        sets, args = [], []
        for k, v in kw.items():
            sets.append(f"{k}=?")
            args.append(v)
        args.append(sid)
        self._exec(f"UPDATE signals SET {', '.join(sets)} WHERE id=?", args)

    def signals(self, night: str | None = None, mint: str | None = None):
        sql = "SELECT * FROM signals WHERE 1=1"
        args: list = []
        if night:
            sql += " AND night=?"
            args.append(night)
        if mint:
            sql += " AND mint=?"
            args.append(mint)
        sql += " ORDER BY id"
        return self._all(sql, args)

    def latest_signal(self, mint: str, night: str | None = None):
        if night:
            return self._one(
                "SELECT * FROM signals WHERE mint=? AND night=? ORDER BY id DESC LIMIT 1",
                (mint, night),
            )
        return self._one("SELECT * FROM signals WHERE mint=? ORDER BY id DESC LIMIT 1", (mint,))

    def add_position(self, **kw) -> int:
        return self._exec(
            "INSERT INTO positions(mint, opened_at, entry_price, size_usd, peak_price, stop_price, "
            "trail_active, closed_at, exit_price, exit_reason, pnl_usd, fees_usd, mode, "
            "exit_started_at, reminded, no_price_misses, no_price_pushed, night) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                kw["mint"], kw["opened_at"], kw["entry_price"], kw["size_usd"],
                kw.get("peak_price", kw["entry_price"]), kw["stop_price"],
                1 if kw.get("trail_active") else 0, kw.get("closed_at"), kw.get("exit_price"),
                kw.get("exit_reason"), kw.get("pnl_usd"), kw.get("fees_usd"), kw.get("mode"),
                kw.get("exit_started_at"), 1 if kw.get("reminded") else 0,
                kw.get("no_price_misses") or 0, 1 if kw.get("no_price_pushed") else 0,
                kw.get("night"),
            ),
        )

    def update_position(self, pid: int, **kw):
        if "trail_active" in kw:
            kw["trail_active"] = 1 if kw["trail_active"] else 0
        if "reminded" in kw:
            kw["reminded"] = 1 if kw["reminded"] else 0
        if "no_price_pushed" in kw:
            kw["no_price_pushed"] = 1 if kw["no_price_pushed"] else 0
        sets, args = [], []
        for k, v in kw.items():
            sets.append(f"{k}=?")
            args.append(v)
        args.append(pid)
        self._exec(f"UPDATE positions SET {', '.join(sets)} WHERE id=?", args)

    def open_positions(self):
        return self._all(
            "SELECT * FROM positions WHERE closed_at IS NULL AND exit_reason IS NULL ORDER BY id"
        )

    def exiting_positions(self):
        return self._all(
            "SELECT * FROM positions WHERE closed_at IS NULL AND exit_reason IS NOT NULL ORDER BY id"
        )

    def positions(self, night: str | None = None):
        if night:
            return self._all("SELECT * FROM positions WHERE night=? ORDER BY id", (night,))
        return self._all("SELECT * FROM positions ORDER BY id")

    def position_for(self, mint: str):
        return self._one(
            "SELECT * FROM positions WHERE mint=? ORDER BY id DESC LIMIT 1",
            (mint,),
        )

    def open_position_for(self, mint: str):
        return self._one(
            "SELECT * FROM positions WHERE mint=? AND closed_at IS NULL ORDER BY id DESC LIMIT 1",
            (mint,),
        )

    def add_decision(self, agent, ts, mint, coin, verdict, reason, night):
        self._exec(
            "INSERT INTO decisions(agent, ts, mint, coin, verdict, reason, night) VALUES(?,?,?,?,?,?,?)",
            (agent, ts, mint, coin, verdict, reason, night),
        )

    def decisions(self, agent: str, limit: int = 20, night: str | None = None):
        if night:
            return self._all(
                "SELECT * FROM decisions WHERE agent=? AND night=? ORDER BY id DESC LIMIT ?",
                (agent, night, limit),
            )
        return self._all(
            "SELECT * FROM decisions WHERE agent=? ORDER BY id DESC LIMIT ?",
            (agent, limit),
        )

    def add_skip(self, mint: str, until: str):
        self._exec(
            "INSERT INTO skips(mint, until) VALUES(?,?) ON CONFLICT(mint) DO UPDATE SET until=excluded.until",
            (mint, until),
        )

    def is_skipped(self, mint: str) -> bool:
        return self._one("SELECT 1 FROM skips WHERE mint=?", (mint,)) is not None

    def clear_skips(self):
        self._exec("DELETE FROM skips")

    def add_ws(self, ts: str, kind: str):
        self._exec("INSERT INTO ws_events(ts, kind) VALUES(?,?)", (ts, kind))

    def ws_reconnects_since(self, ts: str) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM ws_events WHERE kind='reconnect' AND ts>=?", (ts,))
        return int(row["n"]) if row else 0

    def resolve_coins(self, query: str):
        q = (query or "").strip()
        if not q:
            return []
        exact = self._one("SELECT * FROM coins WHERE mint=?", (q,))
        if exact:
            return [exact]
        low = q.lower().lstrip("$")
        rows = self._all("SELECT * FROM coins")
        hits = []
        for row in rows:
            name = (row["name"] or "").lower()
            sym = (row["symbol"] or "").lower()
            if sym == low or name == low or sym == q.lower():
                hits.append(row)
        if hits:
            return hits
        partial = []
        for row in rows:
            name = (row["name"] or "").lower()
            sym = (row["symbol"] or "").lower()
            if low in name or low in sym or low in (row["mint"] or "").lower():
                partial.append(row)
        return partial

    def night(self) -> str:
        started = self.get_setting("desk.night_started", None)
        if isinstance(started, str) and len(started) >= 10 and started[4] == "-":
            return started[:10]
        return ""

    def counts(self, agent: str, night: str) -> int:
        row = self._one(
            "SELECT COUNT(*) AS n FROM decisions WHERE agent=? AND night=?",
            (agent, night),
        )
        return int(row["n"]) if row else 0
