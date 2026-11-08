"""Shared raw payloads. Agents still read them through fields.py."""

from desk.db import DB
from desk.util import Clock, FakeClock, iso

POOL = "Pool111111111111111111111111111111111111111"
SUPPLY = 100_000_000
CREATOR = "Creator11111111111111111111111111111111111"


def fresh_db(path):
    clock = Clock()
    db = DB(str(path))
    db.init(iso(clock.now()))
    now = iso(clock.now())
    db.set_setting("desk.night_started", now, who="test", ts=now)
    db.set_setting("desk.night_start", 1000, who="test", ts=now)
    db.set_setting("desk.cash", 1000, who="test", ts=now)
    db.set_setting("desk.stack", 1000, who="test", ts=now)
    db.set_setting("desk.mode", "PAPER", who="test", ts=now)
    db.set_setting("auditor.retry_s", 0, who="test", ts=now)
    db.set_setting("analyst.mode", "rules", who="test", ts=now)
    return db, clock


def pair(mint, **over):
    pool = over.get("pool", POOL)
    return {
        "dexId": "pumpswap",
        "pairAddress": pool,
        "baseToken": {
            "address": mint,
            "name": over.get("name", "Coin"),
            "symbol": over.get("symbol", "COIN"),
        },
        "priceUsd": str(over.get("price", 0.001)),
        "liquidity": {"usd": over.get("liq", 80000)},
        "marketCap": over.get("mcap", 120000),
        "volume": {
            "m5": over.get("v5", 5000),
            "h1": over.get("v1", 20000),
            "h24": over.get("v24", 90000),
        },
        "txns": {
            "m5": {"buys": over.get("b5", 50), "sells": over.get("s5", 50)},
            "h1": {"buys": over.get("b1", 500), "sells": over.get("s1", 500)},
        },
        "priceChange": {
            "m5": over.get("c5", 0),
            "h1": over.get("c1", 10),
            "h24": over.get("c24", 20),
        },
        "pairCreatedAt": 0,
        "info": {"imageUrl": ""},
    }


def report(pool=POOL, creator=CREATOR, locked=100):
    return {"creator": creator, "markets": [{"pubkey": pool, "lp": {"lpLockedPct": locked}}]}


def graph():
    return [{
        "nodes": [
            {"id": "holderA", "holdings": 10},
            {"id": "holderB", "holdings": 12},
            {"id": "emptyC", "holdings": 0},
        ],
        "links": [
            {"source": "holderA", "target": "holderB"},
            {"source": "emptyC", "target": "holderA"},
        ],
    }]


def rpc_factory(freeze=None, mint_authority=None):
    def rpc(method, params):
        if method == "getAccountInfo":
            return {"result": {"value": {"data": {"parsed": {"info": {
                "mintAuthority": mint_authority,
                "freezeAuthority": freeze,
                "supply": str(SUPPLY),
                "decimals": 6,
            }}}}}}
        if method == "getTokenLargestAccounts":
            rows = [
                {"address": "PoolAcct1111111111111111111111111111111111", "amount": "40000000"},
                {"address": "CurveAcct111111111111111111111111111111111", "amount": "30000000"},
            ]
            for i in range(10):
                rows.append({
                    "address": f"Hold{i:02d}xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                    "amount": "1500000",
                })
            return {"result": {"value": rows}}
        if method == "getMultipleAccounts":
            values = []
            for addr in params[0]:
                if str(addr).startswith("PoolAcct"):
                    owner = POOL
                elif str(addr).startswith("Curve"):
                    owner = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
                else:
                    owner = "Wallet" + str(addr)[:6]
                values.append({"data": {"parsed": {"info": {"owner": owner}}}})
            return {"result": {"value": values}}
        if method == "getTokenAccountsByOwner":
            return {"result": {"value": [{
                "pubkey": "DevAcct111",
                "account": {"data": {"parsed": {"info": {
                    "owner": params[0],
                    "mint": "x",
                    "delegate": None,
                    "tokenAmount": {"amount": "500000"},
                }}}},
            }]}}
        if method == "getProgramAccounts":
            rows = []
            for i in range(3):
                rows.append({"pubkey": f"Tok{i}", "account": {"data": {"parsed": {"info": {
                    "owner": f"Own{i}",
                    "mint": "m",
                    "delegate": None,
                    "tokenAmount": {"amount": "1000"},
                }}}}})
            return {"result": rows}
        return {"error": {"message": f"unexpected {method}"}}
    return rpc


def install(sources, mint, **pair_over):
    sources.pairs[mint] = pair(mint, **pair_over)
    sources.reports[mint] = report()
    sources.graphs[mint] = graph()
