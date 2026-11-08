"""The gate is code. Rules mode reads DexScreener numbers and says so."""

import asyncio

from desk.agents.analyst import Analyst, analyst_name, apply_gate, rules_scores, score_of
from desk.config import merged
from desk.tests.fakes import fresh_db, install, pair
from desk.sources import FakeSources
from desk.bus import Bus
from desk import fields

SETTINGS = merged({})


def test_each_boundary_rejects_and_the_threshold_passes():
    def gate(**answers):
        base = {"alive_6h": 0.80, "organic": 0.80, "flush": 1.0, "chase": 0.10}
        base.update(answers)
        return apply_gate(base, SETTINGS)

    alive = gate(alive_6h=0.69)
    assert not alive["passed"] and any(item.startswith("alive_6h") for item in alive["failed"])

    organic = gate(organic=0.64)
    assert not organic["passed"] and any(item.startswith("organic") for item in organic["failed"])

    chase = gate(chase=0.31)
    assert not chase["passed"] and any(item.startswith("chase") for item in chase["failed"])

    low = gate(alive_6h=0.70, organic=0.65, flush=0.71, chase=0.30)
    assert score_of(low["answers"]) == 69
    assert not low["passed"] and "score 69" in low["failed"]

    ok = gate(alive_6h=0.70, organic=0.65, flush=0.75, chase=0.30)
    assert score_of(ok["answers"]) == 70
    assert ok["passed"] and ok["failed"] == []


def test_rules_mode_from_dex_numbers_labels_the_row(tmp_path):
    cases = {
        "alive": dict(b1=400, s1=290, b5=40, s5=40, c5=10, v5=100, v1=100),
        "organic": dict(b1=500, s1=500, b5=16, s5=25, c5=10, v5=100, v1=100),
        "chase": dict(b1=500, s1=500, b5=40, s5=40, c5=31, v5=100, v1=100),
        "score": dict(b1=400, s1=300, b5=13, s5=20, c5=30, v5=71, v1=100),
        "pass": dict(b1=400, s1=300, b5=13, s5=20, c5=30, v5=75, v1=100),
    }
    expected = {}
    for name, nums in cases.items():
        mapped = fields.read_pair(pair("Mint", **nums))
        scores = rules_scores(mapped)
        expected[name] = apply_gate(scores, SETTINGS)
    assert not expected["alive"]["passed"]
    assert not expected["organic"]["passed"]
    assert not expected["chase"]["passed"]
    assert expected["score"]["score"] == 69 and not expected["score"]["passed"]
    assert expected["pass"]["passed"]

    db, clock = fresh_db(tmp_path / "desk.db")
    sources = FakeSources()
    mint = "RulesMint11111111111111111111111111111111"
    install(sources, mint, symbol="RULE", name="Rules", **cases["chase"])
    analyst = Analyst(db, Bus(), sources, clock)
    item = {
        "mint": mint, "symbol": "RULE", "name": "Rules", "pool": "Pool",
        "migratedAt": "2026-11-08T00:00:00Z", "top10Pct": 12,
        "liquidityUsd": 80000, "marketCap": 100000,
        "audit": {"layers": [
            {"n": 3, "value": "0.9% · sold 0"},
            {"n": 5, "value": "100% locked"},
            {"n": 7, "value": "None"},
            {"n": 8, "value": "None"},
        ]},
    }
    result = asyncio.run(analyst.judge(item))
    assert result["name"] == "Analyst (rules)"
    assert analyst_name("rules") == "Analyst (rules)"
    assert analyst_name("jev") == "Analyst"
    rows = db.activity()
    assert rows and "Analyst (rules)" in rows[0]["agent"]
    assert "Analyst (rules)" in rows[0]["subtitle"]
    assert "chase" in rows[0]["subtitle"]
