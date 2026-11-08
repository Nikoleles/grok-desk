"""Observed raw paths and the only place agents read them.

Agents call the readers below. They do not index raw payloads.
"""

from __future__ import annotations

import re

FIELDS = {
    "migration": {
        "mint": "$.mint",
        "pool": "$.pool",
        "signature": "$.signature",
        "creator": None,
    },
    "dexscreener": {
        "pairAddress": "$.pairAddress",
        "dexId": "$.dexId",
        "priceUsd": "$.priceUsd",
        "liquidity.usd": "$.liquidity.usd",
        "marketCap": "$.marketCap",
        "volume.m5": "$.volume.m5",
        "volume.h1": "$.volume.h1",
        "volume.h24": "$.volume.h24",
        "txns.m5.buys": "$.txns.m5.buys",
        "txns.m5.sells": "$.txns.m5.sells",
        "txns.h1.buys": "$.txns.h1.buys",
        "txns.h1.sells": "$.txns.h1.sells",
        "priceChange.m5": "$.priceChange.m5",
        "priceChange.h1": "$.priceChange.h1",
        "priceChange.h24": "$.priceChange.h24",
        "pairCreatedAt": "$.pairCreatedAt",
    },
    "dexscreener_extra": {
        "name": "$.baseToken.name",
        "symbol": "$.baseToken.symbol",
        "base": "$.baseToken.address",
        "image": "$.info.imageUrl",
    },
    "rugcheck": {
        "creator": "$.creator",
        "market_pool": "$.markets[*].pubkey",
        "lp_locked_pct": "$.markets[*].lp.lpLockedPct",
        "lp_burned_pct": None,
    },
    "insiders_graph": {
        "nodes": "$[*].nodes",
        "edges": "$[*].links",
        "links": "$[*].links",
        "node_id": "$[*].nodes[*].id",
        "node_holdings": "$[*].nodes[*].holdings",
        "link_source": "$[*].links[*].source",
        "link_target": "$[*].links[*].target",
    },
    "rpc": {
        "mintAuthority": "$.result.value.data.parsed.info.mintAuthority",
        "freezeAuthority": "$.result.value.data.parsed.info.freezeAuthority",
        "supply": "$.result.value.data.parsed.info.supply",
        "decimals": "$.result.value.data.parsed.info.decimals",
    },
    "rpc_largest": {
        "address": "$.result.value[*].address",
        "amount": "$.result.value[*].amount",
    },
    "resolved": {
        "creator": {"source": "rugcheck", "path": "$.creator"},
        "pool": {"source": "dexscreener", "path": "$.pairAddress"},
        "lp_locked_pct": {
            "source": "rugcheck",
            "path": "$.markets[*].lp.lpLockedPct",
            "label": "locked",
        },
        "graph_edges": {
            "source": "insiders_graph",
            "path": "$[*].links",
            "from": "source",
            "to": "target",
        },
    },
}

NOTES = {
    "migration.pool": "Observed value is pump-amm, a label, NOT a pool address. Cannot use as Scanner pool "
    "public key. Resolve via DexScreener pairAddress.",
    "migration.creator": "Approved: obtain creator from RugCheck $.creator for the same mint; migration creator "
    "remains absent.",
    "rugcheck.market_pool": "Match markets[].pubkey to selected DexScreener pairAddress; there is no literal "
    "markets[].pool key.",
    "rugcheck.lp_burned_pct": "No explicit burned percentage field; do not relabel locked percentage as burned.",
    "insiders_graph.edges": "Approved mapping: graph links with source/target are the layer 7 edges; no literal "
    "edges key is required.",
    "sampling": "Migration sample mint differs from DexScreener/RugCheck/RPC proof mint.",
    "rugcheck.lp_locked_pct": "Approved: display lpLockedPct as locked, never burned.",
}

# Owners treated as the pool or the bonding curve, never as holders.
CURVE_OWNERS = {
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
}


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()


def is_missing(value) -> bool:
    return value is MISSING


def tokenize(path: str) -> list:
    if not isinstance(path, str) or not path.startswith("$"):
        raise ValueError(path)
    s = path[1:]
    tokens = []
    while s:
        if s[0] == ".":
            s = s[1:]
            continue
        if s.startswith("[*]"):
            tokens.append("*")
            s = s[3:]
            continue
        m = re.match(r"\[(\d+)\]", s)
        if m:
            tokens.append(int(m.group(1)))
            s = s[m.end() :]
            continue
        m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", s)
        if not m:
            raise ValueError(path)
        tokens.append(m.group(0))
        s = s[m.end() :]
    return tokens


def get_path(doc, path):
    """Return a scalar, a list when the path has a wildcard, or MISSING."""
    if path is None:
        return None
    tokens = tokenize(path)
    matches: list = []

    def walk(node, toks):
        if not toks:
            matches.append(node)
            return
        tok = toks[0]
        rest = toks[1:]
        if tok == "*":
            if isinstance(node, list):
                for item in node:
                    walk(item, rest)
            return
        if isinstance(tok, int):
            if isinstance(node, list) and 0 <= tok < len(node):
                walk(node[tok], rest)
            return
        if isinstance(node, dict):
            if tok in node:
                walk(node[tok], rest)
            return
        if isinstance(node, list):
            for item in node:
                walk(item, toks)

    walk(doc, tokens)
    if "*" in tokens:
        return matches
    if not matches:
        return MISSING
    return matches[0]


def _num(value):
    if value is MISSING or value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value):
    n = _num(value)
    if n is None:
        return None
    return int(n)


def read_migration(msg: dict) -> dict:
    """Creator is never taken from the migration message (approved mapping)."""
    if not isinstance(msg, dict):
        return {"mint": None, "pool_label": None, "signature": None, "creator": None}
    mint = get_path(msg, FIELDS["migration"]["mint"])
    pool = get_path(msg, FIELDS["migration"]["pool"])
    sig = get_path(msg, FIELDS["migration"]["signature"])
    return {
        "mint": None if is_missing(mint) else mint,
        "pool_label": None if is_missing(pool) else pool,
        "signature": None if is_missing(sig) else sig,
        "creator": None,
    }


def read_pair(pair: dict) -> dict | None:
    if not isinstance(pair, dict):
        return None
    out = {}
    for key, path in FIELDS["dexscreener"].items():
        out[key] = get_path(pair, path)
    extra = {}
    for key, path in FIELDS["dexscreener_extra"].items():
        extra[key] = get_path(pair, path)
    if is_missing(out.get("pairAddress")) and is_missing(out.get("dexId")):
        return None
    return {
        "pair_address": None if is_missing(out["pairAddress"]) else out["pairAddress"],
        "dex_id": None if is_missing(out["dexId"]) else out["dexId"],
        "price_usd": _num(out["priceUsd"]),
        "liquidity_usd": _num(out["liquidity.usd"]),
        "market_cap": _num(out["marketCap"]),
        "volume_m5": _num(out["volume.m5"]),
        "volume_h1": _num(out["volume.h1"]),
        "volume_h24": _num(out["volume.h24"]),
        "buys_m5": _int(out["txns.m5.buys"]),
        "sells_m5": _int(out["txns.m5.sells"]),
        "buys_h1": _int(out["txns.h1.buys"]),
        "sells_h1": _int(out["txns.h1.sells"]),
        "change_m5": _num(out["priceChange.m5"]),
        "change_h1": _num(out["priceChange.h1"]),
        "change_h24": _num(out["priceChange.h24"]),
        "pair_created_at": None if is_missing(out["pairCreatedAt"]) else out["pairCreatedAt"],
        "name": None if is_missing(extra["name"]) else extra["name"],
        "symbol": None if is_missing(extra["symbol"]) else extra["symbol"],
        "base": None if is_missing(extra["base"]) else extra["base"],
        "image": None if is_missing(extra["image"]) else extra["image"],
    }


def iter_pair_objects(payload) -> list:
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict)]
    if isinstance(payload, dict):
        if isinstance(payload.get("pairs"), list):
            return [p for p in payload["pairs"] if isinstance(p, dict)]
        return [payload]
    return []


def select_pumpswap(payload, mint: str | None = None) -> dict | None:
    """PumpSwap pair with the most liquidity. Several pairs → the deepest one."""
    best = None
    best_liq = -1.0
    for raw in iter_pair_objects(payload):
        mapped = read_pair(raw)
        if not mapped or mapped.get("dex_id") != "pumpswap":
            continue
        if mint and mapped.get("base") and mapped["base"] != mint:
            continue
        liq = mapped.get("liquidity_usd") or 0.0
        if liq > best_liq:
            best = mapped
            best_liq = liq
    return best


def assign_pairs(payload, mints: list[str]) -> dict[str, dict]:
    """Map each mint to its deepest PumpSwap pair inside one DexScreener payload."""
    found = {m: None for m in mints}
    liq = {m: -1.0 for m in mints}
    wanted = set(mints)
    for raw in iter_pair_objects(payload):
        mapped = read_pair(raw)
        if not mapped or mapped.get("dex_id") != "pumpswap":
            continue
        base = mapped.get("base")
        if base not in wanted:
            continue
        amount = mapped.get("liquidity_usd") or 0.0
        if amount > liq[base]:
            found[base] = mapped
            liq[base] = amount
    return {m: p for m, p in found.items() if p}


def read_creator(report) -> str | None:
    if not isinstance(report, dict):
        return None
    value = get_path(report, FIELDS["rugcheck"]["creator"])
    if is_missing(value) or value in (None, ""):
        return None
    return str(value)


def read_lp_locked_pct(report, pool: str):
    """Locked percent for the market whose pubkey equals the DexScreener pair.

    Returns the number, or None when that market (or the key) is absent.
    Never reads a burned percentage. The display label is locked.
    """
    if not isinstance(report, dict) or not pool:
        return None
    pubs = get_path(report, FIELDS["rugcheck"]["market_pool"])
    locks = get_path(report, FIELDS["rugcheck"]["lp_locked_pct"])
    if not isinstance(pubs, list) or not isinstance(locks, list):
        return None
    for pub, lock in zip(pubs, locks):
        if pub == pool:
            return _num(lock)
    return None


def read_insider_graph(doc) -> dict:
    """links (source → target) are the layer 7 edges.

    An empty list is a real answer (no networks). A payload whose objects
    omit the links key is "no data".
    """
    if doc is None:
        return {"missing_links": True, "nodes": [], "edges": []}
    if isinstance(doc, dict):
        if "links" not in doc:
            return {"missing_links": True, "nodes": [], "edges": []}
        doc = [doc]
    if not isinstance(doc, list):
        return {"missing_links": True, "nodes": [], "edges": []}
    if len(doc) == 0:
        return {"missing_links": False, "nodes": [], "edges": []}
    if not any(isinstance(net, dict) and "links" in net for net in doc):
        return {"missing_links": True, "nodes": [], "edges": []}

    nodes = []
    edges = []
    seen = set()
    for net in doc:
        if not isinstance(net, dict) or "links" not in net:
            continue
        for node in net.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            nid = node.get("id")
            if not nid or nid in seen:
                continue
            seen.add(nid)
            holdings = node.get("holdings")
            try:
                holdings = float(holdings or 0)
            except (TypeError, ValueError):
                holdings = 0.0
            nodes.append({"id": nid, "holdings": holdings})
        for link in net.get("links") or []:
            if isinstance(link, dict) and link.get("source") and link.get("target"):
                edges.append((link["source"], link["target"]))
    return {"missing_links": False, "nodes": nodes, "edges": edges}


def largest_holder_component(graph: dict) -> int:
    """Biggest connected component among nodes that still hold the mint."""
    holders = {n["id"] for n in graph.get("nodes") or [] if (n.get("holdings") or 0) > 0}
    parent = {h: h for h in holders}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for src, dst in graph.get("edges") or []:
        if src in holders and dst in holders:
            union(src, dst)
    if not holders:
        return 0
    sizes: dict[str, int] = {}
    for h in holders:
        root = find(h)
        sizes[root] = sizes.get(root, 0) + 1
    return max(sizes.values())


def read_mint_account(rpc_response) -> dict:
    if not isinstance(rpc_response, dict):
        return {"mint_authority": MISSING, "freeze_authority": MISSING, "supply": MISSING, "decimals": MISSING, "error": True}
    if rpc_response.get("error"):
        return {"mint_authority": MISSING, "freeze_authority": MISSING, "supply": MISSING, "decimals": MISSING, "error": True}
    return {
        "mint_authority": get_path(rpc_response, FIELDS["rpc"]["mintAuthority"]),
        "freeze_authority": get_path(rpc_response, FIELDS["rpc"]["freezeAuthority"]),
        "supply": get_path(rpc_response, FIELDS["rpc"]["supply"]),
        "decimals": get_path(rpc_response, FIELDS["rpc"]["decimals"]),
        "error": False,
    }


def read_largest(rpc_response) -> list[dict]:
    if not isinstance(rpc_response, dict) or rpc_response.get("error"):
        return []
    value = get_path(rpc_response, "$.result.value")
    if not isinstance(value, list):
        return []
    rows = []
    for item in value:
        if not isinstance(item, dict):
            continue
        rows.append({
            "address": item.get("address"),
            "amount": item.get("amount"),
        })
    return rows


def read_owners(rpc_response, addresses: list[str]) -> dict[str, str | None]:
    """Map token-account address → owner from getMultipleAccounts jsonParsed."""
    owners = {a: None for a in addresses}
    if not isinstance(rpc_response, dict) or rpc_response.get("error"):
        return owners
    value = get_path(rpc_response, "$.result.value")
    if not isinstance(value, list):
        return owners
    for addr, item in zip(addresses, value):
        if not isinstance(item, dict):
            continue
        info = get_path(item, "$.data.parsed.info.owner")
        owners[addr] = None if is_missing(info) else info
    return owners


def excluded_holder(address: str | None, owner: str | None, pool: str | None) -> bool:
    if pool and (address == pool or owner == pool):
        return True
    if owner in CURVE_OWNERS:
        return True
    return False


def top10_pct(accounts: list[dict], owners: dict, supply_raw, pool: str | None) -> float | None:
    try:
        supply = int(supply_raw)
    except (TypeError, ValueError):
        return None
    if supply <= 0:
        return None
    kept = []
    for row in accounts:
        addr = row.get("address")
        owner = owners.get(addr)
        if excluded_holder(addr, owner, pool):
            continue
        try:
            kept.append(int(row.get("amount") or 0))
        except (TypeError, ValueError):
            continue
    kept.sort(reverse=True)
    return sum(kept[:10]) / supply * 100.0


def read_token_accounts(rpc_response) -> list[dict]:
    """getProgramAccounts or getTokenAccountsByOwner, jsonParsed."""
    if not isinstance(rpc_response, dict) or rpc_response.get("error"):
        return []
    rows = rpc_response.get("result")
    if isinstance(rows, dict):
        rows = rows.get("value")
    if not isinstance(rows, list):
        return []
    out = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        pubkey = item.get("pubkey")
        info = get_path(item, "$.account.data.parsed.info")
        if is_missing(info) or not isinstance(info, dict):
            info = get_path(item, "$.data.parsed.info")
        if not isinstance(info, dict):
            continue
        token_amount = info.get("tokenAmount") or {}
        amount = token_amount.get("amount") if isinstance(token_amount, dict) else None
        delegate = info.get("delegate")
        out.append({
            "address": pubkey,
            "owner": info.get("owner"),
            "mint": info.get("mint"),
            "amount": amount,
            "delegate": delegate if delegate else None,
        })
    return out


def rpc_refused(rpc_response) -> bool:
    if not isinstance(rpc_response, dict):
        return False
    err = rpc_response.get("error")
    if not err:
        return False
    text = str(err).lower()
    needles = ("excluded", "not allowed", "method not found", "unavailable", "disabled", "secondary index")
    return any(n in text for n in needles)


def creator_balance_raw(rpc_response) -> int | None:
    accounts = read_token_accounts(rpc_response)
    if rpc_response and isinstance(rpc_response, dict) and rpc_response.get("error"):
        return None
    total = 0
    for row in accounts:
        try:
            total += int(row.get("amount") or 0)
        except (TypeError, ValueError):
            continue
    return total


def short_addr(addr: str) -> str:
    if not addr:
        return ""
    if len(addr) <= 10:
        return addr
    return f"{addr[:4]}...{addr[-3:]}"
