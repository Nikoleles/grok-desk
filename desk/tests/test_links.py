"""Every FOMO URL comes from links.py and carries r=savipww."""

import json
import re
from pathlib import Path

from desk import links
from desk.agents.executor import buy_text
from desk.api import state_payload
from desk.report import render_markdown, write_report
from desk.telegram import Push, render_message
from desk.tests.fakes import fresh_db
from desk.util import FakeClock

ROOT = Path(__file__).resolve().parents[2]
ALLOW = {
    ROOT / "desk" / "links.py",
    ROOT / "README.md",
}
SKIP_PARTS = {".git", ".venv", "output", "logs", "__pycache__", "uploads", "reports", ".secrets"}


def test_coin_url_and_unknown_chain():
    url = links.coin_url("Mint111", "solana")
    assert url == (
        "https://fomo.family/coin?address=Mint111"
        "&chainId=1399811149&r=savipww&source=share_link"
    )
    assert links.coin_url("Mint111", "base").endswith("chainId=8453&r=savipww&source=share_link")
    assert links.coin_url("Mint111", "not-a-chain") == links.SIGNUP_URL
    assert "/r/savipww" in links.SIGNUP_URL
    assert "r=savipww" in links.profile_url("@Desk")
    assert "bit.ly" not in url and "t.co" not in url


def test_push_button_is_the_exact_links_url():
    mint = "MintExact1111111111111111111111111111111"
    url = links.coin_url(mint, "solana")
    text = "PAPER · " + buy_text("JASON", 47.50, 23.75, 75, 6, 12)
    rendered = render_message(Push(
        text=text, url=url, url_label="Buy on FOMO",
        callbacks=[("Bought", f"bought:{mint}")], paper=True,
    ))
    assert rendered["text"].startswith("PAPER · ")
    button = rendered["reply_markup"]["inline_keyboard"][0][0]
    assert button["url"] == url
    assert button["url"] == links.coin_url(mint, "solana")
    assert "r=savipww" in button["url"]


def test_state_report_and_account_urls(tmp_path):
    db, clock = fresh_db(tmp_path / "desk.db")
    db.set_setting("desk.fomo_handle", "desk", who="test", ts="2026-11-08T00:00:00Z")
    state = state_payload(db, clock)
    assert state["signupUrl"] == links.SIGNUP_URL
    assert state["profileUrl"] == links.profile_url("desk")
    for value in (state["signupUrl"], state["profileUrl"]):
        assert "savipww" in value
    written = write_report(db, clock, tmp_path / "reports")
    text = Path(written["markdown"]).read_text(encoding="utf-8")
    blob = text + Path(written["json"]).read_text(encoding="utf-8")
    assert "New to FOMO? " + links.SIGNUP_URL in text
    for match in re.findall(r"https://fomo\.family\S*", blob):
        assert "savipww" in match
    md = render_markdown(written["data"])
    assert links.SIGNUP_URL in md


def test_repo_has_no_handbuilt_or_shortened_fomo_urls():
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        if "desk/tests" in str(path):
            continue
        if path.suffix not in {".py", ".js", ".html", ".css", ".md", ".json"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "bit.ly" in text or "tinyurl.com" in text or "t.co/" in text:
            offenders.append(f"shortener {path}")
        if "fomo.family" in text and path.resolve() not in ALLOW and "desk/tests" not in str(path):
            offenders.append(str(path))
        for match in re.findall(r"https?://\S*fomo\.family\S*", text):
            if "savipww" not in match:
                offenders.append(f"missing ref {path}: {match}")
    assert offenders == []
