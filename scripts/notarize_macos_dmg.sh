#!/usr/bin/env bash
# OpenAI4S · notarize a Developer-ID-signed macOS disk image.
#
# Order, fail closed (a failing step is a failing run):
#   credential precheck → codesign the DMG → notarytool submit --wait
#   → stapler staple → stapler validate → spctl assess
#
# This is the only place the release workflow talks to Apple's notary.
# `build_macos_dmg.sh` signs the .app (Developer ID or ad-hoc) and never
# submits. Default unit tests never invoke this script against the notary
# service: they cover `--precheck` (credentials) and the ticket / digest /
# omission states the pipeline reads afterwards.
#
# Credentials — one complete set, checked before any Apple service is
# contacted:
#   OPENAI4S_MACOS_SIGNING_IDENTITY
#   plus either
#     APPLE_ID + APPLE_TEAM_ID + APPLE_NOTARY_PASSWORD
#   or
#     APPLE_API_KEY_ID + APPLE_API_ISSUER + APPLE_API_KEY_PATH|APPLE_API_KEY
#
# `macos_asset=omit` (the workflow default) is the remedy when the set is
# incomplete: do not upload a preview DMG. Requesting notarized without the
# secrets is a hard failure, not a silent omission. A schema-2 macOS build
# receipt reads stapler/spctl/post-staple evidence from the sibling
# `.codesign.json`; without a success receipt the pipeline requires DMG
# count zero.
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

usage() {
  echo "usage: $0 [--precheck] [<image.dmg>]" >&2
  exit 2
}

PRECHECK=0
DMG=""
for arg in "$@"; do
  case "$arg" in
    --precheck) PRECHECK=1 ;;
    -h|--help) usage ;;
    *)
      if [ -n "$DMG" ]; then
        usage
      fi
      DMG="$arg"
      ;;
  esac
done

python3 "$REPO_ROOT/scripts/describe_macos_image.py" --check-notary-credentials

if [ "$PRECHECK" -eq 1 ]; then
  echo "notary credentials: ready"
  exit 0
fi

if [ -z "${DMG}" ] || [ ! -f "$DMG" ]; then
  echo "error: disk image not found: ${DMG:-<missing>}" >&2
  exit 1
fi

IDENTITY="${OPENAI4S_MACOS_SIGNING_IDENTITY:?OPENAI4S_MACOS_SIGNING_IDENTITY is required}"

echo "-- [1/5] codesigning ${DMG} --"
codesign --force --sign "$IDENTITY" --timestamp --options runtime "$DMG"
codesign --verify --deep --strict "$DMG"

echo "-- [2/5] notarytool submit --wait --"
if [ -n "${APPLE_API_KEY_ID:-}" ] && [ -n "${APPLE_API_ISSUER:-}" ]; then
  KEY_PATH="${APPLE_API_KEY_PATH:-}"
  if [ -z "$KEY_PATH" ]; then
    if [ -z "${APPLE_API_KEY:-}" ]; then
      echo "error: APPLE_API_KEY_PATH or APPLE_API_KEY is required for the API-key set" >&2
      exit 1
    fi
    KEY_PATH="$(mktemp "${TMPDIR:-/tmp}/AuthKey.XXXXXX.p8")"
    trap 'rm -f "$KEY_PATH"' EXIT
    printf '%s\n' "$APPLE_API_KEY" > "$KEY_PATH"
  fi
  xcrun notarytool submit "$DMG" --key "$KEY_PATH" --key-id "$APPLE_API_KEY_ID" \
    --issuer "$APPLE_API_ISSUER" --wait
else
  xcrun notarytool submit "$DMG" \
    --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
    --password "$APPLE_NOTARY_PASSWORD" --wait
fi

echo "-- [3/5] stapler staple --"
xcrun stapler staple "$DMG"

echo "-- [4/5] stapler validate --"
xcrun stapler validate "$DMG"

echo "-- [5/5] spctl assess --"
spctl --assess --type open --context context:primary-signature -vv "$DMG"

POST_STAPLE="$(shasum -a 256 "$DMG" | awk '{print $1}')"
echo "post_staple_sha256=$POST_STAPLE"
