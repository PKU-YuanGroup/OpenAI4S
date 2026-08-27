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

  # Installed is not the same as usable. Exercise the lifecycle and namespace
  # flags emitted by wrap_bwrap_command(), rather than a stronger user/uid
  # configuration that the real scientific Cell never requests. `--new-session`
  # is the one deliberate difference: the runtime argv omits it because the
  # spawner owns the session, so probing it here asks a strict superset --
  # enough to prove the distribution can build the boundary, which is all this
  # preflight decides. The exact-argv guarantee belongs to the daemon's own
  # sandbox self-test, which does pass new_session=False.
  if ! bwrap --die-with-parent --new-session \
      --unshare-ipc --unshare-uts --unshare-net \
      --ro-bind / / --dev /dev --proc /proc -- /bin/true >/dev/null 2>&1; then
    echo "bubblewrap $BWRAP_VERSION is installed but cannot create the WSL2 sandbox" >&2
    echo "confirm this distribution is WSL2 with: wsl -l -v" >&2
    exit 1
  fi
  echo "preflight-ok WSL2 bubblewrap-$BWRAP_VERSION"
}

# Files this launcher rewrites carry this marker. A file without it was edited
# by the user (or shipped by the bundle) and is preserved, not clobbered on the
# next launch.
MANAGED_MARK="managed-by-openai4s-windows-launcher"

configure_network() {
  APP="$1"
  FRESH_INSTALL="${2:-0}"
  PYPI_INDEX="${OPENAI4S_PYPI_INDEX_URL:-}"
  CONDA_MIRROR="${OPENAI4S_CONDA_MIRROR:-}"
  PIP_CONF="$APP/runtime/pip.conf"

  PYPI_MODE="unchanged"
  if [ "$PYPI_INDEX" = "off" ]; then
    PYPI_MODE="official"
    PYPI_INDEX=""
  elif [ -n "$PYPI_INDEX" ]; then
    PYPI_MODE="mirror"
  fi

  if [ "$PYPI_MODE" = "mirror" ]; then
    case "$PYPI_INDEX" in
      http://*|https://*) ;;
      *) echo "invalid PyPI mirror URL: $PYPI_INDEX" >&2; exit 1 ;;
    esac
  fi

  # A fresh install always claims the file, even with no mirror selected. The
  # unmarked file is then the pristine bundle baseline, and leaving it unmarked
  # would make it permanently unclaimable: a later launch sees "no marker, not
  # fresh" and reports it user-managed, so setting OPENAI4S_WSL_PYPI_INDEX
  # afterwards would silently never take effect.
  if [ "$PYPI_MODE" != "unchanged" ] || [ "$FRESH_INSTALL" = "1" ]; then
    # This is pip's site config for the embedded interpreter. Environment-only
    # PIP_* settings do not reach a sandboxed Cell, so putting the mirror here
    # is what keeps later in-Cell installs off a direct public index.
    #
    # The bundle ships a build-time pip.conf that routes installs to the user
    # site and names no index; rewriting that one is this launcher's job. On a
    # fresh install the unmarked file is that pristine baseline and may be
    # claimed. On later launches, removing the marker transfers ownership to
    # the user, and the launcher preserves the whole file rather than guessing
    # which individual setting was intentional.
    if [ -f "$PIP_CONF" ] && ! grep -q "$MANAGED_MARK" "$PIP_CONF" 2>/dev/null \
        && [ "$FRESH_INSTALL" != "1" ]; then
      echo "note: $PIP_CONF is user-managed; leaving it unchanged" >&2
    else
      {
        printf '%s\n' \
          "# $MANAGED_MARK -- rewritten on every launch." \
          '# Set OPENAI4S_WSL_PYPI_INDEX in Windows to change the mirror, or to' \
          '# off to restore the official index. Direct edits here are preserved' \
          '# only if this marker line is removed.'
        if [ "$PYPI_MODE" = "mirror" ]; then
          printf '%s\n' '[global]' "index-url = $PYPI_INDEX" ''
        fi
        printf '%s\n' \
          '[install]' \
          'user = true' \
          'break-system-packages = true'
      } > "$PIP_CONF"
    fi
  fi

  if [ "$CONDA_MIRROR" = "off" ]; then
    CONDARC_FILE="$NETWORK_DIR/condarc"
    if [ -f "$CONDARC_FILE" ] && grep -q "$MANAGED_MARK" "$CONDARC_FILE" 2>/dev/null; then
      rm -f -- "$CONDARC_FILE"
    elif [ -f "$CONDARC_FILE" ]; then
      echo "note: $CONDARC_FILE is user-managed; leaving it unchanged" >&2
    fi
  elif [ -n "$CONDA_MIRROR" ]; then
    case "$CONDA_MIRROR" in
      http://*|https://*) ;;
      *) echo "invalid Conda mirror URL: $CONDA_MIRROR" >&2; exit 1 ;;
    esac
    mkdir -p "$NETWORK_DIR"
    CONDARC_FILE="$NETWORK_DIR/condarc"
    if [ -f "$CONDARC_FILE" ] && ! grep -q "$MANAGED_MARK" "$CONDARC_FILE" 2>/dev/null; then
      echo "note: $CONDARC_FILE is user-managed; leaving it unchanged" >&2
    else
      printf '%s\n' \
        "# $MANAGED_MARK -- rewritten on every launch." \
        '# Set OPENAI4S_WSL_CONDA_MIRROR in Windows to change the mirror, or to' \
        '# off to stop writing this file. Direct edits here are preserved only' \
        '# if this marker line is removed.' \
        'channels:' \
        '  - conda-forge' \
        '  - defaults' \
        "channel_alias: $CONDA_MIRROR/cloud" \
        'default_channels:' \
        "  - $CONDA_MIRROR/pkgs/main" \
        "  - $CONDA_MIRROR/pkgs/r" \
        "  - $CONDA_MIRROR/pkgs/msys2" \
        'show_channel_urls: true' > "$CONDARC_FILE"
    fi
  fi
}

is_fake_ip_address() {
  case "$1" in
    198.18.*|198.19.*) return 0 ;;
    *) return 1 ;;
  esac
}

configure_fake_ip_dns() {
  MODE="${OPENAI4S_FAKE_IP_DNS_MODE:-auto}"
  case "$MODE" in
    on|1|true|yes)
      OPENAI4S_ALLOW_FAKE_IP_DNS=1
      ;;
    off|0|false|no)
      OPENAI4S_ALLOW_FAKE_IP_DNS=0
      ;;
    auto|'')
      # Clash and similar Windows TUN proxies may put both their WSL DNS
      # listener and every synthetic public answer in RFC 2544's
      # 198.18.0.0/15 range. Require both signals before enabling the daemon's
      # narrow compatibility path. The Python guard still accepts that range
      # only for catalogued or explicitly approved domains and never for an IP
      # literal, loopback, metadata, or another private range.
      #
      # The local resolver check gates the network one, and that order is
      # load-bearing rather than stylistic: this function runs before every
      # `cli` action too, so an unconditional `getent` would put a live DNS
      # lookup of a third-party domain in front of `status`, `url` and `stop`
      # -- and block each of them for the full resolv.conf budget on exactly
      # the half-configured proxy this feature exists for. A machine with an
      # ordinary resolver now reads one local file and stops.
      RESOLV_CONF="${OPENAI4S_WSL_RESOLV_CONF:-/etc/resolv.conf}"
      RESOLVER=""
      PROBE=""
      if [ -r "$RESOLV_CONF" ]; then
        RESOLVER="$(awk '/^[[:space:]]*nameserver[[:space:]]+/ {print $2; exit}' "$RESOLV_CONF")"
      fi
      if is_fake_ip_address "$RESOLVER" && command -v getent >/dev/null 2>&1; then
        PROBE="$(getent ahostsv4 api.openalex.org 2>/dev/null | awk 'NR == 1 {print $1; exit}')"
      fi
      if is_fake_ip_address "$RESOLVER" && is_fake_ip_address "$PROBE"; then
        OPENAI4S_ALLOW_FAKE_IP_DNS=1
        echo "detected trusted WSL Fake-IP DNS; enabling restricted public-domain compatibility" >&2
      else
        OPENAI4S_ALLOW_FAKE_IP_DNS=0
      fi
      ;;
    *)
      echo "invalid OPENAI4S_FAKE_IP_DNS_MODE: $MODE (expected auto, on, or off)" >&2
      exit 2
      ;;
  esac
  export OPENAI4S_ALLOW_FAKE_IP_DNS
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
    configure_network "$APP" 0
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
  configure_network "$APP" 1
  install_cli_link "$APP"
  # The marker is the *last* step, because it is what makes the next launch
  # take the already-installed fast path. Written first, an install that then
  # failed to write pip.conf (unwritable tree, ENOSPC) left a tree marked
  # complete: every later launch skipped the extraction, re-entered
  # configure_network with FRESH_INSTALL=0, and failed the same way with no
  # route back to a working install.
  printf '%s\n' "$EXPECTED" > "$MARKER"
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
  configure_fake_ip_dns

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
  # The Fake-IP verdict only matters to a command that can make an outbound
  # request. `status`, `url` and `stop` cannot, and on the very machines this
  # feature exists for -- a Clash/TUN resolver in 198.18/15 -- probing first
  # would block each of them on a third-party lookup. `stop` in particular is
  # the command that has to work when DNS is the thing that is wedged. Anything
  # not named here still gets the probe, so a new subcommand fails safe.
  case "${1:-}" in
    status|url|stop|--help|-h|help) ;;
    *) configure_fake_ip_dns ;;
  esac
  exec "$APP/bin/openai4s" "$@"
  ;;

*)
  echo "unknown action: $ACTION" >&2
  exit 2
  ;;
esac
