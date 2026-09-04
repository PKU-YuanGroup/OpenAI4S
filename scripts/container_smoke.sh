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
# Needs a Docker daemon and a host `python3` (the probe speaks HTTP with it, so
# that nothing here depends on curl). Both are checked before the build, since
# discovering a missing interpreter after a five-minute image build — and
# reading it as "the daemon never became healthy" — is the wrong failure.
# Otherwise it runs the same on a laptop as in CI, which is the point: a gate
# only reachable from a runner cannot be debugged.
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

# --- preflight ----------------------------------------------------------------
command -v docker >/dev/null || { echo "container smoke: needs a Docker daemon" >&2; exit 1; }
command -v python3 >/dev/null || { echo "container smoke: needs python3 on PATH" >&2; exit 1; }

# This script destroys $VOLUME on the way in and on the way out, and the volume
# name is an overridable knob — so refuse to touch one we did not create.
# Pointing OPENAI4S_SMOKE_VOLUME at, say, compose.yaml's `openai4s-data` would
# otherwise delete every session, artifact and the access token before the
# first check ran.
if docker volume inspect "$VOLUME" >/dev/null 2>&1; then
  echo "container smoke: volume '${VOLUME}' already exists; refusing to delete" >&2
  echo "  remove it yourself, or set OPENAI4S_SMOKE_VOLUME to an unused name." >&2
  exit 1
fi

cleanup() {
  docker rm --force "$CONTAINER" >/dev/null 2>&1 || true
  docker volume rm --force "$VOLUME" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker rm --force "$CONTAINER" >/dev/null 2>&1 || true

# `python3` rather than curl: the image ships no HTTP client, and neither should
# this script assume one on the host beyond the interpreter it needs anyway.
# Prints "<status> <body>" so a caller can assert on both; an unreachable port
# prints "000 <error>", which every caller treats as not-ready.
probe() {
  python3 - "$1" "${2-}" <<'PY'
import sys, urllib.error, urllib.request

url, token = sys.argv[1], sys.argv[2]
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

running() { [ -n "$(docker ps --quiet --filter "name=^/${CONTAINER}$")" ]; }

wait_for_health() {
  local deadline=$((SECONDS + ${1:-120}))
  while [ "$SECONDS" -lt "$deadline" ]; do
    case "$(probe "${BASE}/health")" in
      200\ *) return 0 ;;
    esac
    running || fail "the container exited while we waited for /health"
    sleep 1
  done
  fail "/health did not answer 200 within ${1:-120}s"
}

echo "container smoke: building ${IMAGE}"
docker build --tag "$IMAGE" . || fail "the image did not build"

docker volume create "$VOLUME" >/dev/null || fail "could not create the volume"

echo "container smoke: starting ${CONTAINER}"
docker run --detach \
  --name "$CONTAINER" \
  --publish "127.0.0.1:${HOST_PORT}:8760" \
  --volume "${VOLUME}:/data" \
  "$IMAGE" >/dev/null || fail "the container did not start"

wait_for_health 180
ok "/health answers 200 unauthenticated"

# The image must not run as root. This is the one property a reader cannot
# check from the Dockerfile alone, because a base image or a later layer can
# quietly put it back.
uid="$(docker exec "$CONTAINER" id -u)" || fail "could not read the container uid"
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

token="$(docker exec "$CONTAINER" cat /data/access-token)" \
  || fail "no access token was minted under the data dir"
[ -n "$token" ] || fail "the access token file is empty"
authenticated="$(probe "${BASE}/api/v1/frames" "$token")"
case "$authenticated" in
  200\ *) ok "the minted token authenticates" ;;
  *) fail "expected 200 from /api/v1/frames with the token, got: ${authenticated}" ;;
esac

# `openai4s url` is how an operator gets that token out of a running
# container, so it has to agree with the file.
url="$(docker exec "$CONTAINER" openai4s url)" || fail "\`openai4s url\` failed"
case "$url" in
  *"$token") ok "\`openai4s url\` hands back the same token" ;;
  *) fail "\`openai4s url\` printed '${url}', which does not carry the minted token" ;;
esac

# The science stack is what makes the default image worth its size. Import it
# rather than trusting that pip reported success.
docker exec "$CONTAINER" python -c "import numpy, pandas, matplotlib, sklearn" \
  || fail "the science stack does not import inside the image"
ok "numpy, pandas, matplotlib and scikit-learn import"

# The image's interpreter is a support claim, not a smoke substitute for the
# 3.14 offline suite. Still refuse an image that is not the series the
# Dockerfile and the CI matrix name.
docker exec "$CONTAINER" python -c "import sys; assert sys.version_info[:2] == (3, 14), sys.version" \
  || fail "the image is not running Python 3.14"
ok "container interpreter is Python 3.14"

# --- the restart case ---------------------------------------------------------
#
# A container that cannot restart is not a deployment option. SIGKILL skips the
# daemon's teardown, so the pidfile survives on the volume — and the next
# container starts with a fresh PID namespace where that pid is very likely
# live again but is somebody else.
#
# Both state files are forced to pid 1 rather than left to chance. pid 1 always
# exists in the new container, and because `USER openai4s` precedes ENTRYPOINT
# it is tini running as the same uid as the daemon — so `os.kill(1, 0)` really
# does succeed and the singleton really is asked the hard question.
#
# The statefile has to move WITH the pidfile. The identity check deliberately
# treats a statefile naming a different pid as no information rather than as
# evidence of staleness — that branch exists so a booter caught in the window
# between the two writes cannot declare the live winner stale. Rewriting only
# the pidfile therefore steers around the very code this step means to assert,
# and the daemon refuses to start for the old reason with the new check never
# consulted. The bogus start token is what the restarted daemon must notice.
#
# The helper writes as the image's own user, NOT as root. /data is 0700 owned
# by uid 1000, so root can write there too — but the files it left behind would
# be root-owned, and the restarting daemon opens daemon.json for writing as uid
# 1000. That is EACCES, and the run would fail for a reason that has nothing to
# do with what is being tested.
docker kill --signal=SIGKILL "$CONTAINER" >/dev/null || fail "could not SIGKILL the container"
docker run --rm --volume "${VOLUME}:/data" --entrypoint sh "$IMAGE" -c \
  'printf 1 > /data/openai4s.pid && printf %s "{\"pid\": 1, \"pid_start\": \"1\"}" > /data/daemon.json' \
  >/dev/null || fail "could not plant the colliding pidfile"
docker start "$CONTAINER" >/dev/null || fail "the container did not start again"
wait_for_health 120
ok "restarts after a SIGKILL that left a colliding pidfile behind"

# The token is the same one, i.e. the volume really is the state: a re-mint
# would have invalidated every cookie already issued.
token_after="$(docker exec "$CONTAINER" cat /data/access-token)" \
  || fail "could not read the access token after the restart"
[ "$token_after" = "$token" ] || fail "the access token changed across a restart"
ok "the access token survived the restart"

# --- graceful shutdown ---------------------------------------------------------
#
# Kubernetes sends SIGTERM and waits. The daemon turns it into its Ctrl-C path,
# closes every session slot and exits 0; tini forwards the signal and reports
# that code. A non-zero exit here means the pod would look like it crashed on
# every ordinary rollout.
#
# `-t` and not `--timeout`: the long flag only exists from Docker CLI 23, and
# Debian 12 and Ubuntu 22.04 still ship 20.10. Checking that the container is
# still up first, because `docker stop` on an already-exited container is a
# no-op that returns 0 and leaves `docker inspect` reporting the *earlier*
# exit — an assertion that passes without a SIGTERM ever being delivered.
running || fail "the container was not running when we came to stop it"
docker stop -t 60 "$CONTAINER" >/dev/null || fail "docker stop failed"
exit_code="$(docker inspect --format '{{.State.ExitCode}}' "$CONTAINER")" \
  || fail "could not read the container exit code"
[ "$exit_code" = "0" ] || fail "SIGTERM produced exit code ${exit_code}, expected 0"
ok "SIGTERM shuts the daemon down cleanly"

echo "container smoke: all checks passed"
