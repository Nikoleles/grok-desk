"""Approved raw paths, checked against the real samples."""

import json
from pathlib import Path

from desk import fields

ROOT = Path(__file__).resolve().parents[1] / "samples"


def _load(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def test_migration_sample_creator_absent_and_pool_is_a_label():
    msg = _load("pumpportal-migration.json")
    read = fields.read_migration(msg)
    assert read["mint"] == "AWaBBNoVuwQ22B8Y5nEtLne2uEfk8UaYEFws7vT7pump"
    assert read["pool_label"] == "pump-amm"
    assert read["signature"].startswith("3KMj")
    assert read["creator"] is None
    assert fields.FIELDS["migration"]["creator"] is None


def test_dex_and_rugcheck_and_graph_and_rpc_samples():
    pair = fields.read_pair(_load("dex-pair.json"))
    assert pair["dex_id"] == "pumpswap"
    assert pair["pair_address"] == "ALUywhhvV5DaJncyru5HzzB7oMyhq9jE52n5yRAuBTEk"
    assert pair["price_usd"] == 0.0001927
    assert pair["liquidity_usd"] == 33820.24
    assert pair["name"] == "Jason Duval"
    assert pair["symbol"] == "JASON"
    selected = fields.select_pumpswap(_load("dex-pair.json"))
    assert selected["pair_address"] == pair["pair_address"]

    report = _load("rugcheck-report.json")
    assert fields.read_creator(report) == "g2kai8M6mJQroTG1EGGtP4RQHaqStDgsXGkukLutzo9"
    locked = fields.read_lp_locked_pct(report, pair["pair_address"])
    assert locked == 100
    assert fields.FIELDS["rugcheck"]["lp_burned_pct"] is None
    assert "burned" not in f"{locked:.0f}% locked"

    graph = fields.read_insider_graph(_load("rugcheck-graph.json"))
    assert graph["missing_links"] is False
    assert graph["edges"]
    assert fields.largest_holder_component(graph) == 3

    mint = fields.read_mint_account(_load("rpc-mint.json"))
    assert mint["mint_authority"] is None
    assert mint["freeze_authority"] is None
    assert str(mint["supply"]).startswith("938029880793863")
    assert mint["decimals"] == 6


def test_missing_links_key_is_not_an_empty_graph():
    assert fields.read_insider_graph([{"nodes": []}])["missing_links"] is True
    assert fields.read_insider_graph([])["missing_links"] is False


def test_agents_do_not_touch_raw_keys():
    root = Path(__file__).resolve().parents[1] / "agents"
    banned = ("pairAddress", "lpLockedPct", "mintAuthority", "freezeAuthority", "priceUsd", "lpLocked")
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for word in banned:
            assert word not in text, f"{path.name} reads {word}"
