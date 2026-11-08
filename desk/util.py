"""Clock, money, and percent formatting."""

from datetime import datetime, timedelta, timezone


class Clock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FakeClock:
    def __init__(self, t: datetime):
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        self.t = t

    def now(self) -> datetime:
        return self.t

    def advance(self, seconds: float):
        self.t = self.t + timedelta(seconds=seconds)
        return self.t


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def cents(n: float) -> float:
    return round(float(n) + 1e-9, 2)


def money(n: float, signed: bool = False) -> str:
    n = float(n)
    if signed:
        sign = "+" if n > 0 else ("-" if n < 0 else "")
        return f"{sign}${abs(n):,.2f}"
    if n < 0:
        return f"-${abs(n):,.2f}"
    return f"${n:,.2f}"


def compact_usd(n: float) -> str:
    n = float(n)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000:
        return f"{sign}${n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{sign}${n / 1000:.1f}K"
    return f"{sign}${n:.0f}"


def fmt_pct(p: float) -> str:
    p = float(p)
    if abs(p) >= 10:
        return f"{p:.0f}%"
    return f"{p:.1f}%"


def stop_label(pct: float) -> str:
    """'-8%' from a positive magnitude stored on the position or the setting."""
    pct = float(pct)
    if abs(pct - round(pct)) < 0.05:
        return f"{int(round(pct))}"
    return f"{pct:.1f}"


def age_text(migrated: datetime, now: datetime) -> str:
    mins = int((now - migrated).total_seconds() // 60)
    if mins < 1:
        mins = 0
    if mins < 60:
        return f"{mins}m old"
    hours = mins // 60
    if hours < 48:
        return f"{hours}h old"
    return f"{hours // 24}d old"


def age_short(migrated: datetime, now: datetime) -> str:
    mins = max(0, int((now - migrated).total_seconds() // 60))
    if mins < 60:
        return f"{mins}m"
    hours = mins // 60
    if hours < 48:
        return f"{hours}h"
    return f"{hours // 24}d"


def price_text(p: float | None) -> str:
    if p is None:
        return "n/a"
    p = float(p)
    if p >= 1000:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:,.4f}".rstrip("0").rstrip(".")
    s = f"{p:.8f}".rstrip("0").rstrip(".")
    return f"${s}"


def pct_signed(p: float) -> str:
    p = float(p)
    if abs(p) >= 10:
        body = f"{abs(p):.0f}"
    else:
        body = f"{abs(p):.1f}"
    if p > 0:
        return f"+{body}%"
    if p < 0:
        return f"-{body}%"
    return "0%"
