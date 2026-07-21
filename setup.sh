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

# 3) enable the git pre-commit hook (black · isort · ruff) for contributors.
#    Runs before the optional conda step below: a failed env solve must not cost
#    a contributor their git hook — the control .venv is already complete here.
uv run pre-commit install >/dev/null 2>&1 && echo "· pre-commit hook installed" || true

# 4) optionally build the comprehensive, persistent Python + R kernel envs
if [ "$kernel_envs_mode" != "none" ]; then
  setup_args=(setup --profile standard)
  if [ "$kernel_envs_mode" = "update" ]; then
    setup_args+=(--update)
  fi

  if uv run openai4s "${setup_args[@]}"; then
    echo "· Python + R kernel environments ready"
  else
    echo "warn: kernel environments are incomplete (see above)." >&2
    echo "      the control .venv is ready — ./start.sh still works, and the" >&2
    echo "      agent falls back to the base kernel. retry with:" >&2
    echo "        uv run openai4s ${setup_args[*]}" >&2
    exit 1
  fi
fi

echo "· setup complete — launch with ./start.sh"
