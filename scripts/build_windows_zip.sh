#!/usr/bin/env bash
# OpenAI4S · build the Windows release package (.zip).
#
# WHAT THIS DOES NOT DO, AND WHY
# ------------------------------
# It does not produce a native Windows build. openai4s/platform_support.py
# refuses to start a kernel on win32 — the kernel spawns POSIX subprocesses, the
# R channel rides file descriptors 3 and 4 through a shell redirection, and the
# OS sandbox has no Windows backend — and that refusal is the frozen platform
# decision (docs/v02-decisions.md, 8.5), not an oversight for a packaging script
# to route around. Shipping an .exe that starts, looks fine, and then dies at
# the first cell would be the "warns and proceeds" failure the refusal exists to
# prevent, one release channel further downstream.
#
# So the Windows deliverable is the documented supported route made
# double-clickable: a Windows-side launcher plus the Linux bundle as its
# payload. On first run the launcher installs that bundle into the user's WSL2
# distribution, starts the daemon there, and opens the Windows browser at the
# forwarded localhost port. WSL2 reports as `linux`, which is a supported
# platform, so nothing is being worked around.
#
# The launcher sources live in scripts/windows/ as real files rather than
# heredocs, so they can be read, diffed and checked on their own; this script
# stages them with the line endings each side needs.
#
#   bash scripts/build_windows_zip.sh                       # x86_64
#   ARCH=aarch64 bash scripts/build_windows_zip.sh          # Windows on ARM
#   PAYLOAD=dist/OpenAI4S-0.1.0-linux-x86_64.tar.gz bash scripts/build_windows_zip.sh
#
# With no PAYLOAD, an already-built Linux bundle in dist/ is reused and one is
# built if absent — the Windows package is a wrapper around that exact artifact,
# so the two can never describe different builds.
set -euo pipefail

APP_NAME="OpenAI4S"

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
BUILD="${BUILD:-$REPO_ROOT/.build/windows}"
DIST="${DIST:-$REPO_ROOT/dist}"
SOURCES="$REPO_ROOT/scripts/windows"

VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$REPO_ROOT/openai4s/__init__.py" | head -1)"
if [ -z "$VERSION" ]; then
  echo "error: could not read __version__ from openai4s/__init__.py" >&2
  exit 1
fi

# ARCH names the *payload* architecture, because that is what has to match the
# WSL2 distribution: Windows on ARM runs an aarch64 kernel, so an x86_64 payload
# would install and then fail to execute a single binary.
ARCH="${ARCH:-x86_64}"
case "$ARCH" in
  x86_64|amd64)  ARCH="x86_64";  WINARCH="x86_64" ;;
  aarch64|arm64) ARCH="aarch64"; WINARCH="arm64"  ;;
  *) echo "error: unsupported arch: $ARCH" >&2; exit 1 ;;
esac

STAGE="$BUILD/stage"
PKGNAME="$APP_NAME-$VERSION-windows-$WINARCH"
PKG="$STAGE/$PKGNAME"
ZIP="$DIST/$PKGNAME.zip"

echo "== OpenAI4S Windows packaging =="
echo "  repo    : $REPO_ROOT"
echo "  version : $VERSION"
echo "  payload : linux-$ARCH  (the WSL2 distribution's architecture)"
echo "  build   : $BUILD"
echo "  zip out : $ZIP"

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"
  else shasum -a 256 "$1"
  fi
}

# Windows text files ship CRLF. It is not cosmetic for the .cmd: cmd.exe reads
# batch files line by line and a bare LF file has broken `goto`/label handling
# on some Windows builds, which fails in a way that looks like the script is
# skipping steps rather than erroring.
to_crlf() {
  awk '{ sub(/\r$/, ""); printf "%s\r\n", $0 }' "$1" > "$2"
}

# bootstrap.sh must arrive LF-only. A CRLF shell script fails inside WSL with a
# mangled interpreter line ("/bin/sh^M: bad interpreter"), and it fails on the
# user's machine, not here.
to_lf() {
  awk '{ sub(/\r$/, ""); printf "%s\n", $0 }' "$1" > "$2"
}

# --------------------------------------------------------------------------- #
# 0) the payload — the Linux bundle this package installs into WSL2
# --------------------------------------------------------------------------- #
echo "-- [0/5] resolving the Linux payload --"
PAYLOAD="${PAYLOAD:-$DIST/$APP_NAME-$VERSION-linux-$ARCH.tar.gz}"
if [ ! -f "$PAYLOAD" ]; then
  echo "   no payload at $PAYLOAD — building it"
  ARCH="$ARCH" bash "$REPO_ROOT/scripts/build_linux_bundle.sh"
fi
if [ ! -f "$PAYLOAD" ]; then
  echo "error: the Linux payload was not produced at $PAYLOAD" >&2
  exit 1
fi
PAYLOAD_NAME="$(basename "$PAYLOAD")"
case "$PAYLOAD_NAME" in
  "$APP_NAME-$VERSION-linux-$ARCH.tar.gz") ;;
  *)
    # A payload built from another tree or another version would produce a
    # package whose launcher unpacks a directory it then cannot find.
    echo "error: payload $PAYLOAD_NAME does not match this build" >&2
    echo "       expected $APP_NAME-$VERSION-linux-$ARCH.tar.gz" >&2
    exit 1
    ;;
esac
echo "   payload : $PAYLOAD ($(du -h "$PAYLOAD" | cut -f1))"

# --------------------------------------------------------------------------- #
# 1) clean & skeleton
# --------------------------------------------------------------------------- #
echo "-- [1/5] cleaning & creating package skeleton --"
rm -rf "$BUILD"
mkdir -p "$PKG/wsl" "$PKG/payload" "$DIST"

# --------------------------------------------------------------------------- #
# 2) launcher, with the line endings each side needs
# --------------------------------------------------------------------------- #
echo "-- [2/5] staging the launcher --"
for name in OpenAI4S.cmd openai4s.ps1; do
  if [ ! -f "$SOURCES/$name" ]; then
    echo "error: missing launcher source $SOURCES/$name" >&2
    exit 1
  fi
  to_crlf "$SOURCES/$name" "$PKG/$name"
done
if [ ! -f "$SOURCES/bootstrap.sh" ]; then
  echo "error: missing $SOURCES/bootstrap.sh" >&2
  exit 1
fi
to_lf "$SOURCES/bootstrap.sh" "$PKG/wsl/bootstrap.sh"
chmod +x "$PKG/wsl/bootstrap.sh"

printf '%s\r\n' "$VERSION" > "$PKG/VERSION"
cp "$REPO_ROOT/LICENSE" "$PKG/LICENSE"

# --------------------------------------------------------------------------- #
# 3) the payload and its digest. The digest travels with the package because the
#    launcher re-checks it inside WSL: the archive crosses the 9p/DrvFs boundary
#    on install, where a short read yields a truncated file rather than an
#    error.
# --------------------------------------------------------------------------- #
echo "-- [3/5] staging the payload --"
cp "$PAYLOAD" "$PKG/payload/$PAYLOAD_NAME"
if [ -f "$PAYLOAD.sha256" ]; then
  cp "$PAYLOAD.sha256" "$PKG/payload/$PAYLOAD_NAME.sha256"
else
  ( cd "$PKG/payload" && sha256_of "$PAYLOAD_NAME" > "$PAYLOAD_NAME.sha256" )
fi
# Recomputed rather than trusted: a stale sidecar next to a rebuilt tarball
# would make every install fail the integrity check for the wrong reason.
STAGED_DIGEST="$(cd "$PKG/payload" && sha256_of "$PAYLOAD_NAME" | cut -d' ' -f1)"
RECORDED_DIGEST="$(cut -d' ' -f1 < "$PKG/payload/$PAYLOAD_NAME.sha256")"
if [ "$STAGED_DIGEST" != "$RECORDED_DIGEST" ]; then
  echo "error: the payload checksum sidecar does not match the payload" >&2
  echo "       recorded $RECORDED_DIGEST, actual $STAGED_DIGEST" >&2
  exit 1
fi
echo "   payload sha256: $STAGED_DIGEST"

# --------------------------------------------------------------------------- #
# 4) first-launch note
# --------------------------------------------------------------------------- #
echo "-- [4/5] writing the first-launch note --"
NOTE_SRC="$BUILD/READ_ME_FIRST.txt"
cat > "$NOTE_SRC" <<NOTE
OpenAI4S $VERSION - first launch on Windows
===========================================

1. Double-click OpenAI4S.cmd.

   The first run installs the bundled Linux app into your WSL2 distribution
   (about 1 GB unpacked, no download), starts the local server, and opens
   http://127.0.0.1:8760/ in your browser. The launcher obtains the secure
   sign-in URL from \`openai4s url\`; the credential is removed from the address
   bar after the browser receives its local cookie. Later runs just start it.

   Set your LLM provider + API key in the UI (Customize -> Models). No API key
   is shipped with this package.

2. This package runs OpenAI4S inside WSL2, on purpose.

   Native Windows is not supported and the program refuses to start a kernel
   there: it spawns POSIX subprocesses, the R channel rides file descriptors 3
   and 4 through a shell redirection, and the sandbox has no Windows backend.
   A build that started anyway would leave you to discover the problem from a
   half-working analysis. WSL2 reports as Linux, which is supported, so this is
   the same program every other platform runs - not an emulation of it.

   If you do not have WSL2 yet, the launcher tells you the exact command
   (\`wsl --install -d Ubuntu-24.04\`, from an Administrator PowerShell) and
   stops. WSL1 is refused.

3. Where things are.

   Your data      : \\\\wsl\$\\<distro>\\home\\<you>\\.openai4s
   Logs           : ~/.openai4s/logs/app.out   (inside WSL)
   The app itself : ~/.openai4s/app/           (inside WSL)

   Nothing is written outside your WSL home directory and this folder.

4. The command line.

   OpenAI4S.cmd status      is the daemon up?
   OpenAI4S.cmd url         print a fresh secure browser URL
   OpenAI4S.cmd stop        stop the daemon
   OpenAI4S.cmd setup       create the conda envs, including the R kernel
   OpenAI4S.cmd --help      everything else

   The installer also creates ~/.local/bin/openai4s inside WSL. Open a new
   Ubuntu terminal (or run \`. ~/.profile\`) and the same commands work there:

     openai4s serve --port 8760 --no-browser --detached

5. Choosing a distribution.

   The default WSL2 distribution is used. To pick another, set
   OPENAI4S_WSL_DISTRO before launching:

     set OPENAI4S_WSL_DISTRO=Ubuntu-24.04
     OpenAI4S.cmd

6. The sandbox.

   WSL2 and working bubblewrap 0.8.0 or newer are required. The launcher tests
   the same lifecycle, IPC, UTS, and network namespace flags used by real Cells
   before installation and starts the daemon with OPENAI4S_KERNEL_SANDBOX=enforce.
   Ubuntu 24.04 ships a suitable version:

     sudo apt update && sudo apt install -y bubblewrap

7. The Python science stack is bundled and works offline: numpy, pandas, scipy,
   matplotlib, scikit-learn, rdkit, scanpy and the single-cell stack, umap and
   numba. The R kernel is not bundled - add it with \`OpenAI4S.cmd setup\`.

8. Network downloads after installation.

   The bundled app itself installs offline. Later pip/Conda package downloads
   default to the Tsinghua mirrors in this Windows/WSL launcher. Override with
   OPENAI4S_WSL_PYPI_INDEX or OPENAI4S_WSL_CONDA_MIRROR.

   To use a local proxy, set OPENAI4S_WSL_PROXY before launch. In WSL NAT mode,
   Windows localhost is not WSL localhost; either enable mirrored networking
   and use http://127.0.0.1:7897, or expose the proxy on the Windows host
   gateway. See docs/windows-wsl.md in the installed source tree.
NOTE
to_crlf "$NOTE_SRC" "$PKG/READ ME FIRST.txt"
rm -f "$NOTE_SRC"

# --------------------------------------------------------------------------- #
# 5) zip it up
# --------------------------------------------------------------------------- #
echo "-- [5/5] building the zip --"
rm -f "$ZIP" "$ZIP.sha256"
if ! command -v zip >/dev/null 2>&1; then
  echo "error: 'zip' is not installed" >&2
  exit 1
fi
# -X drops the platform extras (uid/gid, macOS finder attributes); combined with
# COPYFILE_DISABLE it stops a macOS host emitting a __MACOSX shadow tree that
# unzips beside the package on Windows. The launcher and notes compress; the
# payload is an already-gzipped tarball, so a second pass over 380 MB of it buys
# nothing and costs minutes — it is stored instead.
( cd "$STAGE" && COPYFILE_DISABLE=1 zip -q -r -X -9 "$ZIP" "$PKGNAME" -x "$PKGNAME/payload/*" )
( cd "$STAGE" && COPYFILE_DISABLE=1 zip -q -r -X -0 "$ZIP" "$PKGNAME/payload" )

( cd "$DIST" && sha256_of "$(basename "$ZIP")" > "$(basename "$ZIP").sha256" )

echo
echo "== DONE =="
echo "  dir  : $(du -sh "$PKG" | cut -f1)   $PKG"
echo "  zip  : $(du -h "$ZIP" | cut -f1)   $ZIP"
echo "  sha  : $(cut -d' ' -f1 "$ZIP.sha256")"
