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

  if [ "$(id -u)" = 0 ]; then
    echo "OpenAI4S must run as your ordinary WSL user, not root" >&2
    exit 1
  fi

  for tool in awk grep tar flock; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      echo "required WSL tool is missing: $tool" >&2
      echo "use Ubuntu 24.04 or newer (wsl --install -d Ubuntu-24.04)" >&2
      exit 1
    fi
  done

  if ! command -v bwrap >/dev/null 2>&1; then
    echo "bubblewrap $MIN_BWRAP_VERSION or newer is required for isolated cells" >&2
    echo "inside Ubuntu 24.04, run: sudo apt update && sudo apt install -y bubblewrap" >&2
    exit 10
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
      --unshare-ipc --unshare-uts --unshare-net --unshare-pid \
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

# Exit 11 means "this data directory holds no usable installation", which the
# Windows launcher answers by installing the payload instead of refusing.
NOT_INSTALLED=11

resolve_app() {
  REQUESTED="$1"
  if [ -L "$APP_ROOT/current" ]; then
    CURRENT="$(readlink "$APP_ROOT/current")"
    case "$CURRENT" in
      */*|''|*[!a-zA-Z0-9._-]*) echo "invalid installed application pointer" >&2; exit 1 ;;
    esac
    case "$REQUESTED:$CURRENT" in
      current:*|"$REQUESTED:$REQUESTED-"*) printf '%s\n' "$APP_ROOT/$CURRENT"; return ;;
    esac
  fi
  # Every payload is content-addressed as <bundle>-<digest>, so `$APP_ROOT/
  # $REQUESTED` is a path `install` never writes. Naming it here reported a
  # fully installed tree as "not installed" whenever the pointer was missing,
  # foreign or interrupted mid-`select_install` -- with the recovery scan
  # sitting unreachable four lines below. Scan the requested bundle's own
  # generations first, and only then, for `current`, any other installation.
  if [ "$REQUESTED" != current ]; then
    for CANDIDATE in "$APP_ROOT/$REQUESTED" "$APP_ROOT/$REQUESTED-"*; do
      if [ -x "$CANDIDATE/bin/openai4s" ]; then
        printf '%s\n' "$CANDIDATE"
        return
      fi
    done
    echo "OpenAI4S is not installed in $DATA_DIR" >&2
    exit "$NOT_INSTALLED"
  fi
  # Older installations predate the pointer. Do not install a new payload to
  # answer status/stop; use the one already owned by this user's data directory.
  for CANDIDATE in "$APP_ROOT"/OpenAI4S-*; do
    if [ -x "$CANDIDATE/bin/openai4s" ]; then
      printf '%s\n' "$CANDIDATE"
      return
    fi
  done
  echo "OpenAI4S is not installed in $DATA_DIR" >&2
  exit "$NOT_INSTALLED"
}

select_install() {
  SELECTED="$1"
  POINTER="$APP_ROOT/.current-$$"
  # -f, like install_cli_link's own `ln -sfn`: a `.current-<pid>` left by a
  # killed earlier run whose pid has since been recycled otherwise fails under
  # `set -e` on every launch, with no message naming the dotfile to delete.
  ln -sf "${SELECTED##*/}" "$POINTER"
  # -T is what stops `mv` moving the pointer *into* the directory `current`
  # resolves to, and it is a GNU extension. Where it is missing the rename is
  # still correct, just briefly absent -- and this whole action holds the
  # install lock, so no concurrent installer can observe the gap.
  mv -Tf "$POINTER" "$APP_ROOT/current" 2>/dev/null || {
    rm -f "$APP_ROOT/current"
    mv "$POINTER" "$APP_ROOT/current"
  }
  install_cli_link "$SELECTED"
}

# Which installed tree, if any, the recorded daemon is executing from. A build
# is immutable and content-addressed, so anything else is reclaimable -- but a
# running analysis still imports lazily out of its own tree.
running_app() {
  PIDFILE="$DATA_DIR/openai4s.pid"
  [ -r "$PIDFILE" ] || return 0
  RUNNING_PID="$(cat "$PIDFILE" 2>/dev/null || true)"
  case "$RUNNING_PID" in ''|*[!0-9]*) return 0 ;; esac
  readlink "/proc/$RUNNING_PID/exe" 2>/dev/null || true
}

# Immutable payloads are never overwritten, so without this every update left a
# full bundle behind -- inside a WSL VHDX, which does not shrink when files are
# finally deleted. Four releases is several GB, and the fifth install fails with
# a disk-full message about a disk this scheme filled.
prune_installs() {
  KEEP="$1"
  IN_USE="$(running_app)"
  for CANDIDATE in "$APP_ROOT"/OpenAI4S-*; do
    [ -d "$CANDIDATE" ] || continue
    [ "$CANDIDATE" != "$KEEP" ] || continue
    case "$IN_USE" in "$CANDIDATE"/*) continue ;; esac
    rm -rf -- "$CANDIDATE"
  done
}

case "$ACTION" in
prepare)
  if [ "$(id -u)" != 0 ] || ! command -v apt-get >/dev/null 2>&1; then
    echo "automatic preparation requires a Debian/Ubuntu WSL distribution and root" >&2
    exit 1
  fi
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y bubblewrap
  ;;

preflight)
  if [ -n "${1:-}" ] && [ "$(uname -m)" != "$1" ]; then
    echo "this package is for $1, but the WSL distribution uses $(uname -m)" >&2
    exit 1
  fi
  run_preflight
  ;;

install)
  TARBALL="${1:?install needs the payload path}"
  EXPECTED="${2:?install needs the expected sha256}"
  DIRNAME="${3:?install needs the bundle directory name}"
  case "$DIRNAME" in
    OpenAI4S-*) ;;
    *) echo "invalid bundle directory name" >&2; exit 1 ;;
  esac
  case "$DIRNAME" in */*|*[!a-zA-Z0-9._-]*) echo "invalid bundle directory name" >&2; exit 1 ;; esac
  case "$EXPECTED" in ''|*[!a-f0-9]*) echo "invalid payload digest" >&2; exit 1 ;; esac
  [ "${#EXPECTED}" -eq 64 ] || { echo "invalid payload digest" >&2; exit 1; }
  mkdir -p "$APP_ROOT"
  exec 9> "$APP_ROOT/.install.lock"
  flock -w 120 9 || { echo "another installation is still in progress; retry shortly" >&2; exit 1; }
  # Each payload is immutable and uniquely addressed. A running older daemon
  # retains its source/runtime paths even when a newer payload has the same version.
  APP="$APP_ROOT/$DIRNAME-$EXPECTED"
  MARKER="$APP/.installed"

  if [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$EXPECTED" ] && [ -x "$APP/bin/openai4s" ]; then
    configure_network "$APP" 0
    select_install "$APP"
    prune_installs "$APP"
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

  STAGING="$(mktemp -d "$APP_ROOT/.staging-XXXXXXXX")"
  cleanup_install() {
    case "$STAGING" in "$APP_ROOT"/.staging-*) rm -rf -- "$STAGING" ;; esac
  }
  trap cleanup_install EXIT
  trap 'exit 1' HUP INT TERM
  tar -xzf "$TARBALL" --no-same-owner -C "$STAGING"
  STAGED_APP="$STAGING/$DIRNAME"
  if [ ! -x "$STAGED_APP/bin/openai4s" ]; then
    echo "the payload did not unpack into $STAGED_APP" >&2
    exit 1
  fi
  INSTALL_APP="$APP"
  configure_network "$STAGED_APP" 1
  APP="$INSTALL_APP"
  # The marker is the *last* step, because it is what makes the next launch
  # take the already-installed fast path. Written first, an install that then
  # failed to write pip.conf (unwritable tree, ENOSPC) left a tree marked
  # complete: every later launch skipped the extraction, re-entered
  # configure_network with FRESH_INSTALL=0, and failed the same way with no
  # route back to a working install.
  printf '%s\n' "$EXPECTED" > "$STAGED_APP/.installed"
  if [ -e "$APP" ]; then
    # This path is the payload's own digest, so an existing tree here is the
    # same bytes -- incomplete, or damaged since. Refusing outright made that
    # unrepairable: the fast path rejects it, the re-extract lands, and every
    # later launch repeats both and refuses again, with `doctor` (which does
    # not delete app trees) as the only suggestion. Replace it, unless the
    # recorded daemon is still executing out of it.
    case "$(running_app)" in
      "$APP"/*)
        echo "a running OpenAI4S is using $APP; run OpenAI4S.cmd stop first" >&2
        exit 1
        ;;
    esac
    rm -rf -- "$APP"
  fi
  mv "$STAGED_APP" "$APP"
  select_install "$APP"
  prune_installs "$APP"
  echo "installed $APP"
  ;;

serve)
  DIRNAME="${1:?serve needs the bundle directory name}"
  HOST="${2:-127.0.0.1}"
  PORT="${3:-8760}"
  # The Windows launcher runs this action inside a hidden wsl.exe with no
  # stream capture, so anything printed before the exec below reaches no
  # console and -- until this redirect -- no file either: `resolve_app`'s
  # refusals, an invalid Fake-IP mode, an unreadable `.installed`. The launcher
  # then blamed localhost forwarding and pointed at an empty log.
  mkdir -p "$DATA_DIR/logs"
  exec >>"$DATA_DIR/logs/app.out" 2>&1
  APP="$(resolve_app "$DIRNAME")"
  if [ ! -x "$APP/bin/openai4s" ]; then
    echo "not installed: $APP" >&2
    exit 1
  fi
  configure_fake_ip_dns

  OPENAI4S_HOST="$HOST"
  OPENAI4S_PORT="$PORT"
  OPENAI4S_KERNEL_SANDBOX="${OPENAI4S_KERNEL_SANDBOX:-enforce}"
  OPENAI4S_NO_OPEN=1
  export OPENAI4S_HOST OPENAI4S_PORT OPENAI4S_KERNEL_SANDBOX OPENAI4S_NO_OPEN
  OPENAI4S_BUNDLE_ID="$(cat "$APP/.installed")"
  export OPENAI4S_BUNDLE_ID

  # The Windows launcher owns a hidden wsl.exe for this foreground process.
  # Detaching into systemd leaves WSL free to stop the whole distribution when
  # the last terminal exits, even while the scientist is using the browser.
  exec "$APP/bin/openai4s" serve \
    --host "$HOST" --port "$PORT" --no-browser
  ;;

cli)
  DIRNAME="${1:?cli needs the bundle directory name}"
  shift
  APP="$(resolve_app "$DIRNAME")"
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
