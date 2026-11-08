"""One asyncio process: agents, Telegram long polling, plan B JSON writer."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from datetime import timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from desk.agents.analyst import Analyst
from desk.agents.auditor import Auditor
from desk.agents.executor import Executor
from desk.agents.risk import Risk
from desk.agents.scanner import Scanner
from desk.bus import Bus
from desk.config import Config
from desk.db import DB
from desk.log import get_logger, register_secret, scrub_env_name, setup
from desk.portfolio import equity
from desk.publish import publish_tree, write_snapshot
from desk.sources import HttpSources
from desk.telegram import DeskSession, Telegram
from desk.util import Clock, iso

log = get_logger("main")


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return


def _serve(directory: Path, host: str, port: int):
    directory.mkdir(parents=True, exist_ok=True)

    class Handler(_Quiet):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        log.info("static server not bound: %s", type(exc).__name__)
        return None
    thread = threading.Thread(target=httpd.serve_forever, name="desk-static", daemon=True)
    thread.start()
    return httpd


def _boot_db(db: DB, clock: Clock, preview: bool):
    now = iso(clock.now())
    db.init(now)
    if not db.settings_map().get("desk.night_started"):
        db.set_setting("desk.night_started", now, who="system", ts=now)
        db.set_setting("desk.night_start", db.get_setting("desk.cash"), who="system", ts=now)
    if not db.balances():
        db.add_balance(now, equity(db))
    if preview:
        from desk.seed_preview import seed
        seed(db, clock)


async def _balance_loop(db, clock):
    while True:
        await asyncio.sleep(60)
        try:
            db.add_balance(iso(clock.now()), equity(db))
        except Exception as exc:
            log.info("balance %s", type(exc).__name__)


async def _writer(db, cfg, clock, sources):
    from desk.api import poll_chart_prices
    last_publish = clock.now() - timedelta(days=1)
    while True:
        try:
            if sources is not None and not cfg.preview:
                try:
                    await poll_chart_prices(db, sources, clock)
                except Exception as exc:
                    log.info("chart prices %s", type(exc).__name__)
            write_snapshot(db, cfg.output_dir, cfg.data_path, cfg.web_dir, clock)
            interval = float(db.get_setting("desk.publish_interval_s") or 360)
            if cfg.gh_token and (clock.now() - last_publish).total_seconds() >= interval:
                publish_tree(cfg.output_dir, cfg.gh_repo, cfg.gh_token)
                last_publish = clock.now()
        except Exception as exc:
            log.info("writer %s", type(exc).__name__)
        wait = float(db.get_setting("desk.write_interval_s") or 15)
        await asyncio.sleep(max(1.0, wait))


async def amain():
    cfg = Config()
    cfg.load_paths(generate=True)
    for name in ("TG_TOKEN", "GH_TOKEN", "TYPESAFE_API_KEY", "APP_TOKEN", "DATA_PATH", "VAPID_PRIVATE_KEY"):
        scrub_env_name(name)
        register_secret(getattr(cfg, {
            "TG_TOKEN": "tg_token",
            "GH_TOKEN": "gh_token",
            "TYPESAFE_API_KEY": "typesafe_key",
            "APP_TOKEN": "app_token",
            "DATA_PATH": "data_path",
            "VAPID_PRIVATE_KEY": "vapid_private",
        }[name], None))
    setup(cfg.log_dir)
    clock = Clock()
    db = DB(cfg.db_path)
    _boot_db(db, clock, cfg.preview)
    if cfg.owner_chat_id and not db.settings_map().get("desk.owner_chat_id"):
        db.set_setting("desk.owner_chat_id", cfg.owner_chat_id, who="env", ts=iso(clock.now()))
    db.set_setting("desk.gh_repo", cfg.gh_repo, who="system", ts=iso(clock.now()))
    if os.environ.get("RPC_URL"):
        db.set_setting("desk.rpc_url", os.environ["RPC_URL"], who="env", ts=iso(clock.now()))
    bus = Bus()
    sources = None if cfg.preview else HttpSources(str(db.get_setting("desk.rpc_url")))
    # Notifier is attached after Telegram exists so commands and pushes share it.
    session = DeskSession(db, clock, notifier=None)
    telegram = Telegram(cfg.tg_token, session)
    session.notifier = telegram if cfg.tg_token else _NullNotifier()
    if not cfg.preview:
        auditor = Auditor(db, bus, sources, clock, session.notifier)
        analyst = Analyst(db, bus, sources, clock, session.notifier)
        analyst.jev_key = cfg.typesafe_key
        executor = Executor(db, bus, sources, clock, session.notifier)
        risk = Risk(db, bus, sources, clock, session.notifier)
        scanner = Scanner(db, bus, sources, clock, session.notifier)
        session.auditor = auditor
        session.executor = executor
        session.risk = risk
        tasks = [
            asyncio.create_task(scanner.run(), name="scanner"),
            asyncio.create_task(auditor.run(), name="auditor"),
            asyncio.create_task(analyst.run(), name="analyst"),
            asyncio.create_task(executor.run(), name="executor"),
            asyncio.create_task(risk.run(), name="risk"),
            asyncio.create_task(telegram.run(), name="telegram"),
            asyncio.create_task(_balance_loop(db, clock), name="balance"),
            asyncio.create_task(_writer(db, cfg, clock, sources), name="writer"),
        ]
    else:
        for name in ("scanner", "auditor", "analyst", "executor", "risk"):
            db.heartbeat(name, iso(clock.now()))
        tasks = [asyncio.create_task(_writer(db, cfg, clock, None), name="writer")]
    run_dir = cfg.root / "run"
    run_dir.mkdir(exist_ok=True)
    (run_dir / "desk.pid").write_text(f"{os.getpid()}\n{clock.now().timestamp()}\n", encoding="utf-8")
    if cfg.serve:
        _serve(cfg.output_dir, cfg.host, cfg.port)
    log.info("desk up preview=%s serve=%s", int(cfg.preview), int(cfg.serve))
    try:
        await asyncio.gather(*tasks)
    finally:
        if sources is not None:
            await sources.aclose()


class _NullNotifier:
    async def push(self, push, chat_id=None):
        return False

    async def reply(self, chat_id, text, markup=None):
        return False


def main():
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
