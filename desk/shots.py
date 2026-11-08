"""Render the iPhone screens at 390px with headless Chrome."""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
import subprocess
import time
from pathlib import Path

import websockets

from desk.db import DB
from desk.publish import write_snapshot
from desk.seed_preview import seed
from desk.util import Clock, iso

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "desk" / "web"
OUT = Path("/opt/cursor/artifacts")


def build_stage(stage: Path) -> dict[str, str]:
    if stage.exists():
        shutil.rmtree(stage)
    clock = Clock()
    pop = "a" * 40
    empty = "b" * 40
    err = "c" * 40
    pop_db = DB(str(stage / "pop.db"))
    pop_db.init(iso(clock.now()))
    seed(pop_db, clock)
    write_snapshot(pop_db, stage, pop, WEB, clock)
    empty_db = DB(str(stage / "empty.db"))
    empty_db.init(iso(clock.now()))
    now = iso(clock.now())
    empty_db.set_setting("desk.night_started", now, who="shot", ts=now)
    empty_db.set_setting("desk.night_start", 1000, who="shot", ts=now)
    empty_db.set_setting("desk.cash", 1000, who="shot", ts=now)
    empty_db.set_setting("desk.stack", 1000, who="shot", ts=now)
    empty_db.set_setting("desk.mode", "PAPER", who="shot", ts=now)
    empty_db.set_setting("desk.fomo_handle", "", who="shot", ts=now)
    empty_db.add_balance(now, 1000)
    write_snapshot(empty_db, stage, empty, WEB, clock)
    # Error keeps the last good numbers and raises the offline row.
    err_root = stage / "d" / err
    shutil.copytree(stage / "d" / pop, err_root)
    state_path = err_root / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["offline"] = True
    state["retryIn"] = 8
    state["lastUpdateText"] = "4:41 PM"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    for name in ("JASON", "MIRA", "NOPE"):
        pass
    return {
        "pop": pop,
        "empty": empty,
        "err": err,
        "jason": "DDDfDJGreM6pYbKEiB8NqkZ3RhgG9Q1zKK58SvcHpump",
        "mira": "MiraMint111111111111111111111111111111pump",
    }


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.n = 0

    async def call(self, method, params=None, timeout=20):
        self.n += 1
        ident = self.n
        await self.ws.send(json.dumps({"id": ident, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=deadline - time.time())
            msg = json.loads(raw)
            if msg.get("id") == ident:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result") or {}
        raise TimeoutError(method)


async def capture(cdp: CDP, url: str, dest: Path):
    await cdp.call("Emulation.setDeviceMetricsOverride", {
        "width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True,
    })
    await cdp.call("Runtime.evaluate", {
        "expression": "document.documentElement.dataset.ready=''",
        "returnByValue": True,
    })
    await cdp.call("Page.navigate", {"url": url})
    ready = False
    marker = "shot=1"
    if "screen=" in url:
        marker = "screen=" + url.split("screen=", 1)[1].split("&", 1)[0]
    mint_marker = ""
    if "mint=" in url:
        mint_marker = url.split("mint=", 1)[1].split("&", 1)[0]
    expr = (
        "(document.documentElement.dataset.ready==='1' && location.hash.indexOf("
        + json.dumps(marker) + ")!==-1 && location.hash.indexOf("
        + json.dumps(mint_marker) + ")!==-1) ? location.hash : ''"
    )
    for _ in range(60):
        result = await cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        if (result.get("result") or {}).get("value"):
            ready = True
            break
        await asyncio.sleep(0.15)
    if not ready:
        raise RuntimeError("page not ready " + url)
    height = await cdp.call("Runtime.evaluate", {
        "expression": "Math.max(document.body.scrollHeight, document.documentElement.scrollHeight, 844)",
        "returnByValue": True,
    })
    h = int((height.get("result") or {}).get("value") or 844)
    h = max(844, min(h + 8, 4200))
    await cdp.call("Emulation.setDeviceMetricsOverride", {
        "width": 390, "height": h, "deviceScaleFactor": 1, "mobile": True,
    })
    await asyncio.sleep(0.2)
    await cdp.call("Runtime.evaluate", {
        "expression": "window.dispatchEvent(new Event('resize'))",
        "returnByValue": True,
    })
    await asyncio.sleep(0.35)
    image = await cdp.call("Page.captureScreenshot", {"format": "png"})
    dest.write_bytes(base64.b64decode(image["data"]))


async def shoot(base: str, paths: dict, out: Path):
    proc = subprocess.Popen(
        [
            "google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox",
            "--hide-scrollbars", "--remote-debugging-port=9333",
            "--user-data-dir=/tmp/desk-chrome", "about:blank",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        ws_url = None
        for _ in range(40):
            try:
                with urllib_open("http://127.0.0.1:9333/json/list") as res:
                    tabs = json.load(res)
                pages = [t for t in tabs if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                await asyncio.sleep(0.15)
        if not ws_url:
            raise RuntimeError("chrome did not open a debugger")
        async with websockets.connect(ws_url, max_size=20_000_000) as ws:
            cdp = CDP(ws)
            await cdp.call("Page.enable")
            def page(name, data, screen, mint=""):
                frag = f"d={data}&screen={screen}&shot=1"
                if mint:
                    frag += f"&mint={mint}"
                # A unique query forces a document load. Hash-only navigations do not.
                return f"{base}/?v={name}#{frag}"

            shots = [
                ("home", page("home", paths["pop"], "home")),
                ("home-empty", page("home-empty", paths["empty"], "home")),
                ("home-error", page("home-error", paths["err"], "home")),
                ("agents", page("agents", paths["pop"], "agents")),
                ("agents-empty", page("agents-empty", paths["empty"], "agents")),
                ("agents-error", page("agents-error", paths["err"], "agents")),
                ("auditor", page("auditor", paths["pop"], "auditor", paths["jason"])),
                ("auditor-empty", page("auditor-empty", paths["empty"], "auditor")),
                ("auditor-error", page("auditor-error", paths["err"], "auditor", paths["jason"])),
                ("coin", page("coin", paths["pop"], "coin", paths["jason"])),
                ("coin-empty", page("coin-empty", paths["pop"], "coin", paths["mira"])),
                ("coin-error", page("coin-error", paths["err"], "coin", paths["jason"])),
                ("account", page("account", paths["pop"], "account")),
                ("account-empty", page("account-empty", paths["empty"], "account")),
                ("account-error", page("account-error", paths["err"], "account")),
            ]
            for name, url in shots:
                await capture(cdp, url, out / f"{name}.png")
                print(name, (out / f"{name}.png").stat().st_size)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def urllib_open(url):
    import urllib.request
    return urllib.request.urlopen(url, timeout=2)


def main():
    import http.server
    import socketserver
    import threading

    stage = Path("/tmp/desk-shots")
    paths = build_stage(stage)
    OUT.mkdir(parents=True, exist_ok=True)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(stage), **kwargs)

        def log_message(self, fmt, *args):
            return

    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", 8793), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        asyncio.run(shoot("http://127.0.0.1:8793", paths, OUT))
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
