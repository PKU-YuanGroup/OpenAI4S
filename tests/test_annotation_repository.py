"""Direct contracts for image-annotation persistence."""

from __future__ import annotations

import threading

from openai4s.config import Config
from openai4s.store import get_store


def _store(tmp_path):
    return get_store(Config(data_dir=tmp_path).db_path)


def _add(store, frame_id, artifact_id="figure-a", **overrides):
    values = {
        "artifact_name": f"{artifact_id}.png",
        "rel_x": 0.25,
        "rel_y": 0.75,
        "body": "inspect this region",
    }
    values.update(overrides)
    return store.add_annotation(
        root_frame_id=frame_id,
        artifact_id=artifact_id,
        **values,
    )


def test_annotation_repository_shares_store_boundary_and_facade(tmp_path):
    store = _store(tmp_path)
    assert store._annotations._connection is store._conn
    assert store._annotations._lock is store._lock

    frame_a = store.new_frame(project_id="science")
    frame_b = store.new_frame(project_id="science")
    first = _add(store, frame_a, rel_x=-2, rel_y=4)
    second = _add(store, frame_a)
    other_artifact = _add(store, frame_a, "figure-b")
    other_frame = _add(store, frame_b)

    assert (first["rel_x"], first["rel_y"]) == (0.0, 1.0)
    assert [first["number"], second["number"]] == [1, 2]
    assert other_artifact["number"] == 1
    assert other_frame["number"] == 1
    assert store.get_annotation(first["annotation_id"]) == (
        store._annotations.get(first["annotation_id"])
    )
    assert [
        item["annotation_id"]
        for item in store.list_annotations(frame_a, artifact_id="figure-a")
    ] == [first["annotation_id"], second["annotation_id"]]

    before = store.get_annotation(first["annotation_id"])
    assert store.update_annotation(first["annotation_id"]) == before
    updated = store.update_annotation(first["annotation_id"], body="revised")
    assert updated["body"] == "revised"

    store.update_annotation(second["annotation_id"], status="dismissed")
    store.mark_annotations_sent([first["annotation_id"], second["annotation_id"], ""])
    assert store.get_annotation(first["annotation_id"])["status"] == "sent"
    assert store.get_annotation(second["annotation_id"])["status"] == "dismissed"
    store.mark_annotations_sent([])

    store.delete_annotation(first["annotation_id"])
    assert store.get_annotation(first["annotation_id"]) is None


def test_annotation_ordinals_are_atomic_with_concurrent_pins(tmp_path):
    store = _store(tmp_path)
    frame_id = store.new_frame(project_id="science")
    workers = 12
    barrier = threading.Barrier(workers)
    results = []
    result_lock = threading.Lock()

    def add_pin(index):
        barrier.wait()
        annotation = _add(store, frame_id, body=f"pin {index}")
        with result_lock:
            results.append(annotation)

    threads = [
        threading.Thread(target=add_pin, args=(index,)) for index in range(workers)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == workers
    assert sorted(annotation["number"] for annotation in results) == list(
        range(1, workers + 1)
    )


def test_annotation_cascades_stay_in_store_transactions(tmp_path):
    store = _store(tmp_path)
    project = store.create_project(name="Annotations", project_id="project-a")
    frame_id = store.new_frame(project_id=project["project_id"])
    _add(store, frame_id, "project-figure")

    store.delete_project(project["project_id"])
    assert store.list_annotations(frame_id) == []

    surviving_frame = store.new_frame(project_id="default")
    annotation = _add(store, surviving_frame, "deleted-artifact")
    store.delete_artifact(annotation["artifact_id"])
    assert store.get_annotation(annotation["annotation_id"]) is None
