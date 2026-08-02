"""A pinned figure is bound to one immutable artifact VERSION.

An annotation is a statement about a picture: the user clicked a point on the
image in front of them and wrote a sentence about that point. The send path
resolved `artifact_id`, which SQLite answers with the artifact's *latest*
version -- so an agent that re-plotted between the pin and the send handed the
model different bytes while the pin coordinates still described the old figure.
Nothing anywhere said so. That is the failure this file exists to prevent:
wrong, not absent, and therefore believed.

Three properties are pinned here:

  * the bytes that reach the provider are the bytes that were pinned, even
    after the file on disk has been overwritten;
  * when the pinned bytes cannot be produced, nothing is substituted -- the
    reason reaches both the UI and the model, before the provider call;
  * what a file *is* is decided by its magic number, not by its name.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
from base64 import b64decode
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.store import Store

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\ntrailer\n%%EOF\n"


class _Hub:
    def __init__(self):
        self.events = []

    def emitter(self, root_frame_id):
        return lambda event: self.events.append(event)

    def broadcast(self, root_frame_id, event):
        self.events.append(event)


def _cfg(tmp_path):
    return Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )


def _runner(tmp_path, hub):
    return gateway_mod.SessionRunner(_cfg(tmp_path), hub, start_idle_sweeper=False)


def _png(width: int, height: int, colour: tuple[int, int, int]) -> bytes:
    """A real PNG of an exact size. Size is the discriminator the assertions
    use: it survives the re-encode that draws the pin markers, so the shape of
    the image that arrives identifies which version produced it."""
    pytest.importorskip("PIL", reason="pin markers are drawn with Pillow")
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def _png_size(raw: bytes) -> tuple[int, int]:
    """Read width/height out of the IHDR chunk, without decoding the image."""
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    return struct.unpack(">II", raw[16:24])


def _record(store, path: Path, data: bytes, *, frame_id: str, filename=None) -> dict:
    path.write_bytes(data)
    return store.record_cell_artifact(
        path=str(path),
        filename=filename or path.name,
        content_type="image/png",
        size_bytes=len(data),
        checksum=hashlib.sha256(data).hexdigest(),
        producing_cell_id=None,
        frame_id=frame_id,
        root_frame_id=frame_id,
        project_id="p",
    )


def _pin_through_the_route(cfg, runner, frame_id: str, artifact_id: str) -> dict:
    """Create the annotation the way a browser does, through `_route`.

    Calling `store.add_annotation` directly would let the binding be supplied by
    the test rather than by the server, which is the one thing worth proving.
    """
    import io

    from openai4s.server import local_auth

    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    body = json.dumps(
        {
            "artifact_id": artifact_id,
            "artifact_name": "figure.png",
            "x": 0.5,
            "y": 0.5,
            "body": "make this bar wider",
        }
    ).encode()
    handler.headers = {
        local_auth.TOKEN_HEADER: local_auth.load_or_mint(cfg.data_dir),
        "Content-Length": str(len(body)),
    }
    handler.rfile = io.BytesIO(body)
    handler.close_connection = False
    handler.path = f"/api/v1/frames/{frame_id}/annotations"
    replies: list = []
    handler._json = lambda obj, code=200: replies.append((code, obj))
    handler._route("POST")
    assert replies and replies[-1][0] == 201, replies
    return replies[-1][1]["annotation"]


@pytest.fixture
def _vision(monkeypatch):
    from openai4s import llm

    monkeypatch.setattr(llm, "supports_vision_for", lambda cfg: True)


def _content(runner, hub, annos, text="what about this?"):
    state = runner._state(runner.store.new_frame(kind="turn", project_id="p"), "p")
    return runner._build_annotated_content(state, text, annos)


def _images(parts):
    return [p for p in parts if isinstance(p, dict) and p.get("type") == "image"]


def _problem_reasons(hub):
    events = [e for e in hub.events if e.get("type") == "attachment_problems"]
    assert events, "nothing was reported to the UI"
    return {p["reason"] for p in events[-1]["problems"]}


def _model_note(parts):
    notes = [
        p["text"]
        for p in parts
        if isinstance(p, dict)
        and p.get("type") == "text"
        and "were NOT sent" in p.get("text", "")
    ]
    assert notes, "the model was not told anything was missing"
    return notes[-1]


# ---------------------------------------------------------------------------
# (1) the bytes are bound to a version
# ---------------------------------------------------------------------------


def test_the_pin_records_the_version_the_user_was_looking_at(tmp_path):
    """The binding is written by the server, on the real POST route."""
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = _runner(tmp_path, hub)
    try:
        store = runner.store
        frame_id = store.new_frame(kind="turn", project_id="p")
        first = _record(
            store, tmp_path / "figure.png", _png(64, 16, (1, 2, 3)), frame_id=frame_id
        )

        pinned = _pin_through_the_route(cfg, runner, frame_id, first["artifact_id"])
        stored = store.get_annotation(pinned["annotation_id"])

        assert stored["version_id"] == first["version_id"]
        assert stored["checksum"] == first["checksum"]
        # And it is visible in the API projection, so a client can tell a pin on
        # the figure now on screen from one taken two re-plots ago.
        assert pinned["version_id"] == first["version_id"]
    finally:
        runner.close()


def test_the_sent_bytes_are_the_pinned_version_after_the_figure_is_overwritten(
    tmp_path, _vision
):
    """The property everything else here exists to protect.

    The pinned figure is 64x16; the one that replaces it at the same path is
    16x64. What arrives at the provider must be 64x16 -- the picture the pin
    coordinates actually describe.
    """
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = _runner(tmp_path, hub)
    try:
        store = runner.store
        frame_id = store.new_frame(kind="turn", project_id="p")
        live = tmp_path / "figure.png"
        pinned_bytes = _png(64, 16, (10, 20, 30))
        first = _record(store, live, pinned_bytes, frame_id=frame_id)
        pin = _pin_through_the_route(cfg, runner, frame_id, first["artifact_id"])

        # What the daemon does before a later cell overwrites a live file
        # (ArtifactManager.protect_latest): freeze the superseded version's
        # bytes, then let the path be rewritten.
        frozen = tmp_path / "frozen.png"
        frozen.write_bytes(pinned_bytes)
        store.set_version_snapshot(first["version_id"], str(frozen))
        second = _record(store, live, _png(16, 64, (200, 100, 50)), frame_id=frame_id)
        assert second["version_id"] != first["version_id"]
        assert store.get_artifact(first["artifact_id"])["latest_version_id"] == (
            second["version_id"]
        )

        parts = _content(runner, hub, [store.get_annotation(pin["annotation_id"])])

        images = _images(parts)
        assert len(images) == 1, parts
        assert _png_size(b64decode(images[0]["data"])) == (
            64,
            16,
        ), "the model received the figure that replaced the pinned one"
        assert not [e for e in hub.events if e.get("type") == "attachment_problems"]
    finally:
        runner.close()


def test_an_overwritten_live_file_is_refused_rather_than_substituted(tmp_path, _vision):
    """No frozen copy exists, so the pinned bytes are simply gone.

    The recorded checksum is what notices. Sending the new file under the old
    pin's coordinates -- the previous behaviour -- is the failure; sending
    nothing and saying so is the fix.
    """
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = _runner(tmp_path, hub)
    try:
        store = runner.store
        frame_id = store.new_frame(kind="turn", project_id="p")
        live = tmp_path / "figure.png"
        first = _record(store, live, _png(64, 16, (10, 20, 30)), frame_id=frame_id)
        pin = _pin_through_the_route(cfg, runner, frame_id, first["artifact_id"])

        live.write_bytes(_png(16, 64, (7, 7, 7)))  # re-plotted in place

        parts = _content(runner, hub, [store.get_annotation(pin["annotation_id"])])

        assert _images(parts) == []
        assert _problem_reasons(hub) == {"version_changed"}
        assert "version_changed" in _model_note(parts)
    finally:
        runner.close()


def test_a_pin_with_no_recorded_version_still_resolves_the_artifact(tmp_path, _vision):
    """Rows written before the binding columns existed keep working.

    Deliberate, and the only case where "whatever the file holds now" remains
    the best available answer: which version such a row was taken against is
    recorded nowhere, and refusing it would delete a user's pending pins on
    upgrade. Every new pin is bound (see the tests above).
    """
    _cfg(tmp_path)
    hub = _Hub()
    runner = _runner(tmp_path, hub)
    try:
        store = runner.store
        frame_id = store.new_frame(kind="turn", project_id="p")
        first = _record(
            store, tmp_path / "legacy.png", _png(48, 12, (5, 5, 5)), frame_id=frame_id
        )
        legacy = store.add_annotation(
            root_frame_id=frame_id,
            artifact_id=first["artifact_id"],
            artifact_name="legacy.png",
            rel_x=0.5,
            rel_y=0.5,
            body="unbound",
        )
        assert legacy["version_id"] is None

        parts = _content(runner, hub, [legacy])

        images = _images(parts)
        assert len(images) == 1
        assert _png_size(b64decode(images[0]["data"])) == (48, 12)
    finally:
        runner.close()


# ---------------------------------------------------------------------------
# (2) the bytes decide the type
# ---------------------------------------------------------------------------


def test_the_magic_number_decides_the_type_not_the_filename(tmp_path, _vision):
    """Both directions, because both were wrong.

    `figure.dat` holding a PNG was skipped for its extension; `figure.png`
    holding a PDF was handed to the decoder and dropped with no reason given.
    """
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = _runner(tmp_path, hub)
    try:
        store = runner.store
        frame_id = store.new_frame(kind="turn", project_id="p")
        genuine = _record(
            store,
            tmp_path / "figure.dat",
            _png(32, 8, (1, 1, 1)),
            frame_id=frame_id,
            filename="figure.dat",
        )
        pinned = _pin_through_the_route(cfg, runner, frame_id, genuine["artifact_id"])

        parts = _content(runner, hub, [store.get_annotation(pinned["annotation_id"])])
        images = _images(parts)
        assert len(images) == 1, "a real PNG was skipped because of its name"
        assert _png_size(b64decode(images[0]["data"])) == (32, 8)

        liar = tmp_path / "not-really.png"
        liar.write_bytes(PDF_BYTES)
        recorded = store.record_cell_artifact(
            path=str(liar),
            filename="not-really.png",
            content_type="image/png",
            size_bytes=len(PDF_BYTES),
            checksum=hashlib.sha256(PDF_BYTES).hexdigest(),
            producing_cell_id=None,
            frame_id=frame_id,
            root_frame_id=frame_id,
            project_id="p",
        )
        liar_pin = _pin_through_the_route(
            cfg, runner, frame_id, recorded["artifact_id"]
        )

        hub.events.clear()
        parts = _content(runner, hub, [store.get_annotation(liar_pin["annotation_id"])])

        assert _images(parts) == []
        assert _problem_reasons(hub) == {"unsupported_type"}
        assert "unsupported_type" in _model_note(parts)
    finally:
        runner.close()


def test_a_sniff_accepts_the_shipped_raster_types_and_nothing_else():
    """The helper itself, on the byte patterns it exists to tell apart."""
    assert gateway_mod._sniff_image_mime(b"\x89PNG\r\n\x1a\n rest") == "image/png"
    assert gateway_mod._sniff_image_mime(b"\xff\xd8\xff\xe0 rest") == "image/jpeg"
    assert gateway_mod._sniff_image_mime(b"GIF89a rest") == "image/gif"
    webp = b"RIFF" + b"\x00" * 4 + b"WEBPVP8 "
    assert gateway_mod._sniff_image_mime(webp) == "image/webp"
    assert gateway_mod._sniff_image_mime(PDF_BYTES) is None
    assert gateway_mod._sniff_image_mime(b"<svg xmlns=...></svg>") is None
    # "BM" alone is two bytes of ordinary text; a BMP also states its own total
    # length, and that is what separates the two.
    text = b"BM" + b"y the way, this is prose and not a bitmap at all."
    assert gateway_mod._sniff_image_mime(text) is None
    bmp = b"BM" + struct.pack("<I", 30) + b"\x00" * 24
    assert gateway_mod._sniff_image_mime(bmp) == "image/bmp"


# ---------------------------------------------------------------------------
# (4) every refusal reaches both the UI and the model
# ---------------------------------------------------------------------------


def test_a_deleted_figure_is_reported_to_both_channels(tmp_path, _vision):
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = _runner(tmp_path, hub)
    try:
        store = runner.store
        frame_id = store.new_frame(kind="turn", project_id="p")
        live = tmp_path / "gone.png"
        first = _record(store, live, _png(20, 20, (9, 9, 9)), frame_id=frame_id)
        pinned = _pin_through_the_route(cfg, runner, frame_id, first["artifact_id"])
        live.unlink()

        parts = _content(runner, hub, [store.get_annotation(pinned["annotation_id"])])

        assert _images(parts) == []
        assert _problem_reasons(hub) == {"not_found"}
        assert "not_found" in _model_note(parts)
    finally:
        runner.close()


def test_bytes_that_sniff_as_an_image_and_will_not_decode_are_reported(
    tmp_path, monkeypatch, _vision
):
    """`decode_failed` is a real state -- a truncated PNG, or no Pillow at all --
    and used to be an unexplained silence."""
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = _runner(tmp_path, hub)
    try:
        store = runner.store
        frame_id = store.new_frame(kind="turn", project_id="p")
        truncated = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        path = tmp_path / "truncated.png"
        path.write_bytes(truncated)
        recorded = store.record_cell_artifact(
            path=str(path),
            filename="truncated.png",
            content_type="image/png",
            size_bytes=len(truncated),
            checksum=hashlib.sha256(truncated).hexdigest(),
            producing_cell_id=None,
            frame_id=frame_id,
            root_frame_id=frame_id,
            project_id="p",
        )
        pinned = _pin_through_the_route(cfg, runner, frame_id, recorded["artifact_id"])

        parts = _content(runner, hub, [store.get_annotation(pinned["annotation_id"])])

        assert _images(parts) == []
        assert _problem_reasons(hub) == {"decode_failed"}
        assert "decode_failed" in _model_note(parts)
    finally:
        runner.close()


def test_an_enormous_source_file_is_refused_before_it_is_read(
    tmp_path, monkeypatch, _vision
):
    """The other budgets bound what leaves the process; this one bounds what
    enters it, because the pinned bytes must be read whole to be hashed."""
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = _runner(tmp_path, hub)
    try:
        store = runner.store
        frame_id = store.new_frame(kind="turn", project_id="p")
        data = _png(16, 16, (3, 3, 3))
        first = _record(store, tmp_path / "big.png", data, frame_id=frame_id)
        pinned = _pin_through_the_route(cfg, runner, frame_id, first["artifact_id"])

        # A fixed cap far below any real figure, rather than writing 64 MiB to
        # a temp dir. Deriving it from the constant would make the test agree
        # with whatever the constant says, including "no cap at all".
        monkeypatch.setattr(gateway_mod, "MAX_SOURCE_IMAGE_BYTES", 8)
        assert len(data) > 8
        # Unreadable on purpose: stat still answers, so a guard that measures
        # first gets `too_large`, while one that opens first gets `not_found`.
        # That is what makes this about the order and not just the reason.
        (tmp_path / "big.png").chmod(0o000)

        parts = _content(runner, hub, [store.get_annotation(pinned["annotation_id"])])

        assert _images(parts) == []
        assert _problem_reasons(hub) == {"too_large"}
        assert "too_large" in _model_note(parts)
        # The numbers travel with the refusal: the UI card formats them, and it
        # would otherwise render "0 B, over the 0 B limit" for this reason.
        reported = [e for e in hub.events if e.get("type") == "attachment_problems"][
            -1
        ]["problems"][0]
        assert (reported["bytes"], reported["limit"]) == (len(data), 8)
    finally:
        runner.close()


# ---------------------------------------------------------------------------
# (3) vision is the exact provider+endpoint+model triple
# ---------------------------------------------------------------------------


def test_vision_is_resolved_for_the_model_not_only_the_provider(tmp_path, monkeypatch):
    """A vision-capable provider serving a text-only model.

    The provider-level answer is True, so images were assembled and `chat`'s own
    `_guard_vision` -- which does resolve the triple -- refused the request and
    the turn was lost. The pre-flight has to ask the same question the guard
    asks.
    """
    from openai4s import llm

    llm.register_provider(
        "vision_probe",
        wire="openai",
        base_url="https://example.invalid/v1",
        model="probe-default",
        vision=True,
        replace=True,
    )
    try:
        llm.set_capability_override("vision_probe", model="probe-blind", vision=False)
        blind = LLMConfig(provider="vision_probe", model="probe-blind", api_key="k")
        seeing = LLMConfig(provider="vision_probe", model="probe-default", api_key="k")

        assert llm.supports_vision("vision_probe") is True
        assert llm.supports_vision_for(seeing) is True
        assert llm.supports_vision_for(blind) is False

        hub = _Hub()
        runner = _runner(tmp_path, hub)
        try:
            store = runner.store
            frame_id = store.new_frame(kind="turn", project_id="p")
            first = _record(
                store, tmp_path / "f.png", _png(16, 16, (1, 1, 1)), frame_id=frame_id
            )
            annotation = store.add_annotation(
                root_frame_id=frame_id,
                artifact_id=first["artifact_id"],
                artifact_name="f.png",
                rel_x=0.5,
                rel_y=0.5,
                body="pin",
                version_id=first["version_id"],
                checksum=first["checksum"],
            )
            monkeypatch.setattr(type(runner), "_llm_cfg", lambda self, st=None: blind)
            assert _content(runner, hub, [annotation]) == "what about this?"

            monkeypatch.setattr(type(runner), "_llm_cfg", lambda self, st=None: seeing)
            assert _images(_content(runner, hub, [annotation]))
        finally:
            runner.close()
    finally:
        llm.clear_capability_overrides("vision_probe")
        llm.unregister_provider("vision_probe")


# ---------------------------------------------------------------------------
# the upgraded database, not only the fresh one
# ---------------------------------------------------------------------------


def _schema_sql() -> str:
    import re

    src = Path("openai4s/store.py").read_text()
    return re.search(
        r'_SCHEMA\s*=\s*(?:r?"""|\'\'\')(.*?)(?:"""|\'\'\')', src, re.S
    ).group(1)


def test_an_existing_install_gains_the_binding_columns(tmp_path):
    """The fresh-vs-upgraded blind spot.

    A fresh database gets the columns from CREATE TABLE, so a forgotten
    SCHEMA_VERSION bump would leave migration 12 unreachable and every upgraded
    install inserting into an `annotations` table that lacks the columns the
    INSERT names -- i.e. no pin could be saved at all.
    """
    db = tmp_path / "v11.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_schema_sql())
    columns = [
        row[1]
        for row in conn.execute("PRAGMA table_info(annotations)")
        if row[1] not in ("version_id", "checksum")
    ]
    conn.execute(f"CREATE TABLE _an AS SELECT {','.join(columns)} FROM annotations")
    conn.execute("DROP TABLE annotations")
    conn.execute("ALTER TABLE _an RENAME TO annotations")
    conn.execute("PRAGMA user_version = 11")
    conn.commit()
    conn.close()

    with sqlite3.connect(str(db)) as probe:
        assert "version_id" not in {
            row[1] for row in probe.execute("PRAGMA table_info(annotations)")
        }

    store = Store(db)
    try:
        after = {
            row[1] for row in store._conn.execute("PRAGMA table_info(annotations)")
        }
        assert {"version_id", "checksum"} <= after, "migration 12 did not run"
        frame_id = store.new_frame(kind="turn", project_id="p")
        saved = store.add_annotation(
            root_frame_id=frame_id,
            artifact_id="a-1",
            artifact_name="f.png",
            rel_x=0.1,
            rel_y=0.2,
            body="still savable",
            version_id="v-1",
            checksum="deadbeef",
        )
        assert (saved["version_id"], saved["checksum"]) == ("v-1", "deadbeef")
    finally:
        store.close()


def test_a_v13_install_gains_the_reservation_column(tmp_path):
    """The fresh-vs-upgraded blind spot again, and the same shape of bug.

    `reservation_id` was added only to the ad-hoc add-column pass, not as a
    numbered migration, and `SCHEMA_VERSION` was left at 13. A *fresh*
    database gets the column from `CREATE TABLE`, so every test passed -- while
    an existing v13 install, which is every install that already exists, would
    reach an `UPDATE annotations SET ... reservation_id=?` naming a column its
    table does not have. Admission would fail on exactly the databases that
    have data in them.

    Driven by building a real v13 database and opening a Store on it, which is
    the upgrade path a user actually takes.
    """
    db = tmp_path / "v13.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_schema_sql())
    columns = [
        row[1]
        for row in conn.execute("PRAGMA table_info(annotations)")
        if row[1] != "reservation_id"
    ]
    conn.execute(f"CREATE TABLE _an AS SELECT {','.join(columns)} FROM annotations")
    conn.execute("DROP TABLE annotations")
    conn.execute("ALTER TABLE _an RENAME TO annotations")
    conn.execute("PRAGMA user_version = 13")
    conn.commit()
    conn.close()

    with sqlite3.connect(str(db)) as probe:
        assert "reservation_id" not in {
            row[1] for row in probe.execute("PRAGMA table_info(annotations)")
        }

    store = Store(db)
    try:
        after = {
            row[1] for row in store._conn.execute("PRAGMA table_info(annotations)")
        }
        assert "reservation_id" in after
        # The version moved, and the migration is in the auditable record.
        assert store._conn.execute("PRAGMA user_version").fetchone()[0] >= 14
        names = {
            row["name"]
            for row in store._conn.execute("SELECT name FROM schema_migrations")
        }
        assert "annotation_reservation" in names, names

        # ...and admission actually works on the upgraded database, which is
        # the thing the column exists for.
        root = store.new_frame(kind="turn", project_id="p")
        annotation = store.add_annotation(
            root_frame_id=root,
            artifact_id="a-1",
            artifact_name="plot.png",
            rel_x=0.5,
            rel_y=0.5,
            body="pin",
        )
        claimed = store.reserve_annotations(
            root_frame_id=root,
            annotation_ids=[annotation["annotation_id"]],
            reservation_id="resv-upgraded",
        )
        assert [row["annotation_id"] for row in claimed] == [
            annotation["annotation_id"]
        ]
        assert store.finalize_annotations_sent("resv-upgraded") == 1
    finally:
        store.close()


def test_a_fresh_database_declares_the_reservation_column_canonically(tmp_path):
    """A column that exists only via the add-column pass is a column the
    canonical schema does not describe -- so the two definitions of "an
    annotations table" disagree, and only the upgrade path is exercised."""
    store = Store(tmp_path / "fresh.db")
    try:
        assert "reservation_id" in {
            row[1] for row in store._conn.execute("PRAGMA table_info(annotations)")
        }
    finally:
        store.close()
    assert "reservation_id" in _schema_sql()
