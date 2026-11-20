#!/usr/bin/env bash
# OpenAI4S · start the daemon + web UI from an already-configured environment.
# Run ./setup.sh first. The daemon prints the address it bound and opens a
# signed-in browser tab (set your model in Settings → Models).
set -euo pipefail
cd "$(dirname "$0")"

if [ -x .venv/bin/openai4s ]; then
  RUN=(.venv/bin/openai4s)
elif [ -x .venv/bin/python ]; then
  RUN=(.venv/bin/python -m openai4s)
else
  echo "no .venv found — run ./setup.sh first." >&2
  exit 1
fi

# No address here: OPENAI4S_HOST / OPENAI4S_PORT, .env and `--port` all change
# it, and this script cannot see all three. The daemon prints the one it bound.
# The bare address answers 401 without the sign-in link, so name the command.
echo "· starting OpenAI4S — the address is printed below. Sign-in link: ${RUN[*]} url"
exec "${RUN[@]}" serve "$@"
