"""Local-only fixture for the Stage 1 trusted-delivery browser acceptance.

The browser harness invokes this file in a separate process.  Keeping the
seeding code in Python lets the acceptance use the production Store,
ArtifactManager, completion renderer, and delivery verifier without sending a
model request or exposing a test-only HTTP route from the daemon.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from openai4s.config import Config
from openai4s.kernel import environments
from openai4s.kernel.readiness import (
    load_standard_profile_requirements,
    standard_profile_readiness,
)
from openai4s.server.artifacts import ArtifactManager, PromotionTarget
from openai4s.server.completions import completion_message
from openai4s.server.delivery import CompletionDeliveryService
from openai4s.store import get_store

_SCHEMA_VERSION = 1


def _json_dump(value: Any) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _guess_content_type(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def prepare_standard_environment_fixture(env_root: Path) -> dict[str, Any]:
    """Create metadata-only ``python`` and ``r`` prefixes under ``env_root``.

    Readiness intentionally inspects interpreter presence and local package
    metadata; it neither executes those interpreters nor imports their
    packages.  Python still points at the current runnable interpreter.  R
    points at an explicitly selected or PATH-resolved Rscript when available;
    only hosts without R receive a controlled executable stub.  This remains
    a metadata fixture, not a claim that the standard runtime was built.
    """

    root = env_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    requirements = load_standard_profile_requirements()
    runtime_markers: dict[str, str] = {}
    selected_rscript = os.environ.get("OPENAI4S_STAGE1_RSCRIPT") or shutil.which(
        "Rscript"
    )
    for name, packages in requirements.items():
        prefix = root / name
        bindir = prefix / "bin"
        metadata = prefix / "conda-meta"
        bindir.mkdir(parents=True, exist_ok=True)
        metadata.mkdir(parents=True, exist_ok=True)
        executable = bindir / ("Rscript" if name == "r" else "python")
        source = (
            Path(selected_rscript).expanduser().resolve()
            if name == "r" and selected_rscript
            else Path(sys.executable).resolve() if name == "python" else None
        )
        if source is not None and source.is_file() and os.access(source, os.X_OK):
            try:
                executable.symlink_to(source)
            except OSError:
                shutil.copy2(source, executable)
                executable.chmod(0o700)
            runtime_markers[name] = "local_rscript" if name == "r" else "current_python"
        else:
            executable.write_text(
                "#!/bin/sh\n"
                "echo 'metadata-only R acceptance fixture' >&2\n"
                "exit 97\n",
                encoding="utf-8",
            )
            executable.chmod(0o700)
            runtime_markers[name] = "controlled_stub"
        for index, package in enumerate(packages):
            (metadata / f"{index:03d}-{package}.json").write_text(
                json.dumps({"name": package}, sort_keys=True),
                encoding="utf-8",
            )
    return {
        "schema_version": _SCHEMA_VERSION,
        "fixture_kind": "metadata_fixture",
        "runtime_execution_verified": False,
        "environment_names": list(requirements),
        "required_package_counts": {
            name: len(packages) for name, packages in requirements.items()
        },
        "runtime_markers": runtime_markers,
    }


def inspect_prepared_standard_environment(env_root: Path) -> dict[str, Any]:
    """Run the same production discovery/readiness functions as the daemon."""

    previous_roots = os.environ.get("OPENAI4S_ENV_ROOTS")
    previous_generation_root = os.environ.get("OPENAI4S_ENV_GENERATIONS_ROOT")
    try:
        os.environ["OPENAI4S_ENV_ROOTS"] = str(env_root.expanduser().resolve())
        # Do not let an unrelated applied generation outside this disposable
        # fixture win the production discovery name collision.
        os.environ["OPENAI4S_ENV_GENERATIONS_ROOT"] = str(
            env_root.expanduser().resolve() / ".no-generations"
        )
        environments.invalidate_cache()
        result = standard_profile_readiness(
            enabled=True,
            discover=lambda: environments.discover_environments(force=True),
        )
    finally:
        if previous_roots is None:
            os.environ.pop("OPENAI4S_ENV_ROOTS", None)
        else:
            os.environ["OPENAI4S_ENV_ROOTS"] = previous_roots
        if previous_generation_root is None:
            os.environ.pop("OPENAI4S_ENV_GENERATIONS_ROOT", None)
        else:
            os.environ["OPENAI4S_ENV_GENERATIONS_ROOT"] = previous_generation_root
        environments.invalidate_cache()
    return result


def _artifact_manager(data_dir: Path, store: Any) -> ArtifactManager:
    workspaces = data_dir / "agent-workspaces"

    def workspace_for(frame_id: str) -> Path:
        workspace = workspaces / frame_id
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    return ArtifactManager(
        data_dir=data_dir,
        store=store,
        workspace_for=workspace_for,
        broadcast=lambda _frame_id, _event: None,
        guess_content_type=_guess_content_type,
        checksum=_sha256,
        trusted_delivery=True,
    )


def _register(
    manager: ArtifactManager,
    session: PromotionTarget,
    *,
    filename: str,
    content: bytes,
    cell_id: str | None,
    producer_frame_id: str | None = None,
) -> dict[str, Any]:
    target = session.workspace / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    record = manager.register_file(
        session,
        target,
        cell_id,
        lambda _event: None,
        producer_frame_id=producer_frame_id,
    )
    if not isinstance(record, dict):
        raise RuntimeError("production ArtifactManager did not register fixture bytes")
    return record


def seed_trusted_delivery_fixture(
    data_dir: Path,
    *,
    project_id: str,
    frame_id: str,
    count: int = 100,
) -> dict[str, Any]:
    """Create exact-version deliveries, dedup evidence, and a moved head."""

    if count != 100:
        raise ValueError("the Stage 1 Go/No-Go fixture requires exactly 100 links")
    root = data_dir.expanduser().resolve()
    cfg = Config(data_dir=root)
    store = get_store(cfg.db_path)
    manager = _artifact_manager(root, store)
    workspace = root / "agent-workspaces" / frame_id
    workspace.mkdir(parents=True, exist_ok=True)
    session = PromotionTarget(
        root_frame_id=frame_id,
        project_id=project_id,
        workspace=workspace,
    )
    delivery = CompletionDeliveryService(store=store, data_dir=root)
    expected: list[dict[str, Any]] = []
    first: dict[str, Any] | None = None

    try:
        for ordinal in range(1, count + 1):
            filename = f"stage1-delivery-{ordinal:03d}.txt"
            content = f"stage1 trusted delivery {ordinal:03d}\n".encode("utf-8")
            record = _register(
                manager,
                session,
                filename=filename,
                content=content,
                cell_id=f"cell-delivery-{ordinal:03d}",
            )
            if first is None:
                first = dict(record)
            message = completion_message(
                {"output": {"summary": f"Verified delivery {ordinal:03d}."}},
                [record],
                require_fallback=False,
                trusted_delivery=True,
            )
            verified = delivery.build_manifest(
                root_frame_id=frame_id,
                project_id=project_id,
                versions=[record],
            )
            entry = verified.value["artifacts"][0]
            committed = delivery.commit_verified_manifest(
                verified=verified,
                idempotency_key=f"stage1-browser-delivery-{ordinal:03d}",
                root_frame_id=frame_id,
                branch_id=frame_id,
                frame_id=frame_id,
                content=message,
            )
            store.mark_completion_delivery_published(committed["delivery_id"])
            expected.append(
                {
                    "ordinal": ordinal,
                    "artifact_id": entry["artifact_id"],
                    "version_id": entry["version_id"],
                    "url": entry["url"],
                    "sha256": entry["sha256"],
                    "size_bytes": entry["size_bytes"],
                }
            )

        # Same path and same bytes from a different Cell: one immutable byte
        # version, two producer observations.
        dedup_bytes = b"same bytes, independent producing cells\n"
        dedup_first = _register(
            manager,
            session,
            filename="stage1-dedup.txt",
            content=dedup_bytes,
            cell_id="cell-dedup-first",
        )
        dedup_second = _register(
            manager,
            session,
            filename="stage1-dedup.txt",
            content=dedup_bytes,
            cell_id="cell-dedup-second",
        )
        code_child = store.new_frame(
            parent_id=frame_id,
            project_id=project_id,
            kind="delegate",
            name="Stage 1 delegated code producer",
            depth=1,
            status="done",
        )
        native_child = store.new_frame(
            parent_id=frame_id,
            project_id=project_id,
            kind="delegate",
            name="Stage 1 delegated native producer",
            depth=1,
            status="done",
        )
        delegated_code = _register(
            manager,
            session,
            filename="stage1-delegated-code.txt",
            content=b"delegated code producer bytes\n",
            cell_id="cell-delegated-browser",
            producer_frame_id=code_child,
        )
        delegated_native = _register(
            manager,
            session,
            filename="stage1-delegated-native.txt",
            content=b"delegated native producer bytes\n",
            cell_id=None,
            producer_frame_id=native_child,
        )
        # Move the first Artifact's mutable head after its completion message is
        # durable.  The old message must continue to address the first immutable
        # version and its original checksum.
        if first is None:  # pragma: no cover - count is fixed at 100
            raise RuntimeError("trusted delivery fixture created no Artifact")
        old_version_id = first["version_id"]
        old_sha256 = first["checksum"]
        changed = _register(
            manager,
            session,
            filename=first["filename"],
            content=b"stage1 changed mutable head\n",
            cell_id="cell-head-changed",
        )
        # Reopen through a fresh Store generation.  This proves the result does
        # not depend on the fixture process's repository objects or caches.
        store.close()
        store = get_store(cfg.db_path)
        messages = store.list_messages(frame_id, limit=None)
        linked_messages = [
            row
            for row in messages
            if row.get("role") == "assistant"
            and "/api/v1/artifacts/" in str(row.get("content") or "")
        ]
        reopened_deliveries = [
            store.get_completion_delivery(
                json.loads(row["metadata"])["completion_delivery"]["delivery_id"]
            )
            for row in linked_messages
        ]
        if any(row is None for row in reopened_deliveries):
            raise RuntimeError("a persisted completion delivery did not reopen")
        versions = store.list_versions(dedup_first["artifact_id"])
        observations = store.list_artifact_capture_observations(
            artifact_id=dedup_first["artifact_id"]
        )
        first_artifact = store.get_artifact(first["artifact_id"])

        return {
            "schema_version": _SCHEMA_VERSION,
            "message_count": len(linked_messages),
            "delivery_count": len(reopened_deliveries),
            "expected": expected,
            "dedup": {
                "artifact_id": dedup_first["artifact_id"],
                "same_version_id": (
                    dedup_first["version_id"] == dedup_second["version_id"]
                ),
                "version_count": len(versions),
                "observation_count": len(observations),
                "producing_cell_ids": [
                    row.get("producing_cell_id") for row in observations
                ],
                "capture_kinds": [row.get("capture_kind") for row in observations],
            },
            "delegated_provenance": {
                "code": {
                    "artifact_id": delegated_code["artifact_id"],
                    "filename": delegated_code["filename"],
                    "frame_id": code_child,
                    "producing_cell_id": "cell-delegated-browser",
                },
                "native": {
                    "artifact_id": delegated_native["artifact_id"],
                    "filename": delegated_native["filename"],
                    "frame_id": native_child,
                },
            },
            "head_change": {
                "artifact_id": first["artifact_id"],
                "old_version_id": old_version_id,
                "old_sha256": old_sha256,
                "new_version_id": changed["version_id"],
                "new_sha256": changed["checksum"],
                "latest_version_id": (
                    first_artifact.get("latest_version_id")
                    if isinstance(first_artifact, dict)
                    else None
                ),
            },
        }
    finally:
        store.close()


def test_metadata_fixture_is_found_by_production_readiness(tmp_path, monkeypatch):
    env_root = tmp_path / "fixture-envs"
    prepared = prepare_standard_environment_fixture(env_root)
    monkeypatch.setenv("OPENAI4S_ENV_ROOTS", str(env_root))
    monkeypatch.setenv(
        "OPENAI4S_ENV_GENERATIONS_ROOT", str(tmp_path / "no-generations")
    )
    environments.invalidate_cache()

    result = standard_profile_readiness(
        enabled=True,
        discover=lambda: environments.discover_environments(force=True),
    )

    assert prepared["required_package_counts"] == {"python": 33, "r": 8}
    assert prepared["fixture_kind"] == "metadata_fixture"
    assert prepared["runtime_execution_verified"] is False
    assert prepared["runtime_markers"]["python"] == "current_python"
    assert prepared["runtime_markers"]["r"] in {
        "local_rscript",
        "controlled_stub",
    }
    assert result["ready"] is True
    assert result["state"] == "ready"
    assert result["missing_environments"] == []
    assert result["missing_packages"] == {}
    assert result["network_contacted"] is False
    assert result["mutation_performed"] is False


def test_trusted_delivery_fixture_seeds_exact_messages_dedup_and_old_head(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    project = store.create_project(name="stage1", description="", context="")
    frame_id = store.new_frame(
        kind="turn", project_id=project["project_id"], status="ready"
    )
    store.close()

    result = seed_trusted_delivery_fixture(
        tmp_path,
        project_id=project["project_id"],
        frame_id=frame_id,
    )

    assert result["message_count"] == 100
    assert result["delivery_count"] == 100
    assert len(result["expected"]) == 100
    assert len({row["url"] for row in result["expected"]}) == 100
    assert all(
        row["url"].startswith("/api/v1/artifacts/versions/v-")
        for row in result["expected"]
    )
    assert result["dedup"] == {
        "artifact_id": result["dedup"]["artifact_id"],
        "same_version_id": True,
        "version_count": 1,
        "observation_count": 2,
        "producing_cell_ids": ["cell-dedup-first", "cell-dedup-second"],
        "capture_kinds": ["version_created", "head_checksum_reused"],
    }
    assert result["delegated_provenance"]["code"]["producing_cell_id"] == (
        "cell-delegated-browser"
    )
    assert result["delegated_provenance"]["code"]["frame_id"] != frame_id
    assert result["delegated_provenance"]["native"]["frame_id"] != frame_id
    assert (
        result["head_change"]["old_version_id"]
        != result["head_change"]["new_version_id"]
    )
    assert (
        result["head_change"]["latest_version_id"]
        == result["head_change"]["new_version_id"]
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--env-root", required=True, type=Path)
    inspect = subparsers.add_parser("inspect-readiness")
    inspect.add_argument("--env-root", required=True, type=Path)
    seed = subparsers.add_parser("seed")
    seed.add_argument("--data-dir", required=True, type=Path)
    seed.add_argument("--project-id", required=True)
    seed.add_argument("--frame-id", required=True)
    seed.add_argument("--count", type=int, default=100)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.command == "prepare":
        value = prepare_standard_environment_fixture(args.env_root)
    elif args.command == "inspect-readiness":
        value = inspect_prepared_standard_environment(args.env_root)
    else:
        value = seed_trusted_delivery_fixture(
            args.data_dir,
            project_id=args.project_id,
            frame_id=args.frame_id,
            count=args.count,
        )
    _json_dump(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
