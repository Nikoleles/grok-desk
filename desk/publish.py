"""Build the GitHub Pages tree and force-push one orphan commit.

The token stays in the environment. It is not written into the worktree,
the remote URL, git config, or the log.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from desk.log import get_logger, redact

log = get_logger("publish")

ROOT_FILES = ("index.html", "app.css", "app.js", "chart.js", "sw.js", "manifest.json")


def ensure_stage(stage: Path, web: Path):
    stage.mkdir(parents=True, exist_ok=True)
    (stage / "d").mkdir(exist_ok=True)
    index = stage / "d" / "index.html"
    if not index.exists():
        index.write_text("", encoding="utf-8")
    nojekyll = stage / ".nojekyll"
    if not nojekyll.exists():
        nojekyll.write_text("", encoding="utf-8")
    not_found = stage / "404.html"
    if not not_found.exists() or True:
        not_found.write_text(
            "<!doctype html><meta charset=\"utf-8\"><title>Not found</title>"
            "<body style=\"background:#000;color:#fff;font-family:-apple-system,sans-serif;"
            "padding:32px\">This path is not on the desk.</body>",
            encoding="utf-8",
        )
    if web.is_dir():
        for name in ROOT_FILES:
            src = web / name
            if src.is_file():
                shutil.copy2(src, stage / name)
        icons = web / "icons"
        if icons.is_dir():
            dest = stage / "icons"
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(icons, dest)
        mirror = stage / "web"
        if mirror.exists():
            shutil.rmtree(mirror)
        shutil.copytree(web, mirror, ignore=shutil.ignore_patterns("*.map"))


def write_snapshot(db, stage: Path, data_path: str, web: Path, clock):
    from desk.api import activity_payload, chart_payload, coin_payload, state_payload, write_json
    from desk.config import DATA_PATH_RE

    if not DATA_PATH_RE.match(data_path):
        raise ValueError("DATA_PATH is not a safe path segment")
    ensure_stage(stage, web)
    root = stage / "d" / data_path
    coin_dir = root / "coin"
    coin_dir.mkdir(parents=True, exist_ok=True)
    write_json(root / "state.json", state_payload(db, clock))
    write_json(root / "chart.json", chart_payload(db, clock))
    night = str(db.settings_map().get("desk.night_started") or "")[:10]
    write_json(root / "activity.json", activity_payload(db, night=night or None))
    for coin in db.coins():
        payload = coin_payload(db, coin["mint"], clock)
        if payload:
            write_json(coin_dir / f"{coin['mint']}.json", payload)


def pages_status(relpath: str, data_path: str, files: set[str]) -> int:
    """Plan B guard. /d/ and any path that misses DATA_PATH is a 404.

    d/index.html is published empty so a file server cannot list the folder.
    The desk still treats that URL as not found: it holds no snapshot.
    """
    rel = relpath.strip("/")
    if rel in ("",):
        return 200 if "index.html" in files else 404
    if rel == "d" or rel == "d/index.html":
        return 404
    if rel.startswith("d/"):
        rest = rel[2:]
        if not rest.startswith(data_path + "/"):
            return 404
        return 200 if rel in files else 404
    if rel in files:
        return 200
    return 404


def _askpass(path: Path):
    # Reads GH_TOKEN from the environment. The secret is not in this file.
    path.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  *[Uu]sername*) printf '%s\\n' \"x-access-token\" ;;\n"
        "  *) printf '%s\\n' \"$GH_TOKEN\" ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    path.chmod(0o700)


GIT_TIMEOUT_S = 90


def publish_tree(stage: Path, repo: str, token: str, branch: str = "gh-pages", runner=None):
    """Force-push an orphan single-commit branch. `token` is only placed in env."""
    if not token:
        raise RuntimeError("GH_TOKEN is not set")
    if token in str(stage) or token in repo or token in branch:
        raise RuntimeError("refusing to publish: token leaked into an argument")
    run = runner or subprocess.run
    with tempfile.TemporaryDirectory(prefix="desk-pages-") as tmp:
        tmp_path = Path(tmp)
        work = tmp_path / "work"
        ask = tmp_path / "askpass"
        _askpass(ask)
        if token in ask.read_text(encoding="utf-8"):
            raise RuntimeError("refusing to write the token into askpass")
        env = os.environ.copy()
        env["GH_TOKEN"] = token
        env["GIT_ASKPASS"] = str(ask)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env.pop("GIT_CURL_VERBOSE", None)
        env.pop("GIT_TRACE", None)
        remote = f"https://github.com/{repo}.git"
        if token in remote:
            raise RuntimeError("refusing to put the token in the remote")
        shutil.copytree(stage, work)
        for file in work.rglob("*"):
            if file.is_file() and token in file.read_text(encoding="utf-8", errors="ignore"):
                raise RuntimeError("refusing to publish: token landed in a file")
        commands = [
            ["git", "init", "-b", branch],
            ["git", "add", "-A"],
            ["git", "-c", "user.email=desk@localhost", "-c", "user.name=grok-desk",
             "commit", "-m", "publish"],
            ["git", "push", "--force", remote, f"HEAD:{branch}"],
        ]
        for cmd in commands:
            joined = " ".join(cmd)
            if token in joined:
                raise RuntimeError("refusing to pass the token on the command line")
            try:
                result = run(cmd, cwd=str(work), env=env, capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                log.info("git timeout cmd=%s", cmd[1] if len(cmd) > 1 else "?")
                raise RuntimeError(f"git timed out after {GIT_TIMEOUT_S}s")
            stdout = redact(getattr(result, "stdout", "") or "")
            stderr = redact(getattr(result, "stderr", "") or "")
            code = getattr(result, "returncode", 0)
            if code != 0:
                log.info("git failed rc=%s", code)
                raise RuntimeError(redact(f"git failed rc={code} {stderr[:400]}"))
            if stdout.strip():
                log.info("git ok")
    log.info("published %s %s", repo, branch)


def main():
    import sys

    from desk.config import Config
    from desk.db import DB
    from desk.log import register_secret
    from desk.util import Clock

    cfg = Config()
    cfg.load_paths(generate=False)
    register_secret(cfg.gh_token)
    if not cfg.gh_token:
        print("GH_TOKEN is not set", file=sys.stderr)
        sys.exit(1)
    db = DB(cfg.db_path)
    write_snapshot(db, cfg.output_dir, cfg.data_path, cfg.web_dir, Clock())
    publish_tree(cfg.output_dir, cfg.gh_repo, cfg.gh_token)
    print("published")


if __name__ == "__main__":
    main()
