# OpenAI4S — container image for the daemon + web workbench.
#
# Build:  docker build -t openai4s:local .
# Run:    docker run --rm -p 127.0.0.1:8760:8760 -v openai4s-data:/data openai4s:local
#
# Read docs/docker.md before exposing this beyond loopback. Two properties of
# this image are decisions rather than defaults:
#
#   * It binds 0.0.0.0 *inside the container's own network namespace*, which is
#     the only address a published port can reach. That is not the same as
#     exposing the daemon on a network: what you publish the port to is. A
#     non-loopback bind also makes the access token mandatory and unremovable
#     (gateway.py: `_needs_token = (not _loopback) or ...`), so the token — not
#     the Host-header allowlist, which a wildcard bind necessarily turns off —
#     is the control standing between a caller and endpoints that execute code.
#
#   * It selects the environment-injection secret backend. Credentials arrive
#     as `OPENAI4S_SECRET_<SCOPE>_<NAME>` and nothing credential-shaped is ever
#     written to the volume, which is stronger than the keychain a container
#     cannot have rather than a fallback from it. `auto` would fail closed here
#     — correctly, since storing a key unprotected must be a decision — but
#     failing closed also means a `SecretStoreUnavailable` traceback ahead of
#     the startup banner on every boot, for a migration with nothing to
#     migrate. Choosing the backend that a server actually has removes the
#     noise without weakening anything. See docs/docker.md.

# --- build stage: turn this tree into the same wheel CI builds ---------------
#
# Not a `pip install .` of the source tree, and not `pip install openai4s` from
# PyPI: the published 0.1.0 predates this tree while carrying the same version
# string, so an image built from the index would be silently older than the
# checkout it was built in. Building the wheel here is the path
# `.github/workflows/ci.yml`'s release-artifacts job already proves installable.
FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579 AS builder

WORKDIR /src
# The build backend first, in its own layer: it changes only when the pin in
# `pyproject.toml` does, so every source edit reuses it instead of re-fetching
# the wheel from PyPI on each build.
COPY deploy/container-requirements-build.txt /tmp/container-requirements-build.txt
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes \
        -r /tmp/container-requirements-build.txt
COPY . /src
RUN python -m pip wheel --no-cache-dir --no-deps --no-build-isolation \
        --wheel-dir /wheels /src

# --- runtime stage -----------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579 AS runtime

# `science` installs numpy/pandas/matplotlib/scikit-learn so agent cells can do
# actual work. Pass `--build-arg OPENAI4S_EXTRAS=` for the stdlib-only control
# plane (a much smaller image that can still run the agent, the tools and the
# web UI, but whose Python cells have no scientific stack).
ARG OPENAI4S_EXTRAS=science

# bubblewrap: the Linux kernel-sandbox backend. Present so that a container run
#   with the namespace privileges bwrap needs can actually enforce, and so that
#   `openai4s doctor` reports on the real backend rather than on its absence.
#   Unprivileged containers cannot create the namespaces it wants; the daemon
#   then degrades visibly and the container is the boundary. docs/docker.md
#   says exactly what that does and does not cover.
# tini: PID 1. The daemon spawns kernel workers, `bash -c` jobs and detached
#   background processes; a Python process running as PID 1 does not reap what
#   it never waited on, and it is not an init.
# ca-certificates: every LLM call is urllib over HTTPS against the system trust
#   store — there is no vendored certifi anywhere in the tree.
RUN apt-get update \
    && apt-get install --no-install-recommends --yes \
        bubblewrap \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

# A real account with a real /etc/passwd entry and a real home, not a bare
# numeric uid. `Path.home()` is called before any kernel starts (the sandbox's
# secret-read denial list and its outside-write probe both use it) and raises
# RuntimeError — not OSError, so nothing catches it — when the uid resolves to
# no home at all. `docker run --user 1000:1000` then lands on an account that
# already exists here.
RUN groupadd --gid 1000 openai4s \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash openai4s

COPY deploy/container-requirements-science.txt /tmp/container-requirements-science.txt
COPY --from=builder /wheels/*.whl /tmp/wheels/
RUN set -eu; \
    case "${OPENAI4S_EXTRAS}" in \
        science) python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r /tmp/container-requirements-science.txt ;; \
        "") ;; \
        *) echo "unsupported OPENAI4S_EXTRAS=${OPENAI4S_EXTRAS}; expected science or an empty value" >&2; exit 2 ;; \
    esac; \
    python -m pip install --no-cache-dir --no-index --no-deps /tmp/wheels/openai4s-*.whl; \
    rm -rf /tmp/wheels /tmp/container-requirements-science.txt

# The data directory: the SQLite store, artifacts, session workspaces, skills,
# the checkpoint CAS, and the access token. Everything worth keeping is here
# and nowhere else, so this single path is the only volume that matters.
# Created and owned before the volume exists so a *named* volume inherits this
# ownership; a bind mount does not, and must be chowned to 1000:1000 on the
# host. Deliberately no `VOLUME` instruction: it would conjure an anonymous
# volume for anyone who forgot to mount one, which loses data quietly instead
# of loudly.
RUN install -d -o openai4s -g openai4s -m 0700 /data

ENV OPENAI4S_DATA_DIR=/data \
    OPENAI4S_HOST=0.0.0.0 \
    OPENAI4S_PORT=8760

# OPENAI4S_NO_OPEN — nothing here can open a browser, and the daemon would
#   otherwise spend a second in a thread finding that out.
# OPENAI4S_SKIP_DOTENV — `.env` discovery walks up from the *installed*
#   package, i.e. from site-packages, so it can never reach a file you COPY
#   into the image. Saying so explicitly beats a silent no-op: pass real
#   environment variables.
ENV OPENAI4S_NO_OPEN=1 \
    OPENAI4S_SKIP_DOTENV=1

# Credentials from the environment, written to disk nowhere. `..._SECRET_ENV`
# marks the backend available before any credential exists, which a fresh
# server needs — otherwise the daemon would have nothing to resolve and would
# fail closed on the first credential it was asked to handle. Supply the model
# key as OPENAI4S_SECRET_LLM_LLM_API_KEY; the plain OPENAI4S_LLM_API_KEY is
# still read by the config layer and remains the shorter path for a one-off
# `docker run`. Override with OPENAI4S_SECRET_STORE=plaintext if you would
# rather manage keys from the UI and accept them sitting in the clear in
# openai4s.db on the volume.
ENV OPENAI4S_SECRET_STORE=env \
    OPENAI4S_SECRET_ENV=1

# PYTHONUNBUFFERED — stdout is block-buffered when it is not a TTY, which is
#   every container. Without this the startup lines sit in a buffer and
#   `docker logs` is empty while the daemon is already serving.
# PYTHONDONTWRITEBYTECODE — pip byte-compiles everything it installs (it is
#   the default; only `--no-compile` turns it off), so site-packages arrives
#   precompiled and, at runtime, the daemon is not root and could not write
#   there anyway. An explicit `compileall` pass used to sit above this and was
#   removed: it rewrote nothing pip had not already produced, covered less of
#   site-packages than pip does, and could not fail — `compileall` swallows a
#   directory it cannot list and still exits 0, so a glob that stopped matching
#   would have gone on silently doing nothing.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER openai4s
WORKDIR /home/openai4s
EXPOSE 8760

# `/health` is one of exactly two routes reachable without a credential, and it
# touches no database, no kernel and no LLM. urllib rather than curl: the image
# ships no HTTP client of its own and the interpreter is already here.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('OPENAI4S_PORT','8760')+'/health', timeout=4).read()"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["openai4s", "serve"]
