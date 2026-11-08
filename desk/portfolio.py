"""Mark-to-market equity. Cash is liquid; open positions add their market value."""


def equity(db) -> float:
    settings = db.settings()
    cash = float(settings["desk.cash"])
    total = cash
    rows = list(db.open_positions()) + list(db.exiting_positions())
    for pos in rows:
        if pos["exit_price"]:
            price = float(pos["exit_price"])
        else:
            last = db.last_price(pos["mint"])
            price = float(last["price"]) if last else float(pos["entry_price"])
        entry = float(pos["entry_price"]) or 1.0
        total += float(pos["size_usd"]) * (price / entry)
    return total
