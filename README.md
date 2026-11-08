# Grok Desk

A private trading desk for Solana graduations. Five agents watch, audit, score, size, and guard. The only action is a Telegram message with a FOMO button. The desk never places an order, never asks for a FOMO password, and never touches a wallet.

Plan B hosting: this process writes JSON under `d/{DATA_PATH}/` and `publish.py` force-pushes a single-commit `gh-pages` branch. The iPhone app is static HTML. It learns `DATA_PATH` from the `#d=` fragment once and keeps it in `localStorage`.

## Run

Python 3.12.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./run.sh
```

`./run.sh` creates the venv if it is missing, installs dependencies, and starts the one process (agents, Telegram long polling, JSON writer, local preview).

On first boot, if `DATA_PATH` is unset, the process generates 48 hex characters and stores them in `.secrets/DATA_PATH` (mode 0600). It does not print the value. Read that file on the machine and open:

```text
http://127.0.0.1:8741/#d=<DATA_PATH>
```

The preview binds `0.0.0.0` on port **8741** (`PORT` or `DESK_PORT` overrides it). GitHub Pages is the phone-facing host. The local port is only the operator preview.

### Environment

| Name | Required | Purpose |
|---|---|---|
| `TG_TOKEN` | for Telegram | Bot token from BotFather. Without it the desk still runs; pushes are not sent. |
| `GH_TOKEN` | for Pages | Used only as a git askpass password. Never written to disk, the remote URL, or logs. |
| `GH_REPO` | no | Default `Nikoleles/grok-desk`. |
| `TYPESAFE_API_KEY` | no | Only used when `analyst.mode` is `jev`. Default mode is `rules`. |
| `OWNER_CHAT_ID` | no | Locks Telegram to this chat. If unset, the first `/start` becomes the owner. |
| `DATA_PATH` | generated | 32+ url-safe characters. Secret path for the JSON. Never committed. |
| `APP_TOKEN` | for plan A HTTP | Plan B does not send it. The static files do not contain it. |
| `RPC_URL` | no | Solana JSON-RPC. Default `https://api.mainnet-beta.solana.com`. |
| `DESK_PREVIEW` | no | Set to `1` to seed a PAPER night and skip the network agents. |
| `DESK_DB` | no | SQLite path. Default `desk.db`. |
| `DESK_OUTPUT` | no | Stage directory that is served and published. Default `output/`. |

Secrets may also live as single-line files in `.secrets/` (`TG_TOKEN`, `GH_TOKEN`, `DATA_PATH`, …). The shell exports them only when the variable is unset, and it does not echo them.

PAPER is the default. It starts at $1,000. Fills and exits are simulated. Every push starts with `PAPER · `.

## Other commands

```bash
.venv/bin/pytest          # acceptance tests
python health.py          # one status line; restarts the process if it is dead
python report.py          # writes reports/night.json and reports/night.md
python -m desk.publish    # needs GH_TOKEN; orphan gh-pages force-push
python -m desk.tests.test_replay --hours 2
```

`health.py` and `report.py` do not install timers. Publish runs from the process every `desk.publish_interval_s` seconds (default 360) when `GH_TOKEN` is set.

Telegram, owner only: `buy`, `sell`, `why`, `pause`, `resume`, `set stop to -8%`, `stack 1240`, `status`, `paper`, `live` then `LIVE`, `invite`, `report`, `test`.

## Layout

```text
desk/           main.py, agents, SQLite, links, fees, the web app, tests
run.sh          venv + process
health.py       status / restart
report.py       night report
desk/publish.py gh-pages publisher
```

FOMO URLs are built only in `desk/links.py`. Every one carries `r=savipww`. Raw vendor JSON is read only in `desk/fields.py`.

## Decisions where the prompt was cut off

- **Pool address** is the DexScreener `pairAddress`. The PumpPortal `pool` field is the label `pump-amm`.
- **Creator** always comes from RugCheck `$.creator`. The migration message does not carry one.
- **Layer 5** shows `lpLockedPct` as “locked”, never “burned”.
- **Layer 7** treats graph `links` (`source` / `target`) as edges. An empty list is “None”. A payload that omits the `links` key is “no data”.
- **Too young** keeps a coin in the waiting room until `min_pool_age_min` (10). Killing it on arrival would make the room useless. After `max_wait_min` (60) it is dropped.
- **Scanner kill order** after the truncated line: liquidity, top 10 (pool and curve excluded), 1h change, 24h runner, wait deadline, then promote.
- **Rules mode** (default) maps DexScreener numbers to the four scores: alive = (h1 buys + sells) / 1000, organic = min/max of the 5-minute buy and sell counts, chase = max(0, 5-minute change) / 100, flush = 5-minute volume / 1-hour volume. `jev` mode is implemented behind `analyst.mode` and is fail-closed without a key.
- **Stop tightness:** a smaller percent is tighter and can raise an open stop. A larger percent applies only to new positions.
- **Publish vs write:** local JSON is rewritten every 15s (`desk.write_interval_s`). The git push stays at 360s so GitHub Pages stays inside its build limit.
- **`/d/`** is an empty `index.html` so a file server cannot list the directory. The desk treats `/d/` and any path that misses `DATA_PATH` as not found.
- **test_replay** runs a 2-hour window on a fake clock and fake HTTP. It does not open PumpPortal.
