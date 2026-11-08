"""Settings catalog and secret loading.

Secrets are read from the environment, then from a mode-0600 file under
.secrets/. They are never printed. DATA_PATH is never written into source.
"""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRETS_DIR = ROOT / ".secrets"

DATA_PATH_RE = re.compile(r"^[A-Za-z0-9_-]{32,}$")

# Numbers in brackets in the agent prompts are these defaults.
DEFAULTS: dict[str, object] = {
    "scanner.min_liquidity_usd": 40000,
    "scanner.max_top10_pct": 25,
    "scanner.max_h1_change_pct": 80,
    "scanner.poll_s": 60,
    "scanner.min_pool_age_min": 10,
    "scanner.max_wait_min": 60,
    "scanner.runner_h24_pct": 300,
    "auditor.enabled": 1,
    "auditor.dev_max_pct": 5,
    "auditor.top10_max_pct": 25,
    "auditor.lp_locked_min_pct": 99,
    "auditor.liq_drop_max_pct": 50,
    "auditor.min_pool_age_min": 10,
    "auditor.bundle_max": 5,
    "auditor.delegate_max": 10,
    "auditor.retry_n": 3,
    "auditor.retry_s": 2,
    "analyst.mode": "rules",
    "analyst.alive_min": 0.70,
    "analyst.organic_min": 0.65,
    "analyst.chase_max": 0.30,
    "analyst.score_min": 70,
    "analyst.state_max_chars": 3000,
    "executor.stack_pct": 8,
    "executor.liq_cap_pct": 2,
    "executor.min_ticket_usd": 47.50,
    "executor.below_min": "bump",
    "executor.liq_drop_skip_pct": 20,
    "executor.ran_skip_pct": 15,
    "executor.fill_timeout_min": 5,
    "executor.paper_start_stack": 1000,
    "risk.stop_pct": 6,
    "risk.trail_arm_pct": 20,
    "risk.trail_pct": 12,
    "risk.timeout_h": 3,
    "risk.timeout_min_gain_pct": 5,
    "risk.poll_s": 15,
    "risk.no_price_polls": 4,
    "risk.http_backoff_s": 30,
    "risk.sold_remind_min": 2,
    "desk.mode": "PAPER",
    "desk.fomo_handle": "",
    "desk.rpc_url": "https://api.mainnet-beta.solana.com",
    "desk.publish_interval_s": 360,
    "desk.write_interval_s": 15,
    "desk.fees_ref_discount": 0.10,
    "desk.stack": 1000,
    "desk.cash": 1000,
    "desk.gh_repo": "Nikoleles/grok-desk",
    "desk.owner_chat_id": "",
    "desk.paper_nights": 0,
    "desk.layer8_down": 0,
    "desk.layer8_rpc": "",
    "desk.offline": 0,
    "desk.unread": 0,
    "paused.scanner": 0,
    "paused.auditor": 0,
    "paused.analyst": 0,
    "paused.executor": 0,
    "paused.risk": 0,
}

# Account tab groups. (key, label, kind, slider min, slider max)
# kind: usd | pct | num | seconds | minutes | hours | bool | text | mode
CATALOG = [
    ("scanner", "Scanner", [
        ("scanner.min_liquidity_usd", "Min liquidity", "usd", 0, 200000),
        ("scanner.max_top10_pct", "Max top 10", "pct", 0, 100),
        ("scanner.max_h1_change_pct", "Max 1h change", "pct", 0, 500),
        ("scanner.poll_s", "Poll", "seconds", 15, 300),
        ("scanner.min_pool_age_min", "Min pool age", "minutes", 0, 120),
        ("scanner.max_wait_min", "Max wait", "minutes", 10, 240),
        ("scanner.runner_h24_pct", "Runner 24h", "pct", 0, 2000),
    ]),
    ("auditor", "Auditor", [
        ("auditor.enabled", "Take apart before buying", "bool", 0, 1),
        ("auditor.dev_max_pct", "Max dev hold", "pct", 0, 50),
        ("auditor.top10_max_pct", "Max top 10", "pct", 0, 100),
        ("auditor.lp_locked_min_pct", "Min LP locked", "pct", 0, 100),
        ("auditor.liq_drop_max_pct", "Max liquidity drop", "pct", 0, 100),
        ("auditor.min_pool_age_min", "Min pool age", "minutes", 0, 120),
        ("auditor.bundle_max", "Max bundled wallets", "num", 1, 30),
        ("auditor.delegate_max", "Max delegated wallets", "num", 1, 50),
        ("auditor.retry_n", "Retries", "num", 1, 6),
        ("auditor.retry_s", "Retry gap", "seconds", 0, 30),
    ]),
    ("analyst", "Analyst", [
        ("analyst.mode", "Mode", "text", 0, 1),
        ("analyst.alive_min", "Alive 6h minimum", "num", 0, 1),
        ("analyst.organic_min", "Organic minimum", "num", 0, 1),
        ("analyst.chase_max", "Chase maximum", "num", 0, 1),
        ("analyst.score_min", "Score minimum", "num", 0, 100),
        ("analyst.state_max_chars", "State length", "num", 500, 6000),
    ]),
    ("executor", "Executor", [
        ("executor.stack_pct", "Size of stack", "pct", 1, 25),
        ("executor.liq_cap_pct", "Pool liquidity cap", "pct", 0.5, 10),
        ("executor.min_ticket_usd", "Minimum ticket", "usd", 5, 100),
        ("executor.below_min", "Under the minimum", "text", 0, 1),
        ("executor.liq_drop_skip_pct", "Skip if liquidity drops", "pct", 0, 80),
        ("executor.ran_skip_pct", "Skip if price runs", "pct", 0, 80),
        ("executor.fill_timeout_min", "LIVE fill window", "minutes", 1, 30),
        ("executor.paper_start_stack", "PAPER starting stack", "usd", 50, 100000),
    ]),
    ("risk", "Risk", [
        ("risk.stop_pct", "Stop", "pct", 1, 40),
        ("risk.trail_arm_pct", "Trail arms at", "pct", 1, 100),
        ("risk.trail_pct", "Trail giveback", "pct", 1, 50),
        ("risk.timeout_h", "Timeout", "hours", 1, 24),
        ("risk.timeout_min_gain_pct", "Timeout if under", "pct", 0, 30),
        ("risk.poll_s", "Poll", "seconds", 5, 120),
        ("risk.no_price_polls", "No-price polls", "num", 1, 10),
        ("risk.http_backoff_s", "Backoff after 429", "seconds", 5, 120),
        ("risk.sold_remind_min", "LIVE sold reminder", "minutes", 1, 10),
    ]),
    ("desk", "Desk", [
        ("desk.publish_interval_s", "Pages publish", "seconds", 120, 3600),
        ("desk.write_interval_s", "Local snapshot", "seconds", 5, 120),
        ("desk.fees_ref_discount", "Fee discount", "num", 0, 0.5),
        ("desk.rpc_url", "RPC URL", "text", 0, 1),
        ("desk.fomo_handle", "FOMO handle", "text", 0, 1),
    ]),
]

ALIASES = {
    "stop": "risk.stop_pct",
    "stop_pct": "risk.stop_pct",
    "trail": "risk.trail_pct",
    "trail_pct": "risk.trail_pct",
    "timeout": "risk.timeout_h",
    "timeout_h": "risk.timeout_h",
    "stack": "desk.stack",
    "min liquidity": "scanner.min_liquidity_usd",
    "min_liquidity": "scanner.min_liquidity_usd",
    "liquidity": "scanner.min_liquidity_usd",
    "top 10": "scanner.max_top10_pct",
    "top10": "scanner.max_top10_pct",
    "h1": "scanner.max_h1_change_pct",
    "poll": "scanner.poll_s",
    "mode": "analyst.mode",
    "alive": "analyst.alive_min",
    "organic": "analyst.organic_min",
    "chase": "analyst.chase_max",
    "score": "analyst.score_min",
    "size": "executor.stack_pct",
    "stack_pct": "executor.stack_pct",
    "below_min": "executor.below_min",
    "handle": "desk.fomo_handle",
    "fomo": "desk.fomo_handle",
    "rpc": "desk.rpc_url",
    "publish": "desk.publish_interval_s",
}

SECRET_NAMES = (
    "TG_TOKEN",
    "GH_TOKEN",
    "TYPESAFE_API_KEY",
    "OWNER_CHAT_ID",
    "DATA_PATH",
    "APP_TOKEN",
    "VAPID_PRIVATE_KEY",
    "VAPID_PUBLIC_KEY",
)


def _read_secret_file(name: str) -> str | None:
    path = SECRETS_DIR / name
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def secret(name: str) -> str | None:
    value = os.environ.get(name)
    if value is not None and value.strip() != "":
        return value.strip()
    return _read_secret_file(name)


def store_secret(name: str, value: str) -> None:
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(SECRETS_DIR, 0o700)
    except OSError:
        pass
    path = SECRETS_DIR / name
    path.write_text(value.strip() + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def ensure_data_path(generate: bool = False) -> str:
    value = secret("DATA_PATH")
    if value:
        if not DATA_PATH_RE.match(value):
            raise SystemExit("DATA_PATH must be 32+ url-safe characters")
        return value
    if not generate:
        raise SystemExit("DATA_PATH is not set")
    value = secrets.token_hex(24)
    store_secret("DATA_PATH", value)
    os.environ["DATA_PATH"] = value
    return value


def ensure_app_token(generate: bool = False) -> str | None:
    value = secret("APP_TOKEN")
    if value:
        return value
    if not generate:
        return None
    value = secrets.token_hex(24)
    store_secret("APP_TOKEN", value)
    os.environ["APP_TOKEN"] = value
    return value


def coerce(key: str, value):
    default = DEFAULTS.get(key)
    if isinstance(default, bool):
        if isinstance(value, str):
            return 1 if value.strip().lower() in ("1", "true", "on", "yes") else 0
        return 1 if value else 0
    if isinstance(default, int) and not isinstance(default, bool):
        return int(float(value))
    if isinstance(default, float):
        return float(value)
    return value


def merged(stored: dict | None = None) -> dict:
    out = dict(DEFAULTS)
    for key, value in (stored or {}).items():
        if key in DEFAULTS:
            try:
                out[key] = coerce(key, value)
            except (TypeError, ValueError):
                out[key] = value
        else:
            out[key] = value
    return out


class Config:
    def __init__(self):
        self.root = ROOT
        self.db_path = os.environ.get("DESK_DB", str(ROOT / "desk.db"))
        self.output_dir = Path(os.environ.get("DESK_OUTPUT", str(ROOT / "output")))
        self.log_dir = Path(os.environ.get("DESK_LOGS", str(ROOT / "logs")))
        self.web_dir = ROOT / "desk" / "web"
        self.preview = os.environ.get("DESK_PREVIEW", "") == "1"
        self.serve = os.environ.get("DESK_SERVE", "1") == "1"
        self.host = os.environ.get("DESK_HOST", "0.0.0.0")
        self.port = int(os.environ.get("PORT", os.environ.get("DESK_PORT", "8741")))
        self.tg_token = secret("TG_TOKEN")
        self.gh_token = secret("GH_TOKEN")
        self.typesafe_key = secret("TYPESAFE_API_KEY")
        self.owner_chat_id = secret("OWNER_CHAT_ID")
        self.gh_repo = os.environ.get("GH_REPO", "Nikoleles/grok-desk")
        self.vapid_private = secret("VAPID_PRIVATE_KEY")
        self.vapid_public = secret("VAPID_PUBLIC_KEY")

    def load_paths(self, generate: bool = False):
        self.data_path = ensure_data_path(generate=generate)
        self.app_token = ensure_app_token(generate=generate)
        return self
