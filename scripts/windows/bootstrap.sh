#!/bin/sh
# OpenAI4S · the half of the Windows launcher that runs INSIDE the WSL2 distro.
#
# openai4s.ps1 does the Windows-side work — finding a WSL2 distro, translating
# paths, opening the browser — and hands everything that touches the Linux
# filesystem to this script. That split is not cosmetic: composing a POSIX
# command line inside PowerShell means two layers of quoting over paths that
# routinely contain spaces (`C:\Users\Some Name\Downloads\...`), and the failure
# mode is a half-executed command, not a syntax error.
#
# MUST stay LF-only. A CRLF shell script fails inside WSL with a mangled
# interpreter line, and scripts/verify_windows_zip.py fails the build if the
# packaged copy ever gains a carriage return.
#
#   bootstrap.sh install <tarball> <sha256> <dirname>
#   bootstrap.sh serve   <dirname> [host] [port]
#   bootstrap.sh cli     <dirname> [args...]
set -eu

ACTION="${1:-}"
if [ -z "$ACTION" ]; then
  echo "usage: bootstrap.sh <install|serve|cli> ..." >&2
  exit 2
fi
shift

DATA_DIR="${OPENAI4S_DATA_DIR:-$HOME/.openai4s}"
APP_ROOT="$DATA_DIR/app"

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
install)
  TARBALL="${1:?install needs the payload path}"
  EXPECTED="${2:?install needs the expected sha256}"
  DIRNAME="${3:?install needs the bundle directory name}"
  APP="$APP_ROOT/$DIRNAME"
  MARKER="$APP/.installed"

  if [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$EXPECTED" ] && [ -x "$APP/bin/openai4s" ]; then
    echo "already-installed $APP"
    exit 0
  fi

  if [ ! -f "$TARBALL" ]; then
    echo "the payload is not readable from inside WSL: $TARBALL" >&2
    exit 1
  fi

  # The payload crosses the 9p/DrvFs boundary between the Windows filesystem and
  # the distro. A short read there produces a truncated archive rather than an
  # error, so the digest is checked before anything is unpacked — an app that
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
  export OPENAI4S_HOST OPENAI4S_PORT

  # Every descriptor is redirected and the job is fully detached. wsl.exe waits
  # for the handles it inherited, so a background process that still holds this
  # shell's stdout keeps the Windows-side launcher blocked forever — the daemon
  # would be running and the app would look hung. setsid where it exists, plain
  # nohup where util-linux is absent.
  if command -v setsid >/dev/null 2>&1; then
    setsid nohup "$APP/bin/openai4s" serve >>"$DATA_DIR/logs/app.out" 2>&1 </dev/null &
  else
    nohup "$APP/bin/openai4s" serve >>"$DATA_DIR/logs/app.out" 2>&1 </dev/null &
  fi
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
