"""The GitHub token never reaches a command line, a file, or a remote URL."""

from pathlib import Path

from desk.config import DEFAULTS
from desk.publish import publish_tree


class _Result:
    def __init__(self, cmd):
        self.returncode = 0
        self.stdout = ""
        self.stderr = ""
        self.cmd = cmd


def test_publish_commands_keep_the_token_in_the_environment(tmp_path):
    token = "ghp_SECRETVALUE1234567890abcd"
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (stage / "404.html").write_text("missing", encoding="utf-8")
    seen = []

    def runner(cmd, cwd, env, capture_output, text):
        seen.append((list(cmd), env, cwd))
        assert token not in " ".join(cmd)
        assert token not in cwd
        assert env.get("GH_TOKEN") == token
        assert "GIT_ASKPASS" in env
        ask = Path(env["GIT_ASKPASS"]).read_text(encoding="utf-8")
        assert token not in ask
        assert "GH_TOKEN" in ask
        return _Result(cmd)

    publish_tree(stage, "Nikoleles/grok-desk", token, runner=runner)
    assert seen
    push = seen[-1][0]
    assert push[0] == "git" and "push" in push
    assert "https://github.com/Nikoleles/grok-desk.git" in push
    assert token not in " ".join(push)
    assert DEFAULTS["desk.publish_interval_s"] == 360
    # Nothing in the stage gained the token.
    for file in stage.rglob("*"):
        if file.is_file():
            assert token not in file.read_text(encoding="utf-8", errors="ignore")
