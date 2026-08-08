#!/bin/sh
# OpenAI4S - the half of the Windows launcher that runs INSIDE the WSL2 distro.
#
# openai4s.ps1 does the Windows-side work -- finding a WSL2 distro, translating
# paths, opening the browser -- and hands everything that touches the Linux
# filesystem to this script. That split is not cosmetic: composing a POSIX
# command line inside PowerShell means two layers of quoting over paths that
# routinely contain spaces (`C:\Users\Some Name\Downloads\...`), and the failure
# mode is a half-executed command, not a syntax error.
#
# MUST stay LF-only. A CRLF shell script fails inside WSL with a mangled
# interpreter line, and scripts/verify_windows_zip.py fails the build if the
# packaged copy ever gains a carriage return.
#
#   bootstrap.sh preflight
#   bootstrap.sh install <tarball> <sha256> <dirname>
#   bootstrap.sh serve   <dirname> [host] [port]
#   bootstrap.sh cli     <dirname> [args...]
set -eu

ACTION="${1:-}"
if [ -z "$ACTION" ]; then
  echo "usage: bootstrap.sh <preflight|install|serve|cli> ..." >&2
  exit 2
fi
shift

DATA_DIR="${OPENAI4S_DATA_DIR:-$HOME/.openai4s}"
APP_ROOT="$DATA_DIR/app"
NETWORK_DIR="$DATA_DIR/network"
MIN_BWRAP_VERSION="0.8.0"

version_at_least() {
  awk -v have="$1" -v need="$2" 'BEGIN {
    split(have, h, "."); split(need, n, ".")
    for (i = 1; i <= 3; i++) {
      hv = h[i] + 0; nv = n[i] + 0
      if (hv > nv) exit 0
      if (hv < nv) exit 1
    }
    exit 0
  }'
}

run_preflight() {
  if ! grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
    echo "this Windows package must run inside WSL2" >&2
    exit 1
  fi

  for tool in awk grep tar; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      echo "required WSL tool is missing: $tool" >&2
      echo "use Ubuntu 24.04 or newer (wsl --install -d Ubuntu-24.04)" >&2
      exit 1
    fi
  done

  if ! command -v bwrap >/dev/null 2>&1; then
    echo "bubblewrap $MIN_BWRAP_VERSION or newer is required for isolated cells" >&2
    echo "inside Ubuntu 24.04, run: sudo apt update && sudo apt install -y bubblewrap" >&2
    exit 1
  fi
  BWRAP_VERSION="$(bwrap --version 2>/dev/null | awk '{print $NF}')"
  if [ -z "$BWRAP_VERSION" ] || ! version_at_least "$BWRAP_VERSION" "$MIN_BWRAP_VERSION"; then
    echo "bubblewrap $BWRAP_VERSION is too old; $MIN_BWRAP_VERSION or newer is required" >&2
    echo "install Ubuntu 24.04: wsl --install -d Ubuntu-24.04" >&2
    exit 1
  fi

  # Installed is not the same as usable. Exercise the same lifecycle and
  # namespace flags emitted by wrap_bwrap_command(), rather than a stronger
  # user/uid configuration that the real scientific Cell never requests.
  if ! bwrap --die-with-parent --new-session \
      --unshare-ipc --unshare-uts --unshare-net \
      --ro-bind / / --dev /dev --proc /proc -- /bin/true >/dev/null 2>&1; then
    echo "bubblewrap $BWRAP_VERSION is installed but cannot create the WSL2 sandbox" >&2
    echo "confirm this distribution is WSL2 with: wsl -l -v" >&2
    exit 1
  fi
  echo "preflight-ok WSL2 bubblewrap-$BWRAP_VERSION"
}

configure_network() {
  APP="$1"
  PYPI_INDEX="${OPENAI4S_PYPI_INDEX_URL:-}"
  CONDA_MIRROR="${OPENAI4S_CONDA_MIRROR:-}"

  if [ -n "$PYPI_INDEX" ]; then
    case "$PYPI_INDEX" in
      http://*|https://*) ;;
      *) echo "invalid PyPI mirror URL: $PYPI_INDEX" >&2; exit 1 ;;
    esac
    # This is pip's site config for the embedded interpreter. Environment-only
    # PIP_* settings do not reach a sandboxed Cell, so putting the mirror here
    # is what keeps later in-Cell installs off a direct public index.
    printf '%s\n' \
      '[global]' \
      "index-url = $PYPI_INDEX" \
      '' \
      '[install]' \
      'user = true' \
      'break-system-packages = true' > "$APP/runtime/pip.conf"
  fi

  if [ -n "$CONDA_MIRROR" ]; then
    case "$CONDA_MIRROR" in
      http://*|https://*) ;;
      *) echo "invalid Conda mirror URL: $CONDA_MIRROR" >&2; exit 1 ;;
    esac
    mkdir -p "$NETWORK_DIR"
    printf '%s\n' \
      'channels:' \
      '  - conda-forge' \
      '  - defaults' \
      "channel_alias: $CONDA_MIRROR/cloud" \
      'default_channels:' \
      "  - $CONDA_MIRROR/pkgs/main" \
      "  - $CONDA_MIRROR/pkgs/r" \
      "  - $CONDA_MIRROR/pkgs/msys2" \
      'show_channel_urls: true' > "$NETWORK_DIR/condarc"
  fi
}

install_cli_link() {
  APP="$1"
  BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
  CLI_LINK="$BIN_DIR/openai4s"
  mkdir -p "$BIN_DIR"
  if [ -e "$CLI_LINK" ] && [ ! -L "$CLI_LINK" ]; then
    echo "note: $CLI_LINK already exists and was not replaced" >&2
    return
  fi
  ln -sfn "$APP/bin/openai4s" "$CLI_LINK"
}

if [ -f "$NETWORK_DIR/condarc" ]; then
  CONDARC="$NETWORK_DIR/condarc"
  export CONDARC
fi

digest_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d' ' -f1
  else
    # No digest tool means the integrity check cannot be performed. Saying so is
    # the point: silently installing an unverified payload is the outcome this
    # check exists to prevent.
    echo "NO-DIGEST-TOOL"
  fi
}

case "$ACTION" in
preflight)
  run_preflight
  ;;

install)
  TARBALL="${1:?install needs the payload path}"
  EXPECTED="${2:?install needs the expected sha256}"
  DIRNAME="${3:?install needs the bundle directory name}"
  APP="$APP_ROOT/$DIRNAME"
  MARKER="$APP/.installed"

  if [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$EXPECTED" ] && [ -x "$APP/bin/openai4s" ]; then
    configure_network "$APP"
    install_cli_link "$APP"
    echo "already-installed $APP"
    exit 0
  fi

  if [ ! -f "$TARBALL" ]; then
    echo "the payload is not readable from inside WSL: $TARBALL" >&2
    exit 1
  fi

  # The payload crosses the 9p/DrvFs boundary between the Windows filesystem and
  # the distro. A short read there produces a truncated archive rather than an
  # error, so the digest is checked before anything is unpacked -- an app that
  # half-installed is far harder to diagnose than one that refused to.
  ACTUAL="$(digest_of "$TARBALL")"
  if [ "$ACTUAL" = "NO-DIGEST-TOOL" ]; then
    echo "no sha256sum/shasum in this distro; cannot verify the payload" >&2
    echo "install coreutils (Debian/Ubuntu: apt install coreutils) and retry" >&2
    exit 1
  fi
  if [ "$ACTUAL" != "$EXPECTED" ]; then
    echo "payload checksum mismatch: expected $EXPECTED, got $ACTUAL" >&2
    exit 1
  fi

  mkdir -p "$APP_ROOT"
  # Replace rather than overlay: unpacking a new version on top of an old tree
  # leaves whatever the new one dropped, and a stale .py next to a new one is
  # the sort of bug that only shows up in someone else's analysis.
  rm -rf "$APP"
  tar -xzf "$TARBALL" -C "$APP_ROOT"
  if [ ! -x "$APP/bin/openai4s" ]; then
    echo "the payload did not unpack into $APP" >&2
    exit 1
  fi
  printf '%s\n' "$EXPECTED" > "$MARKER"
  configure_network "$APP"
  install_cli_link "$APP"
  echo "installed $APP"
  ;;

serve)
  DIRNAME="${1:?serve needs the bundle directory name}"
  HOST="${2:-127.0.0.1}"
  PORT="${3:-8760}"
  APP="$APP_ROOT/$DIRNAME"
  if [ ! -x "$APP/bin/openai4s" ]; then
    echo "not installed: $APP" >&2
    exit 1
  fi
  mkdir -p "$DATA_DIR/logs"

  OPENAI4S_HOST="$HOST"
  OPENAI4S_PORT="$PORT"
  OPENAI4S_KERNEL_SANDBOX="${OPENAI4S_KERNEL_SANDBOX:-enforce}"
  OPENAI4S_NO_OPEN=1
  export OPENAI4S_HOST OPENAI4S_PORT OPENAI4S_KERNEL_SANDBOX OPENAI4S_NO_OPEN

  # Let the CLI own detachment and wait for its internal /health check. A bare
  # shell `setsid ... &` can be reaped when a non-interactive wsl.exe session
  # ends before the child reaches exec, leaving an empty log and a launcher that
  # waits on a daemon which never existed. The CLI redirects every descriptor,
  # creates a new POSIX session, and returns only after the service is healthy.
  "$APP/bin/openai4s" serve \
    --host "$HOST" --port "$PORT" --no-browser --detached
  echo "serving http://$HOST:$PORT/  (log: $DATA_DIR/logs/app.out)"
  ;;

cli)
  DIRNAME="${1:?cli needs the bundle directory name}"
  shift
  APP="$APP_ROOT/$DIRNAME"
  if [ ! -x "$APP/bin/openai4s" ]; then
    echo "not installed: $APP" >&2
    exit 1
  fi
  exec "$APP/bin/openai4s" "$@"
  ;;

*)
  echo "unknown action: $ACTION" >&2
  exit 2
  ;;
esac
