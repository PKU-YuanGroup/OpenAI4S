"""The container deployment assets, pinned against the code they describe.

A Dockerfile, a compose file and a Kubernetes manifest are configuration for a
program that is free to change underneath them. Nothing in a YAML file fails
when the daemon renames the variable it reads, moves the route a probe polls,
or changes which paths answer without a credential — the deployment simply
stops working, somewhere else, later, for someone who did not make the change.

So these tests do not restate the manifests. Each one ties a value in an asset
to the thing in `openai4s/` that gives it meaning: the probe path to the
gateway's own unauthenticated set, the port to `Config`'s default, the secret
variable to the name the broker derives, the container uid to the account the
image creates. The exceptions are the two properties that are dangerous rather
than merely drift-prone — a single replica, and a published port bound to
loopback — which are asserted because a plausible edit reverses them.

`scripts/container_smoke.sh` is the other half and answers a different
question: whether the image, built and run, actually works. It needs a Docker
daemon and runs as its own CI job.
"""

from __future__ import annotations

import importlib
import json
import os
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
COMPOSE = ROOT / "compose.yaml"
KUBERNETES = ROOT / "deploy" / "kubernetes.yaml"
INGRESS = ROOT / "deploy" / "kubernetes-ingress.yaml"

#: The container image's account, asserted from both ends: the Dockerfile
#: creates it and the pod securityContext runs as it.
CONTAINER_UID = 1000


def _dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def _dockerfile_env() -> dict[str, str]:
    """The ENV values the image bakes in, flattened across continuations."""
    text = re.sub(r"\\\n", " ", _dockerfile())
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("ENV "):
            continue
        for name, value in re.findall(r"(OPENAI4S_\w+|PYTHON\w+)=(\S+)", stripped):
            values[name] = value.strip('"')
    return values


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _kubernetes() -> list[dict]:
    documents = yaml.safe_load_all(KUBERNETES.read_text(encoding="utf-8"))
    return [document for document in documents if document]


def _kind(kind: str) -> dict:
    matches = [document for document in _kubernetes() if document.get("kind") == kind]
    assert len(matches) == 1, f"expected exactly one {kind} in {KUBERNETES.name}"
    return matches[0]


def _container() -> dict:
    spec = _kind("Deployment")["spec"]["template"]["spec"]
    containers = spec["containers"]
    assert len(containers) == 1
    return containers[0]


def _container_env() -> dict[str, dict]:
    return {entry["name"]: entry for entry in _container().get("env", [])}


# --------------------------------------------------------------------------
# The assets against the daemon they run
# --------------------------------------------------------------------------


def test_every_probe_polls_a_route_that_answers_without_a_credential():
    """A probe that needs a token is a probe that reports the pod unhealthy.

    On any non-loopback bind — which is every container — the access token is
    mandatory and every route except a frozen pair answers 401. A liveness
    probe aimed at `/` would restart a perfectly healthy daemon in a loop, so
    the path is taken from the gateway's own exemption set rather than
    remembered.
    """
    from openai4s.server import contract
    from openai4s.server import gateway as gateway_mod

    # The gate's own set, not a copy of it: a copy keeps passing while somebody
    # widens the real one.
    exempt = set(gateway_mod._UNAUTHENTICATED_PATHS)
    assert "/health" in exempt, (
        "the deployment assets probe /health; the gateway no longer exempts it "
        f"from the token gate (exempt paths are {sorted(exempt)})"
    )
    # Not a stale duplicate of the constant: the API root moving would silently
    # leave the other exempt path unreachable too.
    assert contract.API_ROOT + "/auth/status" in exempt

    probes = _container()
    for name in ("startupProbe", "readinessProbe", "livenessProbe"):
        assert probes[name]["httpGet"]["path"] in exempt, name

    healthcheck = " ".join(_compose()["services"]["openai4s"]["healthcheck"]["test"])
    assert "/health" in healthcheck
    assert "/health" in _dockerfile()


def test_the_port_every_asset_publishes_is_the_port_the_daemon_defaults_to():
    from openai4s.config import Config

    port = Config().port
    assert port == 8760, "the default moved; the assets below encode the old one"

    assert _dockerfile_env()["OPENAI4S_PORT"] == str(port)
    assert f"EXPOSE {port}" in _dockerfile()
    assert _container()["ports"][0]["containerPort"] == port
    assert _container_env()["OPENAI4S_PORT"]["value"] == str(port)
    assert _kind("Service")["spec"]["ports"][0]["port"] == port
    published = _compose()["services"]["openai4s"]["ports"]
    assert published == [f"127.0.0.1:{port}:{port}"]


def test_the_data_dir_the_image_declares_is_the_path_the_volume_mounts():
    """One volume, at one path. A mismatch loses every session silently.

    Nothing fails when a volume is mounted next to the data dir instead of on
    it: the daemon creates a fresh database inside the container's writable
    layer and works perfectly until the container is replaced.
    """
    data_dir = _dockerfile_env()["OPENAI4S_DATA_DIR"]
    assert data_dir == "/data"

    assert _container_env()["OPENAI4S_DATA_DIR"]["value"] == data_dir
    mounts = _container()["volumeMounts"]
    assert [mount["mountPath"] for mount in mounts] == [data_dir]

    volumes = _compose()["services"]["openai4s"]["volumes"]
    assert [str(volume).split(":")[1] for volume in volumes] == [data_dir]


def test_the_injected_api_key_variable_is_one_that_actually_reaches_the_client(
    monkeypatch,
):
    """A key that resolves to nothing is indistinguishable from no key at all.

    The obvious choice here is wrong, and wrong silently. The secret broker's
    environment-injection backend documents itself as the answer for "a
    Kubernetes Secret", but `OPENAI4S_SECRET_LLM_LLM_API_KEY` is consulted only
    once a settings row already holds a `secret://` reference — and the only
    writer of that row refuses to run under that backend. On a fresh volume the
    Secret is mounted, the pod is healthy, and the model reports itself
    unconfigured. So the manifests inject the plain configuration variable, and
    this test resolves it through `LLMConfig` rather than trusting the name.
    """
    from openai4s.config import LLMConfig

    injected = "OPENAI4S_LLM_API_KEY"
    # A per-provider variable outranks the generic one, and the suite's own
    # conftest sets one for the fake provider it pins. Name the provider
    # explicitly — the field default is read at module import, so setting the
    # variable now would be too late — and clear its three key names, so this
    # measures the variable the manifests actually inject.
    for shadowing in ("OPENAI4S_ARK_API_KEY", "ARK_API_KEY", "DOUBAO_API_KEY"):
        monkeypatch.delenv(shadowing, raising=False)
    monkeypatch.setenv(injected, "sk-supplied-by-the-orchestrator")
    assert LLMConfig(provider="ark").api_key == "sk-supplied-by-the-orchestrator", (
        f"{injected} no longer reaches the model client; the deployment assets "
        "inject a variable that resolves to nothing"
    )

    key = _container_env()[injected]
    assert key["valueFrom"]["secretKeyRef"]["name"]
    # The manifest has to apply before the Secret exists, or the first
    # `kubectl apply` of a fresh install cannot start a pod at all.
    assert key["valueFrom"]["secretKeyRef"]["optional"] is True

    assert injected in _compose()["services"]["openai4s"]["environment"]


def test_every_openai4s_variable_the_assets_set_is_one_the_code_reads():
    """A typo'd variable is inert, and inertness looks exactly like a default.

    Read from the sources rather than from a list kept here, so a variable
    that is renamed or retired takes its manifest entry down with it.
    """
    from openai4s.security.secret_broker import EnvInjectionBackend

    sources = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (ROOT / "openai4s").rglob("*.py")
    )

    # A build argument is a knob for the Dockerfile itself, not an environment
    # the daemon ever reads. Recognised by its `ARG` declaration rather than by
    # its name, so a typo in an ENV cannot hide behind the same prefix.
    build_args = set(re.findall(r"^ARG\s+(\w+)", _dockerfile(), flags=re.MULTILINE))

    named: set[str] = set()
    for asset in (DOCKERFILE, COMPOSE, KUBERNETES):
        text = asset.read_text(encoding="utf-8")
        for line in text.splitlines():
            # Comments in these files discuss variables the assets deliberately
            # do not set.
            if line.lstrip().startswith("#"):
                continue
            named.update(re.findall(r"\bOPENAI4S_[A-Z0-9_]+\b", line))

    assert named, "the extraction matched nothing; the assertion would be vacuous"

    unknown = sorted(
        name
        for name in named
        if name not in build_args
        # A per-secret injection variable is constructed at runtime from a
        # scope and a setting name, so it appears in no source literal.
        and not name.startswith(EnvInjectionBackend.PREFIX)
        and f'"{name}"' not in sources
    )
    assert not unknown, f"deployment assets set variables no code reads: {unknown}"


# --------------------------------------------------------------------------
# The two properties that are dangerous rather than drift-prone
# --------------------------------------------------------------------------


def test_the_deployment_stays_a_single_replica_that_never_overlaps_itself():
    """Two pods on one volume corrupt the store; there is no sharded mode.

    The store is SQLite in rollback-journal mode with no cross-process
    coordination beyond the daemon's own pidfile. `replicas: 2` would look like
    scaling and behave like corruption, and a RollingUpdate reaches the same
    state for one interval on every upgrade.
    """
    deployment = _kind("Deployment")
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"]["type"] == "Recreate"
    assert _kind("PersistentVolumeClaim")["spec"]["accessModes"] == ["ReadWriteOnce"]


def test_the_pod_runs_unprivileged_as_the_account_the_image_creates():
    """Including no service-account token: this pod runs model-authored code."""
    assert f"--uid {CONTAINER_UID}" in _dockerfile()
    assert re.search(r"^USER openai4s$", _dockerfile(), flags=re.MULTILINE)

    pod = _kind("Deployment")["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    security = pod["securityContext"]
    assert security["runAsNonRoot"] is True
    assert security["runAsUser"] == CONTAINER_UID
    # A freshly provisioned volume belongs to root; without this the daemon
    # cannot create its own database on it.
    assert security["fsGroup"] == CONTAINER_UID

    container = _container()["securityContext"]
    assert container["allowPrivilegeEscalation"] is False
    assert container["capabilities"]["drop"] == ["ALL"]


def test_the_build_context_cannot_carry_a_credential_into_a_layer():
    """`.env` is git-ignored, so nothing else in the repository stops it."""
    ignored = {
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert ".env" in ignored
    assert ".env.*" in ignored
    assert ".git" in ignored


def test_the_ingress_example_keeps_the_host_header_reasoning_with_it():
    """The Origin==Host guard is why this file needs a warning, not a template.

    A proxy that rewrites Host turns every mutation and every WebSocket upgrade
    into a 403 while ordinary GETs keep working — which reads as a broken
    application rather than a proxy misconfiguration. The gateway comparison is
    asserted here so that removing it makes this documentation obsolete loudly.
    """
    gateway = (ROOT / "openai4s" / "server" / "gateway.py").read_text("utf-8")
    assert "cross-origin request refused" in gateway

    text = INGRESS.read_text(encoding="utf-8")
    assert "Host" in text and "Origin" in text
    manifest = yaml.safe_load(text)
    assert manifest["kind"] == "Ingress"
    assert manifest["spec"]["tls"], "the example must terminate TLS"


# --------------------------------------------------------------------------
# The singleton across a container restart
# --------------------------------------------------------------------------


class _Cfg:
    def __init__(self, root: Path) -> None:
        self.pidfile = root / "openai4s.pid"
        self.statefile = root / "daemon.json"
        self.host = "0.0.0.0"
        self.port = 8760


def test_a_reused_pid_does_not_look_like_a_running_daemon(tmp_path, monkeypatch):
    """The container-restart case, which `os.kill(pid, 0)` alone gets wrong.

    A SIGKILL — an OOM kill, a lost node, `docker kill` — skips the daemon's
    teardown and leaves the pidfile on the volume. The next container starts a
    fresh pid namespace at 1, so that pid is live again and belongs to
    something else, usually the init that just launched this very daemon.
    Liveness says "running", the singleton refuses, and the pod never starts
    again.
    """
    cli_main = importlib.import_module("openai4s.cli.main")

    cfg = _Cfg(tmp_path)
    cfg.pidfile.write_text("7", "utf-8")
    cfg.statefile.write_text(
        json.dumps({"pid": 7, "pid_start": "111111", "host": "0.0.0.0", "port": 8760}),
        "utf-8",
    )

    monkeypatch.setattr(cli_main, "_pid_alive", lambda pid: True)
    # The pid is live, but it started later than the daemon we recorded.
    monkeypatch.setattr(cli_main, "_process_start_token", lambda pid: "999999")

    assert cli_main._daemon_alive(cfg, 7) is False
    # ...so the claim is reclaimed rather than refused.
    assert cli_main._acquire_singleton(cfg) is True


def test_a_matching_start_token_still_means_the_daemon_is_running(
    tmp_path, monkeypatch
):
    """The fix must not turn a real "already running" into a double boot."""
    cli_main = importlib.import_module("openai4s.cli.main")

    cfg = _Cfg(tmp_path)
    cfg.pidfile.write_text("7", "utf-8")
    cfg.statefile.write_text(
        json.dumps({"pid": 7, "pid_start": "111111"}), encoding="utf-8"
    )

    monkeypatch.setattr(cli_main, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(cli_main, "_process_start_token", lambda pid: "111111")

    assert cli_main._daemon_alive(cfg, 7) is True
    assert cli_main._acquire_singleton(cfg) is False


@pytest.mark.parametrize(
    "statefile",
    [None, "{}", "not json", json.dumps({"pid": 999999, "pid_start": "1"})],
)
def test_an_uninformative_statefile_falls_back_to_plain_liveness(
    tmp_path, monkeypatch, statefile
):
    """No information must not be read as evidence of staleness.

    The statefile is written just after the pidfile is claimed, so a booter
    caught in that window is described by its predecessor's record. Treating a
    mismatched pid as proof would let a loser declare the winner stale and
    reclaim a live pidfile — reopening the race O_EXCL exists to close. The
    same applies to a daemon from before this field existed, and to any
    platform with no procfs to read.
    """
    cli_main = importlib.import_module("openai4s.cli.main")

    cfg = _Cfg(tmp_path)
    if statefile is not None:
        cfg.statefile.write_text(statefile, "utf-8")

    monkeypatch.setattr(cli_main, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(cli_main, "_process_start_token", lambda pid: "999999")
    assert cli_main._daemon_alive(cfg, 7) is True

    monkeypatch.setattr(cli_main, "_pid_alive", lambda pid: False)
    assert cli_main._daemon_alive(cfg, 7) is False


def test_the_record_is_on_disk_before_the_pid_that_points_at_it(tmp_path, monkeypatch):
    """Ordering, and the reason the obvious order is wrong.

    Once the statefile carries the identity `_daemon_alive` compares against,
    writing it *after* the pidfile leaves a window holding a readable new pid
    beside the previous generation's record. Where the two name the same pid —
    negligible on a desktop, ordinary in a container, which hands the daemon
    the same low pid every boot — a reader in that window compares this process
    against its predecessor's token, concludes stale, and acts: a second
    `serve` reclaims a live pidfile, and `stop` deletes a live daemon's state.

    Asserted as the invariant rather than as the line order: at no point may
    the pidfile hold a readable pid while the statefile describes something
    else.
    """
    cli_main = importlib.import_module("openai4s.cli.main")

    cfg = _Cfg(tmp_path)
    cfg.pidfile.write_text("99999999", "utf-8")
    cfg.statefile.write_text(
        json.dumps({"pid": 99999999, "pid_start": "PREVIOUS-GENERATION"}), "utf-8"
    )

    # Sampled *before* the statefile write, because that is the only moment the
    # two files can disagree: the pidfile is written through an already-open fd
    # rather than through `write_text`, so a sample taken afterwards sees both
    # writes done and would pass against either order.
    seen: list[tuple[int | None, tuple[int | None, str | None]]] = []
    real_write = type(cfg.pidfile).write_text

    def _watch(self, *args, **kwargs):
        seen.append((cli_main._read_pid(cfg), cli_main._recorded_identity(cfg)))
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(type(cfg.pidfile), "write_text", _watch)
    monkeypatch.setattr(cli_main, "_process_start_token", lambda pid: "CURRENT")
    assert cli_main._acquire_singleton(cfg) is True

    for pid, (recorded_pid, recorded_start) in seen:
        if pid is None:
            continue  # an empty pidfile is "no daemon" to every reader
        assert (recorded_pid, recorded_start) == (pid, "CURRENT"), (
            "the pidfile named a pid the statefile did not describe; a reader "
            "landing here would call a live daemon stale"
        )

    # ...and the settled state is consistent, so the loop above is not the
    # whole assertion resting on an empty sample.
    assert cli_main._read_pid(cfg) == os.getpid()
    assert cli_main._recorded_identity(cfg) == (os.getpid(), "CURRENT")


@pytest.mark.skipif(
    not Path("/proc/self/stat").exists(), reason="requires procfs (Linux)"
)
def test_the_start_token_is_read_from_real_procfs():
    """Parsed from the live /proc, so the field offset is proven, not asserted.

    The monkeypatched tests above would pass just as happily against a parser
    that returned the wrong field. This one reads the process actually running
    the suite: its token must be stable across calls and must differ from pid
    1's, which booted first.
    """
    from openai4s.cli.main import _process_start_token

    mine = _process_start_token(os.getpid())
    assert mine is not None and mine.isdigit()
    assert _process_start_token(os.getpid()) == mine

    init = _process_start_token(1)
    if init is not None:
        assert init != mine, "this process cannot have started when pid 1 did"

    assert _process_start_token(999999) is None


def test_a_wildcard_bind_is_rendered_as_an_address_a_client_can_dial():
    """`http://0.0.0.0:8760/` is the one line a container operator reads."""
    from openai4s.cli.main import _reachable_host

    assert _reachable_host("0.0.0.0") == "localhost"
    assert _reachable_host("") == "localhost"
    assert _reachable_host("::") == "localhost"
    assert _reachable_host("127.0.0.1") == "127.0.0.1"
    assert _reachable_host("192.168.1.10") == "192.168.1.10"
