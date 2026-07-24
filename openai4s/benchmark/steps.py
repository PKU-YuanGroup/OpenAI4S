"""The step implementations. Every one of them drives production code.

The rule this file exists to enforce: a benchmark step may inject only what
genuinely cannot run offline — the model, the network, and a package manager —
and it must inject them *into* the real subsystem rather than replace it. A
step that builds its own answer measures the step.

Each function takes the shared ``Context`` and the case's inputs and returns a
dict merged into the case's result. Raising is how a step reports that the
workflow could not proceed; the runner decides whether that matches what the
case declared.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from openai4s.config import Config, LLMConfig


@dataclass
class Context:
    """Everything a case's steps share: a real data dir, store and workspace."""

    root: Path
    config: Config
    workspace: Path
    state: dict[str, Any] = field(default_factory=dict)

    @property
    def store(self):
        from openai4s.store import get_store

        return get_store(self.config.db_path)


def make_context(root: Path) -> Context:
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    config = Config(
        data_dir=root,
        llm=LLMConfig(provider="deepseek", api_key="benchmark-offline"),
    )
    return Context(root=root, config=config, workspace=workspace)


# --------------------------------------------------------------------------
# session / execution
# --------------------------------------------------------------------------


def open_session(ctx: Context, inputs: dict) -> dict:
    """A real project and root frame in the real Store."""
    project = ctx.store.create_project(name=inputs.get("project", "benchmark"))
    frame = ctx.store.new_frame(
        project_id=project["project_id"], kind="turn", status="running"
    )
    ctx.state["project_id"] = project["project_id"]
    ctx.state["root_frame_id"] = frame
    return {"project_id": project["project_id"], "root_frame_id": frame}


def run_python_cell(ctx: Context, inputs: dict) -> dict:
    """Execute a cell in the real persistent Python kernel."""
    from openai4s.kernel.manager import Kernel

    kernel = Kernel(cwd=str(ctx.workspace))
    try:
        result = kernel.execute(inputs["code"])
    finally:
        kernel.shutdown()
    ctx.state["last_stdout"] = result.get("stdout", "")
    if result.get("error"):
        raise RuntimeError(
            f"cell failed: {result.get('error')}: {result.get('error_message')}"
        )
    return {
        "stdout": result.get("stdout", ""),
        "error": result.get("error"),
    }


def run_r_cell(ctx: Context, inputs: dict) -> dict:
    """Execute a cell in the real persistent R kernel, if one can be resolved."""
    from openai4s.kernel.r_kernel import resolve_r_interpreter, spawn_r_kernel

    if resolve_r_interpreter() is None:
        raise SkipCase("no R interpreter is resolvable on this host")
    kernel = spawn_r_kernel(cwd=str(ctx.workspace))
    try:
        result = kernel.execute(inputs["code"])
    finally:
        kernel.shutdown()
    if result.get("error"):
        # The R worker does not always fill `error_message`; the diagnostic the
        # user sees is on stderr, so that is what has to be reported.
        detail = (
            result.get("error_message") or result.get("stderr") or result.get("error")
        )
        raise RuntimeError(f"R cell failed: {detail}")
    return {"stdout": result.get("stdout", "")}


def cancel_python_cell(ctx: Context, inputs: dict) -> dict:
    """Interrupt a running cell through the kernel's real interrupt path."""
    import threading
    import time

    from openai4s.kernel.manager import Kernel

    kernel = Kernel(cwd=str(ctx.workspace))
    outcome: dict[str, Any] = {}

    def execute():
        outcome["result"] = kernel.execute(inputs["code"])

    worker = threading.Thread(target=execute, daemon=True)
    worker.start()
    time.sleep(float(inputs.get("after_seconds", 1.0)))
    kernel.interrupt()
    worker.join(timeout=30)
    try:
        kernel.shutdown()
    except Exception:  # noqa: BLE001
        pass
    result = outcome.get("result") or {}
    if not result.get("error"):
        raise RuntimeError("the cell was interrupted but reported no error")
    return {"error": result.get("error"), "interrupted": True}


# --------------------------------------------------------------------------
# artifacts, lineage, provenance
# --------------------------------------------------------------------------


def save_artifact(ctx: Context, inputs: dict) -> dict:
    """Register a workspace file through the real Store."""
    path = ctx.workspace / inputs["filename"]
    path.parent.mkdir(parents=True, exist_ok=True)
    if "content" in inputs:
        path.write_text(inputs["content"], encoding="utf-8")
    if not path.is_file():
        raise RuntimeError(f"{inputs['filename']} was never produced")
    data = path.read_bytes()
    record = ctx.store.save_artifact(
        path=str(path),
        filename=inputs["filename"],
        content_type=inputs.get("content_type", "text/plain"),
        size_bytes=len(data),
        checksum=hashlib.sha256(data).hexdigest(),
        frame_id=ctx.state["root_frame_id"],
        root_frame_id=ctx.state["root_frame_id"],
        project_id=ctx.state["project_id"],
    )
    # Lineage is its own recorded edge, not a field on the save. Declaring the
    # inputs at save time would have been a second way to say the same thing,
    # and the Store has exactly one.
    for key in inputs.get("derived_from", []):
        source = ctx.state.get(key)
        if source is None:
            raise KeyError(f"derived_from names {key!r}, which no step produced")
        ctx.store.add_lineage_edge(
            input_version_id=source,
            output_version_id=record["version_id"],
            frame_id=ctx.state["root_frame_id"],
        )
    ctx.state[inputs.get("as", inputs["filename"])] = record["version_id"]
    return {
        "artifact_id": record["artifact_id"],
        "version_id": record["version_id"],
        "checksum": hashlib.sha256(data).hexdigest(),
    }


def assert_lineage(ctx: Context, inputs: dict) -> dict:
    """The derived-from edge the Store actually recorded."""
    output = ctx.state[inputs["output"]]
    expected_input = ctx.state[inputs["input"]]
    edges = ctx.store.lineage_inputs(output)
    sources = {
        str(edge.get("input_version_id") or edge.get("version_id")) for edge in edges
    }
    if expected_input not in sources:
        raise RuntimeError(
            f"no lineage edge from {expected_input} to {output}; recorded: "
            f"{sorted(sources)}"
        )
    return {"edges": len(edges)}


def capture_environment(ctx: Context, inputs: dict) -> dict:
    """The artifact environment snapshot, through the real ArtifactManager."""
    from openai4s.server.artifacts import ArtifactManager

    manager = ArtifactManager(
        data_dir=ctx.root,
        store=ctx.store,
        workspace_for=lambda _frame: ctx.workspace,
        broadcast=lambda _frame, _event: None,
        guess_content_type=lambda _name: "text/plain",
        checksum=lambda _path: "x",
    )
    snapshot_id = manager.capture_environment(
        None,
        root_frame_id=ctx.state["root_frame_id"],
        language=inputs.get("language", "python"),
    )
    snapshot = ctx.store.get_env_snapshot(snapshot_id) if snapshot_id else None
    if snapshot is None:
        raise RuntimeError("no environment snapshot was recorded")
    return {
        "snapshot_id": snapshot_id,
        "kind": snapshot.get("kind"),
        "provenance": snapshot.get("provenance"),
        "generation_confidence": snapshot.get("generation_confidence"),
    }


def register_kernel_generation(ctx: Context, inputs: dict) -> dict:
    generation = ctx.store.create_kernel_generation(
        root_frame_id=ctx.state["root_frame_id"],
        branch_id=inputs.get("branch_id") or ctx.state["root_frame_id"],
        language=inputs.get("language", "python"),
        environment={
            "runtime": inputs.get("language", "python"),
            "interpreter": inputs.get("interpreter", sys.executable),
            "environment_name": inputs.get("environment_name", "benchmark"),
        },
        bootstrap={"status": "ok"},
        state="active",
    )
    ctx.state["generation_id"] = generation["generation_id"]
    return {"generation_id": generation["generation_id"]}


# --------------------------------------------------------------------------
# evidence package
# --------------------------------------------------------------------------


def export_session_package(ctx: Context, inputs: dict) -> dict:
    """Export through the real exporter and verify with the real verifier."""
    from openai4s.evidence import verify_package
    from openai4s.server.session_package import SessionPackageService
    from openai4s.storage.snapshots import WorkspaceCAS

    # Built exactly as the session domain builds it, so the export under test
    # is the export the product performs.
    service = SessionPackageService(
        ctx.store,
        data_dir=ctx.root,
        workspace=lambda _root, _branch: ctx.workspace,
        cas=WorkspaceCAS(ctx.root / "workspace-cas"),
    )
    package = service.export(ctx.state["root_frame_id"])
    target = ctx.root / package["filename"]
    target.write_bytes(package["data"])
    report = verify_package(target)
    if not report["ok"]:
        raise RuntimeError(
            f"the exported package does not verify: {report['problems']}"
        )
    return {
        "path": str(target),
        "sha256": package["sha256"],
        "files_verified": len(report["files_verified"]),
    }


def tamper_with_package(ctx: Context, inputs: dict) -> dict:
    """Change one byte and confirm the verifier notices.

    A package format whose verifier accepts a modified archive is decoration,
    and the only way to know it does not is to modify one.
    """
    import zipfile

    from openai4s.evidence import verify_package

    source = Path(ctx.state["package_path"])
    tampered = source.with_name("tampered.zip")
    with zipfile.ZipFile(source) as original:
        names = original.namelist()
        with zipfile.ZipFile(tampered, "w") as out:
            for name in names:
                payload = original.read(name)
                if name == inputs.get("target", "REPRODUCE.md"):
                    payload = payload + b"\n<injected>\n"
                out.writestr(name, payload)
    report = verify_package(tampered)
    if report["ok"]:
        raise RuntimeError("the verifier accepted a tampered package")
    return {"problems": len(report["problems"])}


# --------------------------------------------------------------------------
# environments
# --------------------------------------------------------------------------


def environment_transaction(ctx: Context, inputs: dict) -> dict:
    """plan -> apply -> (optionally fail) -> rollback, on a real filesystem."""
    from openai4s.kernel import env_generations as eg

    spec = ctx.root / "spec.yml"
    spec.write_text(inputs.get("spec", "numpy\n"), encoding="utf-8")

    fail_on = int(inputs.get("fail_on_build", 0))
    calls = {"n": 0}

    def runner(argv, cwd):
        calls["n"] += 1
        if calls["n"] == fail_on:
            return subprocess.CompletedProcess(argv, 1, stderr=b"solver failed")
        prefix = Path(argv[argv.index("--prefix") + 1])
        (prefix / "bin").mkdir(parents=True, exist_ok=True)
        (prefix / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stderr=b"")

    store = eg.EnvironmentStore(ctx.root / "environments", runner=runner)

    def build(prefix, staged_spec):
        return ["fake-conda", "env", "create", "--prefix", str(prefix)]

    def verify(prefix):
        if not (prefix / "bin" / "python").is_file():
            raise RuntimeError("the build produced no interpreter")
        return str(prefix / "bin" / "python"), []

    name = inputs.get("environment", "python")
    generations = []
    for revision in inputs.get("revisions", ["numpy\n"]):
        spec.write_text(revision, encoding="utf-8")
        plan = store.plan(name, spec, tool="fake-conda")
        result = store.apply(plan, spec, tool="fake-conda", build=build, verify=verify)
        generations.append(
            {"ok": result.ok, "id": result.generation.id if result.generation else None}
        )
    current = store.current_id(name)
    rolled_back = None
    target = inputs.get("rollback_to_index")
    if target is not None:
        candidate = generations[int(target)]["id"]
        store.rollback(name, candidate)
        rolled_back = store.current_id(name)
    return {
        "generations": generations,
        "current": current,
        "after_rollback": rolled_back,
        "applied": sum(1 for g in generations if g["ok"]),
    }


# --------------------------------------------------------------------------
# remote compute
# --------------------------------------------------------------------------


def remote_job(ctx: Context, inputs: dict) -> dict:
    """submit -> poll -> harvest against a real shell standing in for sshd.

    The ssh transport is exercised for real — the emitted remote script runs in
    a real shell with its own session, exactly as sshd would give it — because
    the whole class of defect this path has had is shell behaviour that only
    appears when a shell runs it.
    """
    import time

    from openai4s.compute.manager import ComputeManager

    real_run = subprocess.run
    real_popen = subprocess.Popen
    home = ctx.root / "remote-home"
    home.mkdir(parents=True, exist_ok=True)
    shell = inputs.get("shell", "bash")

    def _remote_env(kw: dict) -> dict:
        import os as _os

        env = dict(kw.pop("env", None) or {})
        env.setdefault("HOME", str(home))
        return {**_os.environ, **env}

    def fake(argv, **kw):
        if argv and argv[0] == "ssh":
            env = _remote_env(kw)
            return real_run(
                [shell, "-c", argv[2]],
                start_new_session=True,
                env=env,
                **{k: v for k, v in kw.items() if k != "timeout"},
            )
        if argv and argv[0] == "scp":
            source, destination = argv[-2], argv[-1]
            _alias, _, remote = source.partition(":")
            remote = remote.replace("~", str(home), 1)
            if not Path(remote).is_file():
                return subprocess.CompletedProcess(argv, 1, b"", b"scp: no such file")
            import shutil as _shutil

            _shutil.copy2(remote, destination)
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        return real_run(argv, **kw)

    def fake_popen(argv, **kw):
        # The capped harvest transfer streams the archive over `ssh cat`; route
        # it through the real shell so it actually cats the staged file.
        if argv and argv[0] == "ssh":
            env = _remote_env(kw)
            return real_popen(
                [shell, "-c", argv[2]], start_new_session=True, env=env, **kw
            )
        return real_popen(argv, **kw)

    skills = ctx.root / "skills"
    (skills / "remote-compute-ssh").mkdir(parents=True, exist_ok=True)
    cfg = _ComputeCfg(ctx.root, skills, ctx.config.db_path)
    import openai4s.compute.manager as manager_module

    original, manager_module.subprocess.run = manager_module.subprocess.run, fake
    original_popen, manager_module.subprocess.Popen = (
        manager_module.subprocess.Popen,
        fake_popen,
    )
    try:
        manager = ComputeManager(cfg, workspace=ctx.workspace)
        submitted = manager.submit(
            {
                "provider": "ssh:bench",
                "command": inputs["command"],
                "outputs": inputs.get("outputs"),
            }
        )
        job = manager._jobs[submitted["job_id"]]
        if inputs.get("cancel_after") is not None:
            time.sleep(float(inputs["cancel_after"]))
            manager.cancel({"job_id": submitted["job_id"]})
            return {"status": "cancelled", "job_id": submitted["job_id"]}
        result = {}
        for _ in range(200):
            result = manager._result_ssh(job)
            if result["status"] != "running":
                break
            time.sleep(0.05)
    finally:
        manager_module.subprocess.run = original
        manager_module.subprocess.Popen = original_popen
    return {
        "status": result.get("status"),
        "exit_code": result.get("exit_code"),
        "featured": [Path(p).name for p in result.get("featured_files", [])],
        "unharvested": result.get("unharvested_outputs", []),
        "job_id": submitted["job_id"],
    }


@dataclass
class _ComputeCfg:
    data_dir: Path
    skills_dir: Path
    db_path: Path


# --------------------------------------------------------------------------
# retrieval
# --------------------------------------------------------------------------

_UNIPROT_BODY = json.dumps(
    {
        "results": [
            {
                "primaryAccession": "P01308",
                "proteinDescription": {
                    "recommendedName": {"fullName": {"value": "Insulin"}}
                },
                "organism": {"scientificName": "Homo sapiens", "taxonId": 9606},
                "sequence": {"length": 110},
            }
        ]
    }
)


def science_query(ctx: Context, inputs: dict) -> dict:
    """Query the real connector service over a recorded upstream body.

    The body is recorded rather than fetched because a benchmark must not
    depend on a public API's weather; everything above the transport — the
    adapter, the normalisation, the provenance envelope — is the real code.
    """
    from openai4s.host.science import ScienceConnectorService

    body = inputs.get("body", _UNIPROT_BODY)
    if inputs.get("drop_required"):
        record = json.loads(body)
        for item in record.get("results", []):
            item["primaryAccession"] = None
        body = json.dumps(record)
    raw = body.encode("utf-8")

    def fetch(_url, _fmt, _timeout, _max_chars):
        return {
            "content": body,
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "raw_bytes": len(raw),
        }

    result = ScienceConnectorService(fetch=fetch).search(
        inputs.get("database", "uniprot"), inputs.get("query", "insulin"), limit=5
    )
    provenance = result["provenance"]
    return {
        "count": result["count"],
        "response_sha256": provenance["response_sha256"],
        "hashed": provenance["responses"][0]["hashed"],
        "raw_bytes": provenance["responses"][0]["bytes"],
        "expected_sha256": hashlib.sha256(raw).hexdigest(),
    }


def connector_drift_check(ctx: Context, inputs: dict) -> dict:
    """Run the manifest check the nightly canary runs."""
    from openai4s.host.connector_manifest import MANIFEST_BY_ID

    manifest = MANIFEST_BY_ID[inputs.get("database", "uniprot")]
    document = json.loads(inputs.get("body", _UNIPROT_BODY))
    if inputs.get("drop_required"):
        for item in document.get("results", []):
            item["primaryAccession"] = None
    drift = manifest.check(document)
    return {
        "missing_required": drift["required"],
        "missing_expected": drift["expected"],
    }


# --------------------------------------------------------------------------
# permissions and consent
# --------------------------------------------------------------------------


def host_file_write(ctx: Context, inputs: dict) -> dict:
    """Write through the real workspace boundary, escapes included."""
    from openai4s.host.files import WorkspaceFileService

    files = WorkspaceFileService(
        data_dir=ctx.root,
        frame_id=lambda: ctx.state.get("root_frame_id", "bench"),
        workspace=lambda: ctx.workspace,
    )
    target = files.resolve(inputs["path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(inputs.get("content", "x"), encoding="utf-8")
    return {"path": str(target)}


def telemetry_identity_cycle(ctx: Context, inputs: dict) -> dict:
    """Grant, seal, revoke, re-grant — and try to send the stale payload."""
    from openai4s.telemetry import consent as consent_mod
    from openai4s.telemetry import sender as sender_mod
    from openai4s.telemetry import wire

    first = consent_mod.grant(ctx.store)
    if first is None:
        raise RuntimeError("consent could not be granted")
    stale = wire.seal(first.install_id, [{"event": "daemon_start", "outcome": "ok"}])
    if not inputs.get("revoke", True):
        return {"sent": sender_mod.send(ctx.store, stale), "identity": "current"}
    consent_mod.revoke(ctx.store)
    second = consent_mod.grant(ctx.store)
    if second is None or second.install_id == first.install_id:
        raise RuntimeError("re-granting did not mint a fresh identity")
    return {
        "sent": sender_mod.send(ctx.store, stale),
        "identity": "revoked",
        "ids_differ": True,
    }


class SkipCase(Exception):
    """This host cannot run the case; not a failure of the system under test."""


#: Name -> implementation. A manifest may only name a step that exists here,
#: which is what stops a workflow from describing work nothing performs.
STEPS: dict[str, Callable[[Context, dict], dict]] = {
    "open_session": open_session,
    # Two artifact saves in one workflow need distinct step names, because the
    # runner keys a step's inputs by its name — reusing the name would silently
    # give both saves the same file.
    "save_raw": save_artifact,
    "save_derived": save_artifact,
    "run_python_cell": run_python_cell,
    "run_r_cell": run_r_cell,
    "cancel_python_cell": cancel_python_cell,
    "save_artifact": save_artifact,
    "assert_lineage": assert_lineage,
    "capture_environment": capture_environment,
    "register_kernel_generation": register_kernel_generation,
    "export_session_package": export_session_package,
    "tamper_with_package": tamper_with_package,
    "environment_transaction": environment_transaction,
    "remote_job": remote_job,
    "science_query": science_query,
    "connector_drift_check": connector_drift_check,
    "host_file_write": host_file_write,
    "telemetry_identity_cycle": telemetry_identity_cycle,
}


__all__ = ["Context", "SkipCase", "STEPS", "make_context"]
