#!/usr/bin/env bash
# Start the desk. Secrets are read from the environment or from .secrets/<NAME>
# and are never printed.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3.12}"
if [[ ! -x .venv/bin/python ]]; then
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -q -r requirements.txt
mkdir -p .secrets logs run reports output

if [[ -d .secrets ]]; then
  while IFS= read -r -d '' file; do
    name="$(basename "$file")"
    if [[ -z "${!name:-}" ]]; then
      value="$(tr -d '\r\n' < "$file")"
      export "${name}=${value}"
    fi
  done < <(find .secrets -maxdepth 1 -type f -print0)
fi

exec python -m desk.main
