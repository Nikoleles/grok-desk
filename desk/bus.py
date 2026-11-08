"""Agents talk only through this bus and the database."""

import asyncio


class Bus:
    def __init__(self):
        self.auditor: asyncio.Queue = asyncio.Queue()
        self.analyst: asyncio.Queue = asyncio.Queue()
        self.executor: asyncio.Queue = asyncio.Queue()
        self.events: asyncio.Queue = asyncio.Queue()

    async def emit(self, event: dict):
        await self.events.put(event)
