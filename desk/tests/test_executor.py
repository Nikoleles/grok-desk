"""Sizing: 8% of stack, the $47.50 floor, and the 2% liquidity cap."""

from desk.agents.executor import compute_size, ticket_warning
from desk.config import DEFAULTS, merged
from desk.fees import one_side

SETTINGS = merged({})


def test_bump_skip_and_liquidity_cap():
    bump = compute_size(200, 200, 1_000_000, {**SETTINGS, "executor.below_min": "bump"})
    assert bump.ok and bump.bumped
    assert bump.size == 47.50

    skipped = compute_size(200, 200, 1_000_000, {**SETTINGS, "executor.below_min": "skip"})
    assert not skipped.ok
    assert skipped.reason == "under $47.50"

    warning = ticket_warning(200, {**SETTINGS, "executor.below_min": "skip"})
    assert warning == "every coin will be skipped"
    assert ticket_warning(200, {**SETTINGS, "executor.below_min": "bump"}) is None

    capped = compute_size(20_000, 20_000, 40_000, SETTINGS)
    assert capped.ok and capped.capped
    assert capped.size == 800

    # 8% of a normal stack clears the floor and is not capped by a deep pool.
    plain = compute_size(1000, 1000, 80_000, SETTINGS)
    assert plain.ok and not plain.bumped and not plain.capped
    assert plain.size == 80


def test_fee_schedule_boundaries():
    # 47.50 * 2% == $0.95, then the 10% referral discount.
    assert abs(one_side(47.50) - 0.855) < 1e-9
    assert abs(one_side(190) - 0.855) < 1e-9
    assert abs(one_side(200) - 0.90) < 1e-9
    assert abs(one_side(10) - 0.18) < 1e-9
    assert DEFAULTS["desk.publish_interval_s"] == 360
