"""Every FOMO URL the desk produces comes from here. Never shorten or wrap one."""

import logging

log = logging.getLogger("desk.links")

CHAIN_ID = {
    "solana": 1399811149,
    "robinhood": 4663,
    "base": 8453,
    "bnb": 56,
}

SIGNUP_URL = "https://fomo.family/r/savipww"


def coin_url(address, chain="solana"):
    if chain not in CHAIN_ID:
        log.warning("unknown chain %s", chain)
        return SIGNUP_URL
    cid = CHAIN_ID[chain]
    return f"https://fomo.family/coin?address={address}&chainId={cid}&r=savipww&source=share_link"


def profile_url(handle):
    h = (handle or "").lstrip("@")
    return f"https://fomo.family/profile/{h}?source=share_link&r=savipww"
