"""FOMO Solana fee schedule. Gas is 0. Referral discount comes off the fee."""

# $5–$47.50 → 2% of the ticket. The boundary equals the flat fee:
# 47.50 * 2% = $0.95, and $190 * 0.50% = $0.95.
_FLAT_AT = 47.50
_FLAT_UNTIL = 190.0
_FLAT = 0.95


def raw_fee(size_usd: float) -> float:
    size = float(size_usd)
    if size <= _FLAT_AT:
        return size * 0.02
    if size <= _FLAT_UNTIL:
        return _FLAT
    return size * 0.005


def one_side(size_usd: float, discount: float = 0.10) -> float:
    """Fee for one side after the referral discount. Gas is 0."""
    return raw_fee(size_usd) * (1.0 - float(discount))


def both_sides(size_usd: float, discount: float = 0.10) -> float:
    return one_side(size_usd, discount) * 2.0


def pnl(size_usd: float, entry: float, exit_price: float, discount: float = 0.10):
    """Net PnL and total fees. Fees are charged on the ticket, both sides."""
    gross = float(size_usd) * (float(exit_price) / float(entry) - 1.0)
    fees = both_sides(size_usd, discount)
    return gross - fees, fees
