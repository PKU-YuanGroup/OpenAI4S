#!/usr/bin/env bash
# OpenAI4S · build a self-contained macOS .app + .dmg for release.
#
# Strategy: this project's kernel spawns its worker via
#   subprocess.Popen([sys.executable, "-u", worker.py], PYTHONPATH=<repo root>)
# so freezing (py2app / PyInstaller) would break it — sys.executable must stay a
# real interpreter and the package must stay loose .py files on disk. We therefore
# embed a *relocatable* standalone CPython (python-build-standalone, via uv) plus
# the full CORE_PACKAGES science stack, and ship the source tree intact. Every
# Path(__file__)-relative lookup (webui/, skills/, envs/, compute/templates/,
# worker.py) then resolves correctly wherever the .app lives, and all writable
# state goes to ~/.openai4s (outside the read-only bundle).
#
# No Apple Developer credentials are used: the app is ad-hoc signed only (free),
# which is still required so Apple Silicon does not kill an unsigned binary.
set -euo pipefail

APP_NAME="OpenAI4S"
APP_NAME_LOWER="openai4s"   # the CLI name, matching the PyPI console script
PYSERIES="3.13"
BUNDLE_ID="com.openai4s.app"

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
BUILD="${BUILD:-$REPO_ROOT/.build/dmg}"
DIST="${DIST:-$REPO_ROOT/dist}"

# The version is never re-declared here: scripts/verify_release_tag.py gates a
# release on pyproject.toml and openai4s/__init__.py agreeing with the tag, so
# the DMG has to read from that same source or it can silently drift.
VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$REPO_ROOT/openai4s/__init__.py" | head -1)"
if [ -z "$VERSION" ]; then
  echo "error: could not read __version__ from openai4s/__init__.py" >&2
  exit 1
fi

# Host arch by default; overridable so a CI matrix can build both slices on the
# matching runner (macos-14 → arm64, macos-13 → x86_64). Cross-building is not
# supported: the science wheels are native.
ARCH="${ARCH:-$(uname -m)}"
case "$ARCH" in
  arm64|aarch64) ARCH="arm64";  PYARCH="aarch64" ;;
  x86_64|amd64)  ARCH="x86_64"; PYARCH="x86_64"  ;;
  *) echo "error: unsupported arch: $ARCH" >&2; exit 1 ;;
esac
if [ "$ARCH" != "$(uname -m | sed 's/^aarch64$/arm64/')" ]; then
  echo "error: cannot cross-build $ARCH on $(uname -m) — the bundled science" >&2
  echo "       wheels are native; run this on a $ARCH macOS runner." >&2
  exit 1
fi

STAGE="$BUILD/stage"
APP="$STAGE/$APP_NAME.app"
CONTENTS="$APP/Contents"
RES="$CONTENTS/Resources"
RUNTIME="$RES/runtime"
SRC="$RES/src"
DMG="$DIST/$APP_NAME-$VERSION-macos-$ARCH.dmg"

echo "== OpenAI4S macOS packaging =="
echo "  repo    : $REPO_ROOT"
echo "  version : $VERSION"
echo "  arch    : $ARCH"
echo "  build   : $BUILD"
echo "  dmg out : $DMG"

# --------------------------------------------------------------------------- #
# 0) locate a relocatable standalone CPython (python-build-standalone via uv)
# --------------------------------------------------------------------------- #
echo "-- [0/10] locating standalone CPython $PYSERIES ($ARCH) --"
uv python install "$PYSERIES" >/dev/null 2>&1 || true
# Ask uv where it keeps them rather than assuming ~/.local/share/uv/python: CI
# images and UV_PYTHON_INSTALL_DIR both move it. -V so 3.13.9 does not sort
# above 3.13.13.
UV_PY_DIR="$(uv python dir 2>/dev/null || echo "$HOME/.local/share/uv/python")"
STDPY_BIN="$(ls -d "$UV_PY_DIR"/cpython-"$PYSERIES"*-macos-"$PYARCH"-none/bin/python3 2>/dev/null | sort -V | tail -1 || true)"
if [ -z "${STDPY_BIN:-}" ] || [ ! -x "$STDPY_BIN" ]; then
  echo "error: could not find a uv-managed standalone CPython $PYSERIES for $PYARCH" >&2
  echo "       looked under: $UV_PY_DIR/cpython-$PYSERIES*-macos-$PYARCH-none/bin/python3" >&2
  exit 1
fi
STDPY_ROOT="$(cd "$(dirname "$STDPY_BIN")/.." && pwd)"
echo "   using: $STDPY_ROOT"

# --------------------------------------------------------------------------- #
# 1) clean & skeleton
# --------------------------------------------------------------------------- #
echo "-- [1/10] cleaning & creating bundle skeleton --"
rm -rf "$BUILD"
mkdir -p "$RUNTIME" "$SRC" "$CONTENTS/MacOS" "$DIST"

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
echo "   runtime python: $("$RUNPY" -c 'import sys;print(sys.version.split()[0])')"

# --------------------------------------------------------------------------- #
# 3) pre-bake the science stack into the runtime so the app runs the default
#    kernel env's workflows offline with no task-time install. The package set
#    is the pip-installable superset of envs/python.yml, kept in one manifest
#    (scripts/dmg_bundled_packages.txt) that the bundle verifier reads too, so
#    "what we install" and "what we check" cannot drift.
# --------------------------------------------------------------------------- #
echo "-- [3/10] installing the science stack into the runtime (this is the slow step) --"
# python-build-standalone ships a PEP 668 marker; drop it on our private copy so
# pip may install into the bundled interpreter's own site-packages.
rm -f "$RUNTIME"/lib/python*/EXTERNALLY-MANAGED 2>/dev/null || true
MANIFEST="$REPO_ROOT/scripts/dmg_bundled_packages.txt"
if [ ! -f "$MANIFEST" ]; then
  echo "error: missing package manifest $MANIFEST" >&2
  exit 1
fi
# First column of every non-comment line = the pip name.
PKGS=$(awk 'NF && $1 !~ /^#/ { print $1 }' "$MANIFEST")
echo "   bundling $(printf '%s\n' "$PKGS" | grep -c .) packages from $(basename "$MANIFEST")"
"$RUNPY" -m pip install --upgrade --no-warn-script-location pip >/dev/null
# shellcheck disable=SC2086
"$RUNPY" -m pip install --no-warn-script-location $PKGS
# The third-party test suites are dead weight in a shipped app — ~50MB of .py
# that nothing imports, plus the bytecode step 8 would then have to compile and
# sign for all of it. Only directories literally named test/tests go: `testing`
# packages stay, because numpy.testing (and friends) are public API that library
# code imports at runtime.
find "$RUNTIME"/lib/python*/site-packages \
  \( -type d -name tests -o -type d -name test \) -prune -exec rm -rf {} + 2>/dev/null || true

# Drop pip's bytecode: it is timestamp-invalidated, and copying the app out of
# the DMG rewrites the .py mtimes, so every one of those .pyc would be treated
# as stale on the user's machine. Step 8 recompiles the whole bundle with
# hash-based, never-revalidated bytecode instead.
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
  "$REPO_ROOT/envs" "$REPO_ROOT/skills" "$REPO_ROOT/scripts" "$REPO_ROOT/docs" \
  "$SRC/"
cp "$REPO_ROOT/README.md" "$REPO_ROOT/README_zh.md" "$REPO_ROOT/LICENSE" \
   "$REPO_ROOT/.env.example" "$REPO_ROOT/pyproject.toml" "$SRC/" 2>/dev/null || true

# --------------------------------------------------------------------------- #
# 4b) pip's *site* config for the embedded interpreter. This must land AFTER the
#     science stack is installed (it would otherwise redirect that install too).
#
#     It is the only way to reach the pip invocations that come from inside a
#     Cell: the kernel builds its child environment from an allowlist and strips
#     every PIP_* variable, so an env-based redirect covers the daemon and misses
#     `host.bash("pip install …")` — which the system prompt actively suggests.
#     Config in the bundle covers both, and is sealed by the signature.
# --------------------------------------------------------------------------- #
echo "-- [4b/10] writing pip site config --"
cat > "$RUNTIME/pip.conf" <<'PIPCONF'
# Installs land in the user site (PYTHONUSERBASE, set by the app launcher to
# $OPENAI4S_DATA_DIR/pysite) instead of inside the read-only, signed app bundle.
[install]
user = true
break-system-packages = true
PIPCONF

# --------------------------------------------------------------------------- #
# 4c) a real `openai4s` CLI inside the bundle. Without it the app ships no
#     command line at all, and the documented way to add the R kernel
#     (`openai4s setup`) is unreachable for anyone who only downloaded the DMG.
#     Self-locating, so a symlink into /usr/local/bin works.
# --------------------------------------------------------------------------- #
echo "-- [4c/10] writing the bundled CLI --"
cat > "$RUNTIME/bin/$APP_NAME_LOWER" <<'CLI'
#!/bin/bash
# OpenAI4S CLI — the same entry point as the PyPI console script, resolved out of
# the app bundle. Symlink me into /usr/local/bin to get `openai4s` on your PATH.
set -e
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  case "$SOURCE" in /*) ;; *) SOURCE="$DIR/$SOURCE" ;; esac
done
BIN="$(cd -P "$(dirname "$SOURCE")" && pwd)"
RES="$(cd -P "$BIN/../.." && pwd)"
export PYTHONPATH="$RES/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUSERBASE="${PYTHONUSERBASE:-${OPENAI4S_DATA_DIR:-$HOME/.openai4s}/pysite}"
exec "$BIN/python3" -m openai4s "$@"
CLI
chmod +x "$RUNTIME/bin/$APP_NAME_LOWER"

# --------------------------------------------------------------------------- #
# 5) launcher (CFBundleExecutable). exec replaces the shell so the process macOS
#    tracks as the app *is* the python server — Quit delivers SIGTERM to it, and
#    cmd_serve's SIGTERM handler shuts down cleanly.
# --------------------------------------------------------------------------- #
echo "-- [5/10] writing launcher --"
cat > "$CONTENTS/MacOS/$APP_NAME" <<'LAUNCHER'
#!/bin/bash
# OpenAI4S launcher — starts the local daemon + web UI and opens the browser.
set -e
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
PY="$RES/runtime/bin/python3"
SRC="$RES/src"

export PYTHONPATH="$SRC${PYTHONPATH:+:$PYTHONPATH}"
export OPENAI4S_DATA_DIR="${OPENAI4S_DATA_DIR:-$HOME/.openai4s}"
export OPENAI4S_HOST="${OPENAI4S_HOST:-127.0.0.1}"
export OPENAI4S_PORT="${OPENAI4S_PORT:-8760}"
export MPLBACKEND="Agg"

# The embedded runtime's bin/ MUST come first. Finder hands the app a bare
# /usr/bin:/bin PATH, and the kernel passes PATH straight through to every cell
# subprocess (kernel/environment.py) — so without this, `python` and `pip` do not
# resolve at all inside a shipped app and `python3` is Apple's 3.9.6 with no
# science stack, even though the system prompt tells the model to shell out to
# them. A selected conda env still prepends its own bin/ ahead of this.
# Homebrew/local are kept last so a user-installed Rscript stays discoverable.
export PATH="$RES/runtime/bin:/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin${PATH:+:$PATH}"

# On-demand installs must not write into the signed bundle: /Applications is not
# writable for a non-admin user, a build run straight from the DMG is read-only,
# and either way a write breaks the code signature. `$RUNTIME/pip.conf` (pip's
# site-level config, written at build time) redirects every invocation of the
# bundled pip to the user site — including the ones from inside a Cell, which the
# kernel's env allowlist would strip PIP_* from. This variable is what makes that
# user site *ours* rather than the machine-global ~/.local, which is shared with
# every other CPython 3.13 on the box and would shadow the conda envs. The kernel
# worker allowlists PYTHONUSERBASE, so it resolves the same directory.
export PYTHONUSERBASE="$OPENAI4S_DATA_DIR/pysite"

mkdir -p "$OPENAI4S_DATA_DIR/logs"
cd "$OPENAI4S_DATA_DIR"

URL="http://$OPENAI4S_HOST:$OPENAI4S_PORT/"

# A second launch of an app that is already serving should surface the running
# UI, not die with "daemon already running" behind a Finder bounce.
if "$PY" - "$OPENAI4S_HOST" "$OPENAI4S_PORT" <<'PROBE'
import socket, sys
s = socket.socket()
s.settimeout(0.4)
sys.exit(0 if s.connect_ex((sys.argv[1], int(sys.argv[2]))) == 0 else 1)
PROBE
then
  exec /usr/bin/open "$URL"
fi

# -u: the log is the only way to diagnose a Finder-launched daemon, and block
# buffering leaves it empty for exactly as long as anyone is looking at it.
exec "$PY" -u -m openai4s serve >>"$OPENAI4S_DATA_DIR/logs/app.out" 2>&1
LAUNCHER
chmod +x "$CONTENTS/MacOS/$APP_NAME"

# --------------------------------------------------------------------------- #
# 6) Info.plist + PkgInfo
# --------------------------------------------------------------------------- #
echo "-- [6/10] writing Info.plist --"
cat > "$CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundleExecutable</key><string>$APP_NAME</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>app.icns</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.developer-tools</string>
</dict>
</plist>
PLIST
printf 'APPL????' > "$CONTENTS/PkgInfo"

# --------------------------------------------------------------------------- #
# 7) app icon. The artwork is the committed brand mark (the bonded atoms around
#    the terminal block), NOT something drawn here: regenerate it with
#    scripts/make_app_icon.py if the brand changes. Info.plist declares the icon,
#    so a missing one is a broken bundle, not a cosmetic warning — fail hard.
# --------------------------------------------------------------------------- #
echo "-- [7/10] building the app icon from the brand mark --"
ICON_SRC="$REPO_ROOT/assets/app-icon-1024.png"
if [ ! -f "$ICON_SRC" ]; then
  echo "error: missing $ICON_SRC — run: uv run python scripts/make_app_icon.py" >&2
  exit 1
fi
ICONSET="$BUILD/$APP_NAME.iconset"
mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$ICON_SRC" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  d=$((s * 2))
  sips -z $d $d "$ICON_SRC" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$RES/app.icns"
echo "   icon written: $RES/app.icns"

# --------------------------------------------------------------------------- #
# 8) precompile every .py in the bundle — MUST happen before the signature, so
#    the bytecode is sealed by it rather than appearing afterwards.
#
#    Without this the app writes __pycache__ into its own bundle on first run,
#    which (a) invalidates the code signature the moment anyone uses the app and
#    (b) silently degrades to recompiling the entire stdlib + science stack on
#    *every* launch wherever the bundle is read-only — running straight from the
#    DMG, or /Applications for a non-admin user.
#
#    unchecked-hash: the .pyc records a source hash and Python never revalidates
#    it. Timestamp bytecode would be invalidated by the mtime rewrite that
#    copying the app out of the DMG performs, putting us right back to writing
#    into the bundle.
# --------------------------------------------------------------------------- #
echo "-- [8/10] precompiling bytecode (sealed into the signature) --"
# compileall exits non-zero when any single file fails to parse; third-party
# packages ship such files on purpose (py2 fixtures, templates), and one of them
# must not fail the build.
"$RUNPY" -m compileall -q -f --invalidation-mode unchecked-hash \
  "$RUNTIME/lib" "$SRC" >/dev/null 2>&1 || true
echo "   compiled: $(find "$APP" -name '*.pyc' | wc -l | tr -d ' ') .pyc files"

# --------------------------------------------------------------------------- #
# 9) ad-hoc codesign (no Apple Developer credentials; required on Apple Silicon)
# --------------------------------------------------------------------------- #
echo "-- [9/10] ad-hoc codesigning --"
codesign --force --deep --sign - --timestamp=none "$APP" 2>&1 | tail -2 || true
codesign --verify --deep "$APP" && echo "   codesign verify: OK" || echo "   codesign verify: WARN (ad-hoc)"

# --------------------------------------------------------------------------- #
# 10) build the DMG
# --------------------------------------------------------------------------- #
echo "-- [10/10] building DMG --"
ln -s /Applications "$STAGE/Applications"
ARCH_NOTE="Apple Silicon (arm64) only."
[ "$ARCH" = "x86_64" ] && ARCH_NOTE="Intel (x86_64) only."
cat > "$STAGE/READ ME — first launch.txt" <<NOTE
OpenAI4S $VERSION — first launch on macOS
=========================================

1. Drag OpenAI4S.app onto the Applications folder (shown here).

2. This build is ad-hoc signed but NOT notarized (no Apple Developer account),
   so Gatekeeper will refuse it on first launch. To open it:
     • macOS 15 (Sequoia) and newer: double-click, dismiss the warning, then go
       to System Settings → Privacy & Security and press "Open Anyway".
     • macOS 12-14: right-click (or Control-click) OpenAI4S.app → Open → Open.
     • Either version, from Terminal:
         xattr -dr com.apple.quarantine /Applications/OpenAI4S.app

3. Launching starts a local server and opens http://127.0.0.1:8760/ in your
   browser. Set your LLM provider + API key in the UI (Customize → Models).
   All data lives in ~/.openai4s.  Logs: ~/.openai4s/logs/app.out

4. The command line ships inside the app. To put it on your PATH:
     sudo ln -sf /Applications/OpenAI4S.app/Contents/Resources/runtime/bin/openai4s \\
       /usr/local/bin/openai4s
   The R kernel is NOT bundled. To add it, install micromamba/mamba/conda, then:
     openai4s setup

5. $ARCH_NOTE The default Python science stack is bundled and works offline:
   numpy/pandas/scipy/matplotlib/scikit-learn, plus rdkit (cheminformatics),
   scanpy + the single-cell stack, umap, and numba. No API key is shipped.
NOTE

rm -f "$DMG"
hdiutil create -volname "$APP_NAME $VERSION" -srcfolder "$STAGE" \
  -ov -format UDZO "$DMG" >/dev/null

# Publish the digest next to the image: a GitHub Release asset is a bare file
# with no provenance of its own, so the checksum is the only thing a downloader
# can verify the transfer against.
( cd "$DIST" && shasum -a 256 "$(basename "$DMG")" > "$(basename "$DMG").sha256" )
echo
echo "== DONE =="
echo "  app  : $(du -sh "$APP" | cut -f1)   $APP"
echo "  dmg  : $(du -h "$DMG" | cut -f1)   $DMG"
echo "  sha  : $(cut -d' ' -f1 "$DMG.sha256")"
