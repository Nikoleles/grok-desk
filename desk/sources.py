"""HTTP and websocket sources. Agents receive raw JSON and read it through fields.py."""

from __future__ import annotations

import httpx

DEX_URL = "https://api.dexscreener.com/tokens/v1/solana/"
RUG_REPORT = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
RUG_GRAPH = "https://api.rugcheck.xyz/v1/tokens/{mint}/insiders/graph"
PUMP_WS = "wss://pumpportal.fun/api/data"


class RateLimit(Exception):
    pass


class HttpSources:
    def __init__(self, rpc_url: str, client: httpx.AsyncClient | None = None):
        self.rpc_url = rpc_url
        self.client = client or httpx.AsyncClient(timeout=20.0)
        self._owns = client is None

    async def aclose(self):
        if self._owns:
            await self.client.aclose()

    async def dex_pairs(self, mints: list[str]) -> list:
        if not mints:
            return []
        out: list = []
        for i in range(0, len(mints), 30):
            chunk = mints[i : i + 30]
            url = DEX_URL + ",".join(chunk)
            response = await self.client.get(url)
            if response.status_code == 429:
                raise RateLimit()
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                out.extend(data)
            elif isinstance(data, dict) and isinstance(data.get("pairs"), list):
                out.extend(data["pairs"])
        return out

    async def rug_report(self, mint: str):
        response = await self.client.get(RUG_REPORT.format(mint=mint))
        if response.status_code == 429:
            raise RateLimit()
        response.raise_for_status()
        return response.json()

    async def rug_graph(self, mint: str):
        response = await self.client.get(RUG_GRAPH.format(mint=mint))
        if response.status_code == 429:
            raise RateLimit()
        response.raise_for_status()
        return response.json()

    async def rpc(self, method: str, params: list):
        response = await self.client.post(
            self.rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        )
        if response.status_code == 429:
            raise RateLimit()
        response.raise_for_status()
        return response.json()


class FakeSources:
    """In-memory raw payloads for tests and the replay harness."""

    def __init__(self):
        self.pairs: dict[str, object] = {}
        self.reports: dict[str, object] = {}
        self.graphs: dict[str, object] = {}
        self.rpc_fn = lambda method, params: {"result": None}
        self.dex_calls = 0

    async def dex_pairs(self, mints: list[str]) -> list:
        self.dex_calls += 1
        rows = []
        for mint in mints:
            item = self.pairs.get(mint)
            if item is None:
                continue
            if isinstance(item, list):
                rows.extend(item)
            else:
                rows.append(item)
        return rows

    async def rug_report(self, mint: str):
        if mint not in self.reports:
            raise RuntimeError("no rug report")
        return self.reports[mint]

    async def rug_graph(self, mint: str):
        if mint not in self.graphs:
            raise RuntimeError("no graph")
        return self.graphs[mint]

    async def rpc(self, method: str, params: list):
        return self.rpc_fn(method, params)
