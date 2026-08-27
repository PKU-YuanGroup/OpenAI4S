#!/usr/bin/env bash
# OpenAI4S · build a self-contained Linux .tar.gz for release.
#
# Same strategy as scripts/build_macos_dmg.sh, and for the same reason: this
# project's kernel spawns its worker via
#   subprocess.Popen([sys.executable, "-u", worker.py], PYTHONPATH=<repo root>)
# so freezing (PyInstaller / cx_Freeze) would break it — sys.executable must stay
# a real interpreter and the package must stay loose .py files on disk. We embed
# a *relocatable* standalone CPython (python-build-standalone, via uv) plus the
# CORE_PACKAGES science stack from scripts/bundled_packages.txt, and ship the
# source tree intact. Every Path(__file__)-relative lookup (webui/, skills/,
# envs/, compute/templates/, worker.py) then resolves wherever the bundle is
# unpacked, and all writable state goes to ~/.openai4s (outside the tree).
#
# Not an AppImage: an AppImage is a squashfs mounted through FUSE, and the
# kernel's whole job is to spawn subprocesses under bubblewrap. Nesting a user
# namespace inside a FUSE mount is exactly the combination that fails on
# hardened and containerised hosts, and it would fail at *cell execution* time —
# long after the app looked like it started. A plain relocatable directory has
# no such interaction, and `install.sh` gives it the desktop integration an
# AppImage would have been chosen for.
#
#   bash scripts/build_linux_bundle.sh                 # native, on Linux
#   ARCH=aarch64 bash scripts/build_linux_bundle.sh    # pick the slice
#
# On a non-Linux host this cross-builds: uv fetches the target's relocatable
# CPython and the manylinux wheels, and the steps that would have to *run* the
# target interpreter are skipped, loudly. A cross-built image is a real image —
# nothing in it is host-specific — but it has not been executed, so the release
# job builds it on a Linux runner where verify_linux_bundle.py can.
set -euo pipefail

APP_NAME="OpenAI4S"
APP_NAME_LOWER="openai4s"   # the CLI name, matching the PyPI console script
PYSERIES="3.13"

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
BUILD="${BUILD:-$REPO_ROOT/.build/linux}"
DIST="${DIST:-$REPO_ROOT/dist}"

# The version is never re-declared here: scripts/verify_release_tag.py gates a
# release on pyproject.toml and openai4s/__init__.py agreeing with the tag, so
# the bundle has to read from that same source or it can silently drift.
VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$REPO_ROOT/openai4s/__init__.py" | head -1)"
if [ -z "$VERSION" ]; then
  echo "error: could not read __version__ from openai4s/__init__.py" >&2
  exit 1
fi

HOST_OS="$(uname -s)"
ARCH="${ARCH:-$(uname -m)}"
case "$ARCH" in
  x86_64|amd64)  ARCH="x86_64";  UV_TRIPLE="x86_64-unknown-linux-gnu"  ;;
  aarch64|arm64) ARCH="aarch64"; UV_TRIPLE="aarch64-unknown-linux-gnu" ;;
  *) echo "error: unsupported arch: $ARCH" >&2; exit 1 ;;
esac

# Cross-building is supported here — unlike the macOS image — because nothing is
# compiled: uv downloads a relocatable CPython built for the target and only
# manylinux wheels (--only-binary=:all:), and .pyc is a function of the Python
# *version*, not the machine. What a foreign host cannot do is execute the
# result, so the in-build smoke checks are skipped and the fact is printed
# rather than assumed away.
CROSS=0
if [ "$HOST_OS" != "Linux" ]; then
  CROSS=1
elif [ "$ARCH" != "$(uname -m | sed -e 's/^amd64$/x86_64/' -e 's/^arm64$/aarch64/')" ]; then
  CROSS=1
fi

STAGE="$BUILD/stage"
APPDIR="$STAGE/$APP_NAME-$VERSION-linux-$ARCH"
RUNTIME="$APPDIR/runtime"
SRC="$APPDIR/src"
TARBALL="$DIST/$APP_NAME-$VERSION-linux-$ARCH.tar.gz"

echo "== OpenAI4S Linux packaging =="
echo "  repo    : $REPO_ROOT"
echo "  version : $VERSION"
echo "  arch    : $ARCH"
echo "  host    : $HOST_OS $(uname -m)$( [ "$CROSS" = 1 ] && echo '  (CROSS-BUILD)' )"
echo "  build   : $BUILD"
echo "  tar out : $TARBALL"

# sha256sum on Linux, shasum on macOS. Printed digest is what a downloader
# checks the transfer against, so it is not optional on either host.
sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"
  else shasum -a 256 "$1"
  fi
}

# --------------------------------------------------------------------------- #
# 0) locate a relocatable standalone CPython (python-build-standalone via uv)
# --------------------------------------------------------------------------- #
echo "-- [0/10] locating standalone CPython $PYSERIES (linux-$ARCH) --"
PY_KEY="cpython-$PYSERIES-linux-$ARCH-gnu"
uv python install "$PY_KEY" >/dev/null 2>&1 || true
# Ask uv where it keeps them rather than assuming ~/.local/share/uv/python: CI
# images and UV_PYTHON_INSTALL_DIR both move it. -V so 3.13.9 does not sort
# above 3.13.13.
UV_PY_DIR="$(uv python dir 2>/dev/null || echo "$HOME/.local/share/uv/python")"
STDPY_ROOT="$(ls -d "$UV_PY_DIR"/cpython-"$PYSERIES".*-linux-"$ARCH"-gnu 2>/dev/null | sort -V | tail -1 || true)"
if [ -z "${STDPY_ROOT:-}" ] || [ ! -f "$STDPY_ROOT/bin/python3" ]; then
  echo "error: could not find a uv-managed standalone CPython $PYSERIES for linux-$ARCH" >&2
  echo "       looked under: $UV_PY_DIR/cpython-$PYSERIES.*-linux-$ARCH-gnu" >&2
  echo "       try: uv python install $PY_KEY" >&2
  exit 1
fi
echo "   using: $STDPY_ROOT"

# --------------------------------------------------------------------------- #
# 1) clean & skeleton
# --------------------------------------------------------------------------- #
echo "-- [1/10] cleaning & creating bundle skeleton --"
rm -rf "$BUILD"
mkdir -p "$RUNTIME" "$SRC" "$APPDIR/bin" "$DIST"

# --------------------------------------------------------------------------- #
# 2) copy the runtime (preserving symlinks) and prune non-runtime bulk
# --------------------------------------------------------------------------- #
echo "-- [2/10] copying embedded Python runtime --"
cp -R "$STDPY_ROOT/." "$RUNTIME/"
rm -f "$RUNTIME/BUILD" 2>/dev/null || true
rm -rf "$RUNTIME"/lib/python*/test "$RUNTIME"/lib/python*/idlelib \
       "$RUNTIME"/lib/python*/turtledemo "$RUNTIME"/lib/python*/lib2to3 2>/dev/null || true
find "$RUNTIME" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
RUNPY="$RUNTIME/bin/python3"
SITE="$(ls -d "$RUNTIME"/lib/python*/site-packages | head -1)"
if [ -z "$SITE" ]; then
  echo "error: the embedded runtime has no site-packages directory" >&2
  exit 1
fi
if [ "$CROSS" = 0 ]; then
  echo "   runtime python: $("$RUNPY" -c 'import sys;print(sys.version.split()[0])')"
else
  echo "   runtime python: $(basename "$STDPY_ROOT")  (not executed — cross-build)"
fi

# --------------------------------------------------------------------------- #
# 3) pre-bake the science stack into the runtime so the app runs the default
#    kernel env's workflows offline with no task-time install. Same manifest the
#    macOS image installs from and every verifier checks against, so "what we
#    install" and "what we check" cannot drift, and the two desktop platforms
#    cannot quietly ship different stacks.
# --------------------------------------------------------------------------- #
echo "-- [3/10] installing the science stack into the runtime (this is the slow step) --"
# python-build-standalone ships a PEP 668 marker; drop it on our private copy so
# pip may install into the bundled interpreter's own site-packages.
rm -f "$RUNTIME"/lib/python*/EXTERNALLY-MANAGED 2>/dev/null || true
MANIFEST="$REPO_ROOT/scripts/bundled_packages.txt"
if [ ! -f "$MANIFEST" ]; then
  echo "error: missing package manifest $MANIFEST" >&2
  exit 1
fi
# First column of every non-comment line = the pip name; a `skip_arch=` column
# drops the package for targets that have no wheel for it.
PKGS=$(awk -v arch="$ARCH" 'NF && $1 !~ /^#/ {
  keep = 1
  for (i = 3; i <= NF; i++) if ($i == "skip_arch=" arch) keep = 0
  if (keep) print $1
}' "$MANIFEST")
echo "   bundling $(printf '%s\n' "$PKGS" | grep -c .) packages from $(basename "$MANIFEST")"
if [ "$CROSS" = 0 ]; then
  "$RUNPY" -m pip install --upgrade --no-warn-script-location pip >/dev/null
  # shellcheck disable=SC2086
  "$RUNPY" -m pip install --no-warn-script-location $PKGS
else
  # The target interpreter cannot run here, so resolution is done by uv against
  # the declared platform. --only-binary=:all: is what makes this honest: a
  # package with no manylinux wheel fails the build instead of being silently
  # built for the *host* and shipped as if it were the target's.
  #
  # One difference from the native path: --target puts third-party console
  # scripts under site-packages/bin rather than runtime/bin, so a cross-built
  # bundle does not put `f2py` and friends on a cell's PATH. Nothing in-tree
  # uses them, and the release job builds natively, but it is a real difference
  # between the two paths rather than a cosmetic one.
  # shellcheck disable=SC2086
  uv pip install \
    --python-platform "$UV_TRIPLE" \
    --python-version "$PYSERIES" \
    --only-binary=:all: \
    --target "$SITE" \
    $PKGS
fi
# The third-party test suites are dead weight in a shipped app — ~50MB of .py
# that nothing imports, plus the bytecode step 8 would then have to compile.
# Only directories literally named test/tests go: `testing` packages stay,
# because numpy.testing (and friends) are public API library code imports.
find "$SITE" \( -type d -name tests -o -type d -name test \) -prune -exec rm -rf {} + 2>/dev/null || true
# Drop pip's bytecode: it is timestamp-invalidated, and unpacking the tarball
# rewrites the .py mtimes, so every one of those .pyc would be treated as stale
# on the user's machine. Step 8 recompiles with hash-based, never-revalidated
# bytecode instead.
find "$RUNTIME" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# --------------------------------------------------------------------------- #
# 4) copy the source tree intact (loose .py) — every relative lookup depends
#    on openai4s/ + openai4s_compute_provider/ + skills/ + envs/ being siblings
# --------------------------------------------------------------------------- #
echo "-- [4/10] copying source tree --"
rsync -a \
  --exclude '.git' --exclude '.venv' --exclude '.build' --exclude 'dist' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache' \
  --exclude '*.egg-info' --exclude '.env' --exclude '.env.*' --exclude '!.env.example' \
  --exclude '*.db' --exclude '.DS_Store' --exclude 'tests' --exclude 'readme-gifs-hd' \
  --exclude '.claude' \
  "$REPO_ROOT/openai4s" "$REPO_ROOT/openai4s_compute_provider" \
  "$REPO_ROOT/openai4s_worker_runtime" \
  "$REPO_ROOT/envs" "$REPO_ROOT/skills" "$REPO_ROOT/workflows" \
  "$REPO_ROOT/scripts" "$REPO_ROOT/docs" \
  "$SRC/"
cp "$REPO_ROOT/README.md" "$REPO_ROOT/README_zh.md" "$REPO_ROOT/LICENSE" \
   "$REPO_ROOT/.env.example" "$REPO_ROOT/pyproject.toml" "$SRC/" 2>/dev/null || true
cp "$REPO_ROOT/LICENSE" "$APPDIR/LICENSE"
printf '%s\n' "$VERSION" > "$APPDIR/VERSION"

# --------------------------------------------------------------------------- #
# 4b) pip's *site* config for the embedded interpreter. This must land AFTER the
#     science stack is installed (it would otherwise redirect that install too).
#
#     It is the only way to reach the pip invocations that come from inside a
#     Cell: the kernel builds its child environment from an allowlist and strips
#     every PIP_* variable, so an env-based redirect covers the daemon and misses
#     `host.bash("pip install …")` — which the system prompt actively suggests.
#     A system-wide unpack (/opt) is not writable by the user running the app,
#     so without this the first in-cell install dies on a permission error.
# --------------------------------------------------------------------------- #
echo "-- [4b/10] writing pip site config --"
cat > "$RUNTIME/pip.conf" <<'PIPCONF'
# Installs land in the user site (PYTHONUSERBASE, set by the app launcher to
# $OPENAI4S_DATA_DIR/pysite) instead of inside the unpacked bundle, which may
# well be read-only for the user running it.
[install]
user = true
break-system-packages = true
PIPCONF

# --------------------------------------------------------------------------- #
# 4c) a real `openai4s` CLI inside the bundle. Without it the bundle ships no
#     command line at all, and the documented way to add the R kernel
#     (`openai4s setup`) is unreachable for anyone who only downloaded the
#     tarball. Self-locating, so a symlink into ~/.local/bin works.
# --------------------------------------------------------------------------- #
echo "-- [4c/10] writing the bundled CLI --"
cat > "$APPDIR/bin/$APP_NAME_LOWER" <<'CLI'
#!/bin/bash
# OpenAI4S CLI — the same entry point as the PyPI console script, resolved out of
# the unpacked bundle. Symlink me into ~/.local/bin to get `openai4s` on your PATH.
set -e
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  case "$SOURCE" in /*) ;; *) SOURCE="$DIR/$SOURCE" ;; esac
done
APPDIR="$(cd -P "$(dirname "$SOURCE")/.." && pwd)"
export PYTHONPATH="$APPDIR/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUSERBASE="${PYTHONUSERBASE:-${OPENAI4S_DATA_DIR:-$HOME/.openai4s}/pysite}"
exec "$APPDIR/runtime/bin/python3" -m openai4s "$@"
CLI
chmod +x "$APPDIR/bin/$APP_NAME_LOWER"

# --------------------------------------------------------------------------- #
# 5) launcher. exec replaces the shell so the process the desktop session tracks
#    as the app *is* the python server — closing it delivers SIGTERM to it, and
#    cmd_serve's SIGTERM handler shuts down cleanly.
# --------------------------------------------------------------------------- #
echo "-- [5/10] writing launcher --"
cat > "$APPDIR/$APP_NAME" <<'LAUNCHER'
#!/bin/bash
# OpenAI4S launcher — starts the local daemon + web UI and opens the browser.
set -e
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  case "$SOURCE" in /*) ;; *) SOURCE="$DIR/$SOURCE" ;; esac
done
APPDIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
PY="$APPDIR/runtime/bin/python3"
SRC="$APPDIR/src"

export PYTHONPATH="$SRC${PYTHONPATH:+:$PYTHONPATH}"
export OPENAI4S_DATA_DIR="${OPENAI4S_DATA_DIR:-$HOME/.openai4s}"
export OPENAI4S_HOST="${OPENAI4S_HOST:-127.0.0.1}"
export OPENAI4S_PORT="${OPENAI4S_PORT:-8760}"
export MPLBACKEND="Agg"

# The embedded runtime's bin/ MUST come first. A .desktop launch hands the app a
# minimal PATH, and the kernel passes PATH straight through to every cell
# subprocess (kernel/environment.py) — so without this, `python` and `pip` may
# not resolve at all inside a shipped app, even though the system prompt tells
# the model to shell out to them. A selected conda env still prepends its own
# bin/ ahead of this. The distro paths are kept so a user-installed Rscript and
# bubblewrap stay discoverable.
export PATH="$APPDIR/runtime/bin:/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin${PATH:+:$PATH}"

# On-demand installs must not write into the unpacked bundle: a system-wide
# install directory is not writable for a normal user, and a bundle unpacked
# read-only would fail outright. `$APPDIR/runtime/pip.conf` (pip's site-level
# config, written at build time) redirects every invocation of the bundled pip
# to the user site — including the ones from inside a Cell, which the kernel's
# env allowlist would strip PIP_* from. This variable is what makes that user
# site *ours* rather than the machine-global ~/.local, which is shared with
# every other CPython 3.13 on the box and would shadow the conda envs. The
# kernel worker allowlists PYTHONUSERBASE, so it resolves the same directory.
export PYTHONUSERBASE="$OPENAI4S_DATA_DIR/pysite"

mkdir -p "$OPENAI4S_DATA_DIR/logs"
cd "$OPENAI4S_DATA_DIR"

URL="http://$OPENAI4S_HOST:$OPENAI4S_PORT/"

open_url() {
  # Ordered by how likely each is to exist on a headless-ish box; every one of
  # them is optional, and a server install with no browser must still serve.
  for opener in xdg-open gio open sensible-browser x-www-browser www-browser; do
    if command -v "$opener" >/dev/null 2>&1; then
      "$opener" "$URL" >/dev/null 2>&1 &
      return 0
    fi
  done
  echo "OpenAI4S is serving at $URL" >&2
  return 0
}

# A second launch of an app that is already serving should surface the running
# UI, not die with "daemon already running" behind a silent desktop launch.
if "$PY" - "$OPENAI4S_HOST" "$OPENAI4S_PORT" <<'PROBE'
import socket, sys
s = socket.socket()
s.settimeout(0.4)
sys.exit(0 if s.connect_ex((sys.argv[1], int(sys.argv[2]))) == 0 else 1)
PROBE
then
  open_url
  exit 0
fi

# Opened in the background: the server has to be the process this shell execs
# into, so the browser cannot be launched after it. A short delay lets the
# listener bind before the tab loads.
( sleep 2; open_url ) >/dev/null 2>&1 &

# -u: the log is the only way to diagnose a desktop-launched daemon, and block
# buffering leaves it empty for exactly as long as anyone is looking at it.
exec "$PY" -u -m openai4s serve >>"$OPENAI4S_DATA_DIR/logs/app.out" 2>&1
LAUNCHER
chmod +x "$APPDIR/$APP_NAME"

# --------------------------------------------------------------------------- #
# 6) desktop entry + install/uninstall. The .desktop is shipped as a template
#    because `Exec` and `Icon` must be absolute paths and a relocatable tarball
#    does not know where it will be unpacked; install.sh substitutes the real
#    location. Shipping a pre-baked path would produce a menu entry that
#    launches nothing, which is worse than no entry at all.
# --------------------------------------------------------------------------- #
echo "-- [6/10] writing desktop entry, install.sh and uninstall.sh --"
mkdir -p "$APPDIR/share/applications"
cat > "$APPDIR/share/applications/$APP_NAME_LOWER.desktop.in" <<DESKTOP
[Desktop Entry]
Type=Application
Name=$APP_NAME
GenericName=Scientific research agent
Comment=Local Code-as-Action scientific research agent with a browser UI
Exec=@APPDIR@/$APP_NAME
Icon=@ICON@
Terminal=false
Categories=Science;Development;IDE;
Keywords=science;research;agent;python;r;notebook;llm;
StartupNotify=true
DESKTOP

cat > "$APPDIR/install.sh" <<INSTALL
#!/bin/bash
# Per-user desktop integration for an unpacked OpenAI4S bundle. Touches nothing
# outside \$HOME and needs no root: it symlinks the CLI onto your PATH and
# registers a menu entry pointing back at *this* directory, wherever it is.
set -euo pipefail
APPDIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="\${XDG_BIN_HOME:-\$HOME/.local/bin}"
DATA_DIR="\${XDG_DATA_HOME:-\$HOME/.local/share}"

mkdir -p "\$BIN_DIR" "\$DATA_DIR/applications"
ln -sf "\$APPDIR/bin/$APP_NAME_LOWER" "\$BIN_DIR/$APP_NAME_LOWER"

ICON=""
for size in 512 256 128 64 48 32 16; do
  src="\$APPDIR/share/icons/hicolor/\${size}x\${size}/apps/$APP_NAME_LOWER.png"
  [ -f "\$src" ] || continue
  dest="\$DATA_DIR/icons/hicolor/\${size}x\${size}/apps"
  mkdir -p "\$dest"
  cp -f "\$src" "\$dest/$APP_NAME_LOWER.png"
  ICON="$APP_NAME_LOWER"
done
# Fall back to the absolute path of the largest shipped icon if the hicolor
# theme is unavailable; a themed name that resolves to nothing shows a blank
# tile, and a blank tile looks like a broken install.
if [ -z "\$ICON" ]; then
  ICON="\$APPDIR/share/icons/hicolor/512x512/apps/$APP_NAME_LOWER.png"
fi

sed -e "s|@APPDIR@|\$APPDIR|g" -e "s|@ICON@|\$ICON|g" \\
  "\$APPDIR/share/applications/$APP_NAME_LOWER.desktop.in" \\
  > "\$DATA_DIR/applications/$APP_NAME_LOWER.desktop"
chmod 644 "\$DATA_DIR/applications/$APP_NAME_LOWER.desktop"

command -v update-desktop-database >/dev/null 2>&1 && \\
  update-desktop-database "\$DATA_DIR/applications" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && \\
  gtk-update-icon-cache -qtf "\$DATA_DIR/icons/hicolor" >/dev/null 2>&1 || true

echo "Installed:"
echo "  CLI       : \$BIN_DIR/$APP_NAME_LOWER  -> \$APPDIR/bin/$APP_NAME_LOWER"
echo "  Menu entry: \$DATA_DIR/applications/$APP_NAME_LOWER.desktop"
echo
case ":\$PATH:" in
  *":\$BIN_DIR:"*) ;;
  *) echo "note: \$BIN_DIR is not on your PATH; add it to use \\\`$APP_NAME_LOWER\\\` directly." ;;
esac
echo "Run the app with: \$APPDIR/$APP_NAME    (or from your application menu)"
INSTALL
chmod +x "$APPDIR/install.sh"

cat > "$APPDIR/uninstall.sh" <<UNINSTALL
#!/bin/bash
# Undo install.sh. Leaves ~/.openai4s (your sessions, artifacts and settings)
# alone — deleting a scientist's data is not an uninstall step.
set -euo pipefail
BIN_DIR="\${XDG_BIN_HOME:-\$HOME/.local/bin}"
DATA_DIR="\${XDG_DATA_HOME:-\$HOME/.local/share}"

rm -f "\$BIN_DIR/$APP_NAME_LOWER"
rm -f "\$DATA_DIR/applications/$APP_NAME_LOWER.desktop"
for size in 512 256 128 64 48 32 16; do
  rm -f "\$DATA_DIR/icons/hicolor/\${size}x\${size}/apps/$APP_NAME_LOWER.png"
done
command -v update-desktop-database >/dev/null 2>&1 && \\
  update-desktop-database "\$DATA_DIR/applications" >/dev/null 2>&1 || true

echo "Removed the CLI symlink, the menu entry and the icons."
echo "Your data in \${OPENAI4S_DATA_DIR:-\$HOME/.openai4s} was left untouched."
UNINSTALL
chmod +x "$APPDIR/uninstall.sh"

# --------------------------------------------------------------------------- #
# 7) icons. The artwork is the committed brand mark, NOT something drawn here:
#    regenerate it with scripts/make_app_icon.py if the brand changes. The
#    .desktop declares an icon, so a missing one is a broken menu entry, not a
#    cosmetic warning — fail hard. Pillow comes from `uv run --with`, so this
#    needs no host Python environment and behaves the same on both hosts.
# --------------------------------------------------------------------------- #
echo "-- [7/10] slicing the icon ladder from the brand mark --"
ICON_SRC="$REPO_ROOT/assets/app-icon-1024.png"
if [ ! -f "$ICON_SRC" ]; then
  echo "error: missing $ICON_SRC — run: uv run python scripts/make_app_icon.py" >&2
  exit 1
fi
# --no-project: this must not resolve, sync or otherwise touch the repository's
# own dev environment just to resize a PNG.
uv run --quiet --no-project --with pillow python - \
  "$ICON_SRC" "$APPDIR/share/icons/hicolor" "$APP_NAME_LOWER" <<'ICONS'
import sys
from pathlib import Path

from PIL import Image

source, root, name = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
image = Image.open(source).convert("RGBA")
for size in (16, 32, 48, 64, 128, 256, 512):
    target = root / f"{size}x{size}" / "apps"
    target.mkdir(parents=True, exist_ok=True)
    image.resize((size, size), Image.LANCZOS).save(target / f"{name}.png")
print(f"   icons: {len(list(root.rglob('*.png')))} sizes under {root.name}/")
ICONS

# --------------------------------------------------------------------------- #
# 8) precompile every .py in the bundle.
#
#    Without this the app writes __pycache__ into its own directory on first
#    run, which silently degrades to recompiling the entire stdlib + science
#    stack on *every* launch wherever the bundle is read-only — a /opt unpack
#    for a non-root user, or a shared multi-user install.
#
#    unchecked-hash: the .pyc records a source hash and Python never revalidates
#    it. Timestamp bytecode would be invalidated by the mtime rewrite that
#    unpacking the tarball performs, putting us right back to writing into the
#    bundle.
#
#    Bytecode is architecture-independent — the magic number tracks the CPython
#    *version* — so a cross-build compiles with a host interpreter of the same
#    series, and refuses if it cannot find one rather than shipping an
#    uncompiled bundle that would look fine until someone ran it.
# --------------------------------------------------------------------------- #
echo "-- [8/10] precompiling bytecode --"
COMPILER="$RUNPY"
if [ "$CROSS" = 1 ]; then
  uv python install "$PYSERIES" >/dev/null 2>&1 || true
  COMPILER="$(uv python find "$PYSERIES" 2>/dev/null || true)"
  if [ -z "$COMPILER" ] || [ ! -x "$COMPILER" ]; then
    echo "error: cross-build needs a host CPython $PYSERIES to emit matching bytecode" >&2
    echo "       try: uv python install $PYSERIES" >&2
    exit 1
  fi
  HOST_SERIES="$("$COMPILER" -c 'import sys;print("%d.%d" % sys.version_info[:2])')"
  if [ "$HOST_SERIES" != "$PYSERIES" ]; then
    echo "error: host compiler is CPython $HOST_SERIES but the bundle embeds $PYSERIES;" >&2
    echo "       the .pyc magic number would not match and every module would" >&2
    echo "       silently recompile on first run." >&2
    exit 1
  fi
  echo "   cross-compiling with host $COMPILER (CPython $HOST_SERIES)"
fi
# compileall exits non-zero when any single file fails to parse; third-party
# packages ship such files on purpose (py2 fixtures, templates), and one of them
# must not fail the build.
"$COMPILER" -m compileall -q -f --invalidation-mode unchecked-hash \
  "$RUNTIME/lib" "$SRC" >/dev/null 2>&1 || true
echo "   compiled: $(find "$APPDIR" -name '*.pyc' | wc -l | tr -d ' ') .pyc files"

# --------------------------------------------------------------------------- #
# 9) first-launch note + an in-build smoke of the thing users actually run
# --------------------------------------------------------------------------- #
echo "-- [9/10] writing the first-launch note --"
cat > "$APPDIR/READ ME — first launch.txt" <<NOTE
OpenAI4S $VERSION — first launch on Linux ($ARCH)
================================================

1. Nothing to install. Run the app straight out of this directory:

     ./$APP_NAME

   That starts a local server and opens http://127.0.0.1:8760/ in your browser.
   Set your LLM provider + API key in the UI (Customize -> Models). All data
   lives in ~/.openai4s.  Logs: ~/.openai4s/logs/app.out

2. Optional desktop integration (per-user, no root, touches only \$HOME):

     ./install.sh      # 'openai4s' on your PATH + an application-menu entry
     ./uninstall.sh    # undo it; your data is left alone

   Move this directory wherever you like first — everything in it is
   relocatable, and install.sh records wherever it ends up.

3. The sandbox. Cells run under bubblewrap when it is available. Install it
   (Debian/Ubuntu: 'apt install bubblewrap', Fedora: 'dnf install bubblewrap',
   Arch: 'pacman -S bubblewrap') or the default OPENAI4S_KERNEL_SANDBOX=auto
   will report a visibly degraded, unisolated kernel. Set it to 'enforce' to
   refuse to run at all without one.

4. The Python science stack is bundled and works offline:
   numpy/pandas/scipy/matplotlib/scikit-learn, plus rdkit (cheminformatics),
   scanpy + the single-cell stack, umap, and numba. No API key is shipped.

   The R kernel is NOT bundled. To add it, install micromamba/mamba/conda, then:

     ./bin/$APP_NAME_LOWER setup

5. This build is $ARCH only. On another architecture, install from PyPI:
   'pip install openai4s'.
NOTE

if [ "$CROSS" = 0 ]; then
  # The bundled CLI is what `openai4s setup` runs through, so a bundle whose
  # CLI does not start is broken even though every file is present.
  if "$APPDIR/bin/$APP_NAME_LOWER" --help 2>&1 | grep -q serve; then
    echo "   smoke: bundled CLI runs (\`openai4s --help\` lists 'serve')"
  else
    echo "error: the bundled CLI did not run out of the finished bundle" >&2
    exit 1
  fi
else
  echo "   smoke: skipped — this is a cross-build and the target cannot run here."
  echo "          scripts/verify_linux_bundle.py on a linux-$ARCH host is what"
  echo "          proves the embedded interpreter and CLI actually work."
fi

# --------------------------------------------------------------------------- #
# 10) tar it up
# --------------------------------------------------------------------------- #
echo "-- [10/10] building the tarball --"
rm -f "$TARBALL" "$TARBALL.sha256"
# Owner/group are normalised so the archive does not carry whoever's uid built
# it, and so two builds of the same tree differ only where the content does.
TAR_FLAGS=(--owner=0 --group=0)
if ! tar --version 2>/dev/null | head -1 | grep -qi 'gnu tar'; then
  # bsdtar. --no-mac-metadata and COPYFILE_DISABLE together stop it emitting an
  # AppleDouble `._<name>` sidecar for every file carrying an extended
  # attribute. Without them a cross-build on macOS produces an archive that
  # unpacks on Linux into two top-level directories — the bundle and a shadow
  # tree of `._` junk — which is not what anyone built and not what the
  # verifier will accept.
  TAR_FLAGS=(--uid 0 --gid 0 --numeric-owner --no-mac-metadata)
fi
COPYFILE_DISABLE=1 tar -C "$STAGE" "${TAR_FLAGS[@]}" -czf "$TARBALL" "$(basename "$APPDIR")"

# Publish the digest next to the archive: a GitHub Release asset is a bare file
# with no provenance of its own, so the checksum is the only thing a downloader
# can verify the transfer against.
( cd "$DIST" && sha256_of "$(basename "$TARBALL")" > "$(basename "$TARBALL").sha256" )

echo
echo "== DONE =="
echo "  dir  : $(du -sh "$APPDIR" | cut -f1)   $APPDIR"
echo "  tar  : $(du -h "$TARBALL" | cut -f1)   $TARBALL"
echo "  sha  : $(cut -d' ' -f1 "$TARBALL.sha256")"
if [ "$CROSS" = 1 ]; then
  echo
  echo "  NOTE: cross-built on $HOST_OS/$(uname -m). The image is complete, but"
  echo "        nothing in it has been executed. Run scripts/verify_linux_bundle.py"
  echo "        on a linux-$ARCH host before releasing it."
fi
