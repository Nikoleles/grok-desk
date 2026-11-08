"""Published files and the source tree do not contain secret values."""

import json
import secrets
from pathlib import Path

from desk.publish import pages_status, write_snapshot
from desk.tests.fakes import fresh_db

ROOT = Path(__file__).resolve().parents[2]


def test_snapshot_and_source_exclude_secrets(tmp_path, monkeypatch):
    tg = "tg" + secrets.token_hex(16)
    gh = "ghp_" + secrets.token_hex(16)
    app = "app" + secrets.token_hex(16)
    data = secrets.token_hex(20)
    monkeypatch.setenv("TG_TOKEN", tg)
    monkeypatch.setenv("GH_TOKEN", gh)
    monkeypatch.setenv("APP_TOKEN", app)
    monkeypatch.setenv("DATA_PATH", data)
    db, clock = fresh_db(tmp_path / "desk.db")
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>desk</title>", encoding="utf-8")
    stage = tmp_path / "stage"
    write_snapshot(db, stage, data, web, clock)
    blob = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in stage.rglob("*") if p.is_file())
    for secret in (tg, gh, app):
        assert secret not in blob
    # The secret path is the directory name, not a value inside a JSON document.
    state = json.loads((stage / "d" / data / "state.json").read_text(encoding="utf-8"))
    assert app not in json.dumps(state)
    assert tg not in json.dumps(state)
    source = ""
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", ".venv", "output", "logs", "__pycache__"} for part in path.parts):
            continue
        if path.suffix not in {".py", ".js", ".html", ".css", ".md", ".json"}:
            continue
        source += path.read_text(encoding="utf-8", errors="ignore")
    assert data not in source
    assert tg not in source and gh not in source and app not in source
    files = {str(p.relative_to(stage)) for p in stage.rglob("*") if p.is_file()}
    assert pages_status("/d/", data, files) == 404
    assert pages_status("/d/index.html", data, files) == 404
    assert pages_status(f"/d/{'c'*40}/state.json", data, files) == 404
    assert pages_status(f"/d/{data}/state.json", data, files) == 200
    assert (stage / "d" / "index.html").read_text(encoding="utf-8") == ""
    assert (stage / "404.html").is_file()
    assert (stage / "web" / "index.html").is_file()
