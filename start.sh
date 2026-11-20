#!/usr/bin/env bash
# OpenAI4S · start the daemon + web UI from an already-configured environment.
# Run ./setup.sh first. The daemon prints the address it actually listens on
# (OPENAI4S_HOST / OPENAI4S_PORT, or --host / --port passed here); set your
# model in the UI.
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

# No address here: this script cannot know it. The port may come from --port,
# OPENAI4S_PORT or .env, and a fixed "http://127.0.0.1:8760/" printed above the
# daemon's own "listening at" line was simply wrong whenever it was changed.
echo "· starting OpenAI4S — its address is printed below once it is listening (set your model in Customize → Models)"
exec "${RUN[@]}" serve "$@"
