#!/usr/bin/env bash
# Build the container image and prove the daemon actually works inside it.
#
#   bash scripts/container_smoke.sh
#
# A Dockerfile that no gate builds is not evidence of anything, and "the image
# built" is not the claim worth making — the claim is that the daemon boots
# unprivileged, answers its probe, refuses an unauthenticated caller, accepts
# the token it minted, and comes back after being killed. Each of those is a
# way containerization has broken this program specifically, so each is checked
# here rather than assumed.
#
# Needs a Docker daemon and nothing else. Runs the same on a laptop as in CI,
# which is the point: a gate only reachable from a runner cannot be debugged.
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="${OPENAI4S_SMOKE_IMAGE:-openai4s:container-smoke}"
CONTAINER="${OPENAI4S_SMOKE_CONTAINER:-openai4s-container-smoke}"
VOLUME="${OPENAI4S_SMOKE_VOLUME:-openai4s-container-smoke-data}"
# Not 8760: a developer running this has a daemon on that port already.
HOST_PORT="${OPENAI4S_SMOKE_PORT:-18760}"
BASE="http://127.0.0.1:${HOST_PORT}"

fail() {
  echo "container smoke: FAIL — $*" >&2
  echo "--- container logs ---" >&2
  docker logs "$CONTAINER" >&2 2>&1 || true
  exit 1
}

ok() { echo "container smoke: ok — $*"; }

cleanup() {
  docker rm --force "$CONTAINER" >/dev/null 2>&1 || true
  docker volume rm --force "$VOLUME" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup

# `python -c` rather than curl: the image ships no HTTP client, and neither
# should this script assume one on the host beyond the interpreter it needs
# anyway. Prints "<status> <body>" so a caller can assert on both.
probe() {
  python3 - "$1" "${2-}" <<'PY'
import sys, urllib.error, urllib.request

url, token = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "")
request = urllib.request.Request(url)
if token:
    request.add_header("Authorization", "Bearer " + token)
try:
    with urllib.request.urlopen(request, timeout=5) as response:
        print(response.status, response.read(400).decode("utf-8", "replace"))
except urllib.error.HTTPError as exc:
    print(exc.code, exc.read(400).decode("utf-8", "replace"))
except Exception as exc:  # connection refused while it is still booting
    print("000", exc)
PY
}

wait_for_health() {
  local deadline=$((SECONDS + ${1:-120}))
  while [ "$SECONDS" -lt "$deadline" ]; do
    case "$(probe "${BASE}/health")" in
      200\ *) return 0 ;;
    esac
    if [ -z "$(docker ps --quiet --filter "name=^/${CONTAINER}$")" ]; then
      fail "the container exited while we waited for /health"
    fi
    sleep 1
  done
  fail "/health did not answer 200 within ${1:-120}s"
}

echo "container smoke: building ${IMAGE}"
docker build --tag "$IMAGE" .

docker volume create "$VOLUME" >/dev/null

echo "container smoke: starting ${CONTAINER}"
docker run --detach \
  --name "$CONTAINER" \
  --publish "127.0.0.1:${HOST_PORT}:8760" \
  --volume "${VOLUME}:/data" \
  "$IMAGE" >/dev/null

wait_for_health 180
ok "/health answers 200 unauthenticated"

# The image must not run as root. This is the one property a reader cannot
# check from the Dockerfile alone, because a base image or a later layer can
# quietly put it back.
uid="$(docker exec "$CONTAINER" id -u)"
[ "$uid" = "1000" ] || fail "expected uid 1000 inside the container, got '${uid}'"
ok "runs as uid 1000"

# A wildcard bind makes the token mandatory and turns the Host-header
# allowlist off; the token is then the only control in front of the
# code-execution routes. If this ever answers 200, the image is publishing a
# shell.
unauthenticated="$(probe "${BASE}/api/v1/frames")"
case "$unauthenticated" in
  401\ *) ok "an unauthenticated API call is refused (401)" ;;
  *) fail "expected 401 from /api/v1/frames without a token, got: ${unauthenticated}" ;;
esac

token="$(docker exec "$CONTAINER" cat /data/access-token)"
[ -n "$token" ] || fail "no access token was minted under the data dir"
authenticated="$(probe "${BASE}/api/v1/frames" "$token")"
case "$authenticated" in
  200\ *) ok "the minted token authenticates" ;;
  *) fail "expected 200 from /api/v1/frames with the token, got: ${authenticated}" ;;
esac

# `openai4s url` is how an operator gets that token out of a running
# container, so it has to agree with the file.
url="$(docker exec "$CONTAINER" openai4s url)"
case "$url" in
  *"$token") ok "\`openai4s url\` hands back the same token" ;;
  *) fail "\`openai4s url\` printed '${url}', which does not carry the minted token" ;;
esac

# The science stack is what makes the default image worth its size. Import it
# rather than trusting that pip reported success.
docker exec "$CONTAINER" python -c "import numpy, pandas, matplotlib, sklearn" \
  || fail "the science stack does not import inside the image"
ok "numpy, pandas, matplotlib and scikit-learn import"

# --- the restart case ---------------------------------------------------------
#
# A container that cannot restart is not a deployment option. SIGKILL skips the
# daemon's teardown, so the pidfile survives on the volume — and the next
# container starts with a fresh PID namespace where that pid is very likely
# live again but is somebody else. The pidfile is forced to 1 here rather than
# left to chance: pid 1 always exists in the new container, so this asserts the
# identity check deterministically instead of hoping for a collision.
docker kill --signal=SIGKILL "$CONTAINER" >/dev/null
docker run --rm --user 0 --volume "${VOLUME}:/data" --entrypoint sh "$IMAGE" \
  -c 'printf 1 > /data/openai4s.pid' >/dev/null
docker start "$CONTAINER" >/dev/null
wait_for_health 120
ok "restarts after a SIGKILL that left a colliding pidfile behind"

# The token is the same one, i.e. the volume really is the state: a re-mint
# would have invalidated every cookie already issued.
token_after="$(docker exec "$CONTAINER" cat /data/access-token)"
[ "$token_after" = "$token" ] || fail "the access token changed across a restart"
ok "the access token survived the restart"

# --- graceful shutdown ---------------------------------------------------------
#
# Kubernetes sends SIGTERM and waits. The daemon turns it into its Ctrl-C path,
# closes every session slot and exits 0; tini forwards the signal and reports
# that code. A non-zero exit here means the pod would look like it crashed on
# every ordinary rollout.
docker stop --timeout 60 "$CONTAINER" >/dev/null
exit_code="$(docker inspect --format '{{.State.ExitCode}}' "$CONTAINER")"
[ "$exit_code" = "0" ] || fail "SIGTERM produced exit code ${exit_code}, expected 0"
ok "SIGTERM shuts the daemon down cleanly"

echo "container smoke: all checks passed"
