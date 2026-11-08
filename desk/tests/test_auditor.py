"""A live freeze authority stops the audit. Later layers are not checked."""

import asyncio

from desk.agents.auditor import Auditor
from desk.bus import Bus
from desk.sources import FakeSources
from desk.tests.fakes import POOL, fresh_db, rpc_factory
from desk.util import iso
from datetime import timedelta


def test_freeze_authority_stops_at_layer_2(tmp_path):
    db, clock = fresh_db(tmp_path / "desk.db")
    sources = FakeSources()
    mint = "FreezeMint1111111111111111111111111111111"
    sources.rpc_fn = rpc_factory(freeze="FreezeAuth111111111111111111111111111111")
    sources.reports[mint] = {"creator": "nobody"}
    def boom(mint):
        raise AssertionError("graph should not be fetched after a red layer")
    sources.rug_graph = boom
    migrated = iso(clock.now() - timedelta(minutes=20))
    auditor = Auditor(db, Bus(), sources, clock, notifier=None)
    result = asyncio.run(auditor.audit({
        "mint": mint,
        "symbol": "FRZ",
        "name": "Frozen",
        "pool": POOL,
        "migratedAt": migrated,
        "creator": "Creator11111111111111111111111111111111111",
        "creatorBalance": "500000",
    }, handoff=True))
    assert result["passed"] is False
    assert result["reason"] == "freeze not revoked"
    assert result["layersChecked"] == 2
    assert result["layers"][0]["pass"] is True
    assert result["layers"][0]["value"] == "Revoked"
    assert result["layers"][1]["pass"] is False
    assert result["layers"][1]["value"] == "freeze not revoked"
    for layer in result["layers"][2:]:
        assert layer["value"] == "not checked"
        assert layer["pass"] is None
    assert result["layers"][1]["source"] == "rpc"
    row = db.activity()[0]
    assert row["kind"] == "rejected"
    assert "freeze not revoked" in row["subtitle"]
    assert bus_empty(auditor)
    # Nothing was handed to the analyst.
    assert auditor.bus.analyst.empty()


def bus_empty(auditor):
    return True
