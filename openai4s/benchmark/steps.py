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
# tool bring-up
# --------------------------------------------------------------------------


#: The fake design tool a bring-up installs: reads ``--target``/``--weights``
#: and prints a deterministic JSON report. Flagged modes exist so a case can
#: inject each way a canary fails. The workflow benchmark executes this fixture
#: with ``sys.executable``; the frozen record carries a portable logical command,
#: never the test interpreter or temporary root.
_TOOL_SCRIPT = """\
import hashlib
import json
import sys


def _value(argv, flag, default):
    try:
        return argv[argv.index(flag) + 1]
    except (ValueError, IndexError):
        return default


argv = sys.argv[1:]
if "--fail" in argv:
    sys.exit(3)
if "--no-output" in argv:
    sys.exit(0)
if "--unparseable" in argv:
    print("not json")
    sys.exit(0)
target = _value(argv, "--target", "unknown")
weights = _value(argv, "--weights", "")
digest = hashlib.sha256(open(weights, "rb").read()).hexdigest()
plddt = 75.0 + (int(hashlib.sha256(target.encode()).hexdigest()[:2], 16) % 200) / 10
print(json.dumps(
    {
        "target": target,
        "sequence": "SEQ" + target.replace(".", "").replace("_", ""),
        "plddt": plddt,
        "weights_sha256": digest,
    },
    sort_keys=True,
))
"""


#: The fake downstream sequence-design adapter: consumes the canary JSON and
#: writes a consumption record. ``--refuse`` exits 1 without writing — the
#: injected way a downstream consumer fails.
_ADAPTER_SCRIPT = """\
import json
import sys

refuse = "--refuse" in sys.argv[1:]
if refuse:
    sys.exit(1)
argv = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
with open(argv[0], encoding="utf-8") as handle:
    canary = json.load(handle)
with open(argv[1], "w", encoding="utf-8") as handle:
    json.dump(
        {
            "consumer": "sequence-design",
            "target": canary.get("target"),
            "sequence": canary.get("sequence"),
            "plddt": canary.get("plddt"),
            "consumed_weights_sha256": canary.get("weights_sha256"),
        },
        handle,
        sort_keys=True,
    )
"""


_TOOL_SPEC = "design-tool==1.0.0"
_REFERENCE_WEIGHTS_SHA256 = (
    "e2b48ba6e8371b7a2f6c615e6ce74d370ae58b4dec285ff0fd968c51ee15c802"
)


def tool_bringup(ctx: Context, inputs: dict) -> dict:
    """Simulate an agent bringing a design tool up: build the environment,
    download weights, run a canary, prove the output parses and a downstream
    adapter consumes it, and freeze the whole thing into ``bringup.json``.

    Every simulated failure is recorded in the frozen record rather than
    raised — the gate is the ``verify_bringup`` step that follows.
    """
    import math
    import time

    from openai4s import pkgscan
    from openai4s.benchmark import bringup
    from openai4s.kernel import env_generations as eg

    started = time.monotonic()
    root = ctx.root
    record_dir = root / bringup.RECORD_DIR
    record_dir.mkdir(parents=True, exist_ok=True)

    # The adapter is part of the frozen deliverable, not an implementation
    # detail created only after the canary happened to pass. Every attempt
    # writes the same deterministic bytes and records their identity below.
    adapter_path = record_dir / "adapter.py"
    adapter_bytes = _ADAPTER_SCRIPT.encode("utf-8")
    adapter_path.write_bytes(adapter_bytes)

    # A retry replaces the active attempt's outputs. Leaving an earlier
    # attempt's files in place would let a failed retry accidentally vouch for
    # stale success bytes even though its record declared no new output.
    canary_output_path = record_dir / "canary_output.json"
    downstream_path = record_dir / "downstream_result.json"
    canary_output_path.unlink(missing_ok=True)
    downstream_path.unlink(missing_ok=True)

    # 1. Build the tool environment through the real EnvironmentStore
    #    transaction: the package manager is injected (the same fake-conda
    #    seam environment_transaction uses), the transaction is real.
    spec = root / "spec.yml"
    spec_text = str(inputs.get("spec", _TOOL_SPEC + "\n"))
    spec.write_text(spec_text, encoding="utf-8")
    spec_matches = spec_text.strip() == _TOOL_SPEC

    def runner(argv, cwd):
        if inputs.get("fail_build"):
            return subprocess.CompletedProcess(
                argv, 1, stderr=b"injected package-manager build failure"
            )
        prefix = Path(argv[argv.index("--prefix") + 1])
        (prefix / "bin").mkdir(parents=True, exist_ok=True)
        (prefix / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
        (prefix / "bin" / "tool").write_text(_TOOL_SCRIPT, encoding="utf-8")
        (prefix / "conda-meta").mkdir(parents=True, exist_ok=True)
        meta = prefix / "conda-meta" / "design-tool-1.0.0-0.json"
        meta.write_text(
            json.dumps({"name": "design-tool", "version": "1.0.0"}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, stderr=b"")

    store = eg.EnvironmentStore(root / "environments", runner=runner)

    def build(prefix, staged_spec):
        return [
            "fake-conda",
            "env",
            "create",
            "--prefix",
            str(prefix),
            "--file",
            str(staged_spec),
        ]

    def verify(prefix):
        if not (prefix / "bin" / "tool").is_file():
            raise RuntimeError("the build produced no tool")
        return str(prefix / "bin" / "python"), ["design-tool==1.0.0"]

    name = inputs.get("environment", "design-tool")
    plan = store.plan(name, spec, tool="fake-conda")
    result = store.apply(plan, spec, tool="fake-conda", build=build, verify=verify)
    generation = result.generation
    prefix = Path(generation.prefix) if generation else None
    build_ok = bool(result.ok and generation is not None and prefix is not None)
    package_present = False
    if prefix is not None:
        packages = pkgscan.collect_packages(prefix)
        package_present = pkgscan.normalize_pkg("design-tool") in packages
    pkgscan_ok = package_present and spec_matches

    # 2. "Download" weights: deterministic bytes with a recorded digest.
    weights_dir = root / "weights"
    weights_dir.mkdir(parents=True, exist_ok=True)
    weights_bytes = hashlib.sha256(
        f"openai4s.bringup:weights:{inputs.get('weights_seed', 'v1')}".encode()
    ).digest()
    weights_path = weights_dir / "model.weights"
    weights_path.write_bytes(weights_bytes)
    weights_sha256 = hashlib.sha256(weights_bytes).hexdigest()

    # 3. Run the canary against a real campaign target.
    target = str(inputs.get("target", "P01308"))
    canary_flags = []
    if inputs.get("fail_canary"):
        canary_flags.append("--fail")
    elif inputs.get("canary_no_output"):
        canary_flags.append("--no-output")
    elif inputs.get("canary_unparseable"):
        canary_flags.append("--unparseable")
    # This is the portable audit command. The actual fixture subprocess below
    # uses absolute paths because it has to run, but those machine-specific
    # values never enter bringup.json.
    canary_command = [
        "python",
        "bin/tool",
        "--target",
        target,
        "--weights",
        "weights/model.weights",
        *canary_flags,
    ]
    canary_exit = None
    canary_stdout = ""
    if build_ok and prefix is not None:
        actual_canary_command = [
            sys.executable,
            str(prefix / "bin" / "tool"),
            "--target",
            target,
            "--weights",
            str(weights_path),
            *canary_flags,
        ]
        canary = subprocess.run(actual_canary_command, capture_output=True, text=True)
        canary_exit = canary.returncode
        canary_stdout = canary.stdout or ""

    parse_ok = False
    parsed = None
    required_fields = inputs.get(
        "canary_fields", ["target", "sequence", "plddt", "weights_sha256"]
    )
    if canary_exit == 0 and canary_stdout.strip():
        canary_output_path.write_text(canary_stdout, encoding="utf-8")
        try:
            parsed = json.loads(canary_stdout)
        except ValueError:
            parsed = None
        parse_ok = isinstance(parsed, dict) and all(
            field in parsed for field in required_fields
        )

    # 4. Prove the downstream sequence-design adapter consumes the output.
    downstream_ok = False
    if parse_ok:
        adapter_flags = ["--refuse"] if inputs.get("refuse_downstream") else []
        downstream = subprocess.run(
            [
                sys.executable,
                str(adapter_path),
                str(canary_output_path),
                str(downstream_path),
                *adapter_flags,
            ],
            capture_output=True,
            text=True,
        )
        downstream_ok = downstream.returncode == 0 and downstream_path.is_file()

    # 5. Admit only when the final attempt passed and the *campaign's cumulative*
    #    cost remains within the budget frozen on its first attempt. A retry may
    #    ask for a new budget, but cannot reset the one already committed.
    attempt_gpu_h = float(inputs.get("cost_gpu_h", 0.5))
    if "bringup_budget_hours" not in ctx.state:
        ctx.state["bringup_budget_hours"] = float(inputs.get("budget_hours", 8.0))
    budget_hours = float(ctx.state["bringup_budget_hours"])
    total_gpu_h = float(ctx.state.get("bringup_total_gpu_h", 0.0)) + attempt_gpu_h
    attempt_wall_s = time.monotonic() - started
    total_wall_s = float(ctx.state.get("bringup_total_wall_s", 0.0)) + attempt_wall_s
    ctx.state["bringup_total_gpu_h"] = total_gpu_h
    ctx.state["bringup_total_wall_s"] = total_wall_s
    cost_is_sane = (
        math.isfinite(attempt_gpu_h)
        and attempt_gpu_h >= 0
        and math.isfinite(budget_hours)
        and budget_hours >= 0
        and math.isfinite(total_gpu_h)
    )
    within_budget = cost_is_sane and total_gpu_h <= budget_hours

    if not result.ok:
        attempt_status = "failed"
        attempt_reason = result.detail or "environment build failed"
    elif not build_ok:
        attempt_status, attempt_reason = (
            "failed",
            "environment build produced no generation",
        )
    elif not spec_matches:
        attempt_status, attempt_reason = (
            "failed",
            f"installed packages do not match the spec {_TOOL_SPEC!r}",
        )
    elif canary_exit != 0:
        attempt_status, attempt_reason = "failed", f"canary exited {canary_exit}"
    elif not canary_stdout.strip():
        attempt_status, attempt_reason = "failed", "canary produced no output"
    elif not parse_ok:
        attempt_status, attempt_reason = "failed", "canary output does not parse"
    elif not downstream_ok:
        attempt_status, attempt_reason = "failed", "downstream consumer refused"
    elif not package_present:
        attempt_status, attempt_reason = (
            "failed",
            "installed packages do not match the spec",
        )
    elif not within_budget:
        attempt_status, attempt_reason = (
            "failed",
            f"cumulative cost exceeds declared budget: {total_gpu_h} > {budget_hours}",
        )
    else:
        attempt_status, attempt_reason = "passed", ""

    attempt_ok = attempt_status == "passed" and within_budget

    attempts = ctx.state.setdefault("bringup_attempts", [])
    attempts.append(
        {
            "status": attempt_status,
            "reason": attempt_reason,
            "wall_s": attempt_wall_s,
            "gpu_h": attempt_gpu_h,
        }
    )
    attempt_statuses = [str(attempt.get("status")) for attempt in attempts]
    recovered = bool(
        attempt_ok
        and len(attempts) > 1
        and any(attempt.get("status") == "failed" for attempt in attempts[:-1])
    )

    # 6. Freeze the record.
    reasons = ["weights verified", "canary parseable", "downstream consumed"]
    record = {
        "schema_version": bringup.SCHEMA_VERSION,
        "tool": {
            "name": "design-tool",
            "version": "1.0.0",
            "source": "https://github.com/openai4s/offline-design-tool",
            "revision": "abc123",
            "adapter": {
                "path": "bringup/adapter.py",
                "sha256": hashlib.sha256(adapter_bytes).hexdigest(),
                "size": len(adapter_bytes),
            },
            "env_name": name,
            "env_generation": generation.id if generation else None,
        },
        "weights": [
            {
                "path": "weights/model.weights",
                "sha256": weights_sha256,
                "size": len(weights_bytes),
                "source": "https://example.com/design-tool/weights",
                "verified": True,
            }
        ],
        "canary": {
            "target": target,
            "command": canary_command,
            "outputs": (
                [
                    {
                        "path": "bringup/canary_output.json",
                        # Hash the bytes actually frozen on disk. Text-mode
                        # newline translation differs on Windows, so hashing
                        # ``canary_stdout.encode()`` would record a digest for
                        # bytes the artifact never contained there.
                        "sha256": hashlib.sha256(
                            canary_output_path.read_bytes()
                        ).hexdigest(),
                    }
                ]
                if canary_exit == 0 and canary_stdout.strip()
                else []
            ),
            "parse": {
                "status": "ok" if parse_ok else "failed",
                "format": "json",
                "fields": list(required_fields),
                "reason": "" if parse_ok else attempt_reason,
            },
            "downstream": {
                "consumer": "sequence-design",
                "status": (
                    "passed"
                    if downstream_ok
                    else "refused" if inputs.get("refuse_downstream") else "failed"
                ),
                "output": "bringup/downstream_result.json" if downstream_ok else None,
                "sha256": (
                    hashlib.sha256(downstream_path.read_bytes()).hexdigest()
                    if downstream_ok
                    else None
                ),
            },
        },
        "admission": {
            "status": "verified" if attempt_ok else "refused",
            "reasons": reasons if attempt_ok else [attempt_reason],
        },
        "runtime": {
            "wall_s": total_wall_s,
            "attempts": list(attempts),
        },
        "cost": {"gpu_h": total_gpu_h, "budget_hours": budget_hours},
    }
    record = bringup.seal_record(record)
    (record_dir / bringup.BRINGUP_FILENAME).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "admitted": attempt_ok,
        "attempts": len(attempts),
        "attempt_statuses": attempt_statuses,
        "recovered": recovered,
        "weights_sha256": weights_sha256,
        "weights_verified": 1,
        "parse": parse_ok,
        "parse_fields": sum(
            1
            for field in required_fields
            if isinstance(parsed, dict) and field in parsed
        ),
        "downstream": downstream_ok,
        "env_generation": generation.id if generation else None,
        "record_sha256": record["record_sha256"],
        "canary_exit": canary_exit,
        "pkgscan_ok": pkgscan_ok,
        "runtime_wall_s": total_wall_s,
        "cost_gpu_h": total_gpu_h,
        "budget_hours": budget_hours,
        "attempt_reason": attempt_reason,
    }


def verify_bringup_step(ctx: Context, inputs: dict) -> dict:
    """The workflow benchmark gate. A frozen bring-up record that fails
    verification refuses the workflow — every bring-up failure case scores
    here, on this one refusal point, rather than being given its own mechanism."""
    from openai4s.benchmark import bringup

    expected_weights = inputs.get(
        "expected_weights",
        {"weights/model.weights": _REFERENCE_WEIGHTS_SHA256},
    )
    report = bringup.verify_bringup(
        ctx.root,
        expected_weights=expected_weights,
    )
    problems = list(report["problems"])
    if not report.get("admitted"):
        problems.append("admission: bring-up record was not admitted")
        attempt_reasons = report.get("attempt_reasons")
        if isinstance(attempt_reasons, list) and attempt_reasons:
            reason = attempt_reasons[-1]
            if isinstance(reason, str) and reason:
                problems.append("attempt: " + reason)
    if not report["ok"] or not report.get("admitted"):
        raise RuntimeError("bringup record failed verification: " + "; ".join(problems))
    return {
        "admitted": report["admitted"],
        "problems": len(report["problems"]),
        "checks": report["checks"],
        "record_sha256": report["record_sha256"],
        "weights_verified": report["weights_verified"],
        "canary_parse": report["canary_parse"],
        "downstream": report["downstream"],
        "admission": report["admission"],
        "attempts": report["attempts"],
        "attempt_statuses": report["attempt_statuses"],
        "recovered": report["recovered"],
        "runtime_wall_s": report["runtime_wall_s"],
        "cost_gpu_h": report["cost_gpu_h"],
    }


def tamper_bringup(ctx: Context, inputs: dict) -> dict:
    """Flip, delete, or forge a frozen bring-up artifact.

    The forge is the interesting action: it rewrites the file *and* the
    record's own digest and re-seals the record, so every internal check
    passes — only the evaluator-held reference digests notice, which is why
    ``verify_bringup`` accepts them.
    """
    from openai4s.benchmark import bringup

    root = ctx.root
    record_path = root / bringup.RECORD_DIR / bringup.BRINGUP_FILENAME
    record = json.loads(record_path.read_text(encoding="utf-8"))
    target = inputs["target"]
    action = inputs.get("action", "flip")
    if target == "weights":
        entry = record["weights"][0]
    elif target == "canary":
        entry = record["canary"]["outputs"][0]
    elif target == "downstream":
        entry = record["canary"]["downstream"]
    else:
        raise ValueError(f"unknown tamper target {target!r}")
    artifact_key = "output" if target == "downstream" else "path"
    path = root / entry[artifact_key]
    if action == "flip":
        data = path.read_bytes()
        path.write_bytes(data[:-1] + bytes([data[-1] ^ 0x01]))
    elif action == "delete":
        path.unlink()
    elif action == "forge":
        data = path.read_bytes()
        forged = data[:-1] + bytes([data[-1] ^ 0x01])
        path.write_bytes(forged)
        entry["sha256"] = hashlib.sha256(forged).hexdigest()
        entry["size"] = len(forged)

        # A full weights forgery has to preserve every internal relationship.
        # Rewriting only the weights entry leaves the canary and downstream
        # proofs naming the old digest; the semantic verifier would catch that
        # without needing the evaluator-held reference, so it would not test
        # the trust seam this action exists to exercise.
        if target == "weights":
            forged_digest = entry["sha256"]
            canary_entry = record["canary"]["outputs"][0]
            canary_path = root / canary_entry["path"]
            canary_payload = json.loads(canary_path.read_text(encoding="utf-8"))
            canary_payload["weights_sha256"] = forged_digest
            canary_bytes = (json.dumps(canary_payload, sort_keys=True) + "\n").encode(
                "utf-8"
            )
            canary_path.write_bytes(canary_bytes)
            canary_entry["sha256"] = hashlib.sha256(canary_bytes).hexdigest()
            canary_entry["size"] = len(canary_bytes)

            downstream_entry = record["canary"]["downstream"]
            downstream_path = root / downstream_entry["output"]
            downstream_payload = json.loads(downstream_path.read_text(encoding="utf-8"))
            downstream_payload["consumed_weights_sha256"] = forged_digest
            downstream_bytes = json.dumps(downstream_payload, sort_keys=True).encode(
                "utf-8"
            )
            downstream_path.write_bytes(downstream_bytes)
            downstream_entry["sha256"] = hashlib.sha256(downstream_bytes).hexdigest()
            downstream_entry["size"] = len(downstream_bytes)
        record = bringup.seal_record(record)
        record_path.write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    else:
        raise ValueError(f"unknown tamper action {action!r}")
    return {"tampered": str(path), "action": action, "target": target}


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

    from openai4s.compute import registry
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
        # The scenario stands in for a deployment that has this host, so it
        # registers it. `submit` refuses an unregistered destination before it
        # spawns ssh, and a benchmark that skipped registration would be
        # measuring a path no agent can take.
        registry.add_host("bench", data_dir=Path(cfg.data_dir))
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


class _RecordingTransport:
    """Stands in for the socket, and for nothing above it.

    Handed to `telemetry.sender.send` through its transport seam, so the
    consent row, the sealed-payload type, the install-id comparison and the
    endpoint validation are all the real code — which is the whole point of the
    case — while the bytes go into this list instead of onto a wire.
    """

    def __init__(self) -> None:
        self.requests: list[Any] = []

    def __call__(self, request: Any) -> int:
        self.requests.append(request)
        return 200


def telemetry_identity_cycle(ctx: Context, inputs: dict) -> dict:
    """Grant, seal, revoke, re-grant — and try to send the stale payload.

    The transport is recorded rather than opened, for the same reason
    `science_query`'s upstream body is recorded rather than fetched: a benchmark
    must not depend on the network. Here it is not merely a dependency. `send`
    is the only function in the tree that transmits, and the identity check this
    case exists to exercise sits *above* the socket — so `send`'s transport seam
    runs every refusal for real (consent row, sealed type, install-id equality,
    endpoint validation) while nothing leaves the machine.

    Without it the `revoke: false` case granted consent on the operator's own
    behalf, sealed a payload under that just-minted identity, and really POSTed
    it to the built-in endpoint — from every developer machine and CI runner
    that ran `uv run pytest`, whose user had consented to nothing. The case
    asserted only `identity`, so the send was an unobserved side effect and the
    suite stayed green over it.

    `transport_reached` is what the `revoke: false` case is actually about: not
    that a packet went out, but that the identity gate let the payload through
    to the transport instead of refusing it.
    """
    from openai4s.telemetry import consent as consent_mod
    from openai4s.telemetry import sender as sender_mod
    from openai4s.telemetry import wire

    first = consent_mod.grant(ctx.store)
    if first is None:
        raise RuntimeError("consent could not be granted")
    stale = wire.seal(first.install_id, [{"event": "daemon_start", "outcome": "ok"}])

    transport = _RecordingTransport()
    if not inputs.get("revoke", True):
        sent = sender_mod.send(ctx.store, stale, transport=transport)
        return {
            "sent": sent,
            "identity": "current",
            "transport_reached": bool(transport.requests),
        }
    consent_mod.revoke(ctx.store)
    second = consent_mod.grant(ctx.store)
    if second is None or second.install_id == first.install_id:
        raise RuntimeError("re-granting did not mint a fresh identity")
    sent = sender_mod.send(ctx.store, stale, transport=transport)
    return {
        "sent": sent,
        "identity": "revoked",
        "ids_differ": True,
        # The refusal is what this case proves, so it must be visible that the
        # payload never reached the transport at all.
        "transport_reached": bool(transport.requests),
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
    # A bring-up retry runs the same function under a second step name, because
    # the runner keys a step's inputs by its name — reusing the name would feed
    # the retry the first run's failure flags.
    "tool_bringup": tool_bringup,
    "tool_bringup_retry": tool_bringup,
    "verify_bringup": verify_bringup_step,
    "tamper_bringup": tamper_bringup,
    "remote_job": remote_job,
    "science_query": science_query,
    "connector_drift_check": connector_drift_check,
    "host_file_write": host_file_write,
    "telemetry_identity_cycle": telemetry_identity_cycle,
}


__all__ = ["Context", "SkipCase", "STEPS", "make_context"]
