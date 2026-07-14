#!/usr/bin/env bash
# OpenAI4S · environment setup (uv)
# Creates the .venv and installs the project + tooling with uv. Optionally also
# creates the comprehensive Python and R kernel environments. Run this once,
# then launch the app with ./start.sh.
set -euo pipefail
cd "$(dirname "$0")"

usage() {
  cat <<'EOF'
Usage: ./setup.sh [OPTION]

  (no option)              install the lightweight control .venv
  --with-kernel-envs       also create the standard Python + R Conda envs
  --update-kernel-envs     create or update the standard Python + R Conda envs
  -h, --help               show this help

The kernel-env options require micromamba, mamba, or conda on PATH.
EOF
}

kernel_envs_mode="none"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --with-kernel-envs)
      kernel_envs_mode="create"
      ;;
    --update-kernel-envs)
      kernel_envs_mode="update"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if ! command -v uv >/dev/null 2>&1; then
  echo "error: 'uv' not found. Install it first:" >&2
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh    # or: pip install uv" >&2
  exit 1
fi

# 1) first-run config (secrets optional — set your model in the UI later)
if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo "· created .env from .env.example"
fi

# 2) create .venv and install: core + dev tools (pytest, pre-commit) + science extra
uv sync --locked --extra science
echo "· environment ready → .venv/"

# 3) optionally build the comprehensive, persistent Python + R kernel envs
case "$kernel_envs_mode" in
  create)
    uv run openai4s setup --profile standard
    echo "· Python + R kernel environments ready"
    ;;
  update)
    uv run openai4s setup --profile standard --update
    echo "· Python + R kernel environments synchronized"
    ;;
esac

# 4) enable the git pre-commit hook (black · isort · ruff) for contributors
uv run pre-commit install >/dev/null 2>&1 && echo "· pre-commit hook installed" || true

echo "· setup complete — launch with ./start.sh"
