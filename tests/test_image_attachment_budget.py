"""What one turn may attach as images, and what it says when it cannot.

`_build_annotated_content` attached every pinned figure at full size,
re-encoded as PNG. Eight pins on a 3000x2200 raster sends about 10 MiB to the
provider; eighty sends ten times that. Nothing bounded it and nothing said a
bound existed, because none did.

All three limits are reported rather than applied in silence. Dropping the
ninth figure quietly means a user pins something, asks about it, and gets a
confident answer about a picture the model never received — which is the
failure mode this whole version has been removing.
"""

from __future__ import annotations

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod


class _Hub:
    def __init__(self):
        self.events = []

    def emitter(self, root_frame_id):
        return lambda event: self.events.append(event)

    def broadcast(self, root_frame_id, event):
        self.events.append(event)


def _runner(tmp_path, hub):
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    return gateway_mod.SessionRunner(cfg, hub, start_idle_sweeper=False)


def _annotations(count):
    return [
        {"artifact_id": f"a-{index}", "artifact_name": f"figure{index}.png"}
        for index in range(count)
    ]


def _install_fakes(monkeypatch, runner, *, image_bytes):
    """Make every artifact resolve to a raster of a chosen size."""
    monkeypatch.setattr(gateway_mod, "_is_raster_image", lambda path: True)
    monkeypatch.setattr(
        gateway_mod,
        "_figure_with_pins",
        lambda path, pins: (b"x" * image_bytes, "image/png"),
    )
    monkeypatch.setattr(
        type(runner.store), "resolve_artifact_path", lambda self, art: "/fake.png"
    )
    monkeypatch.setattr(gateway_mod, "_llm_supports_vision_probe", None, raising=False)


def _content(runner, state, annos):
    return runner._build_annotated_content(state, "look at these", annos)


@pytest.fixture
def _vision(monkeypatch):
    from openai4s import llm

    monkeypatch.setattr(llm, "supports_vision", lambda provider: True)


def test_the_number_of_attached_images_is_bounded_and_the_rest_are_named(
    tmp_path, monkeypatch, _vision
):
    hub = _Hub()
    runner = _runner(tmp_path, hub)
    try:
        _install_fakes(monkeypatch, runner, image_bytes=1000)
        state = runner._state(runner.store.new_frame(kind="turn", project_id="p"), "p")
        # A fixed number, deliberately far above any sane limit. Asserting
        # `len(images) == MAX_ATTACHED_IMAGES` reads well and cannot fail:
        # raising the constant raises the expectation with it, so the test
        # agrees with whatever the code does. Mutation testing is what exposed
        # that -- removing the budget entirely broke nothing.
        offered = 40
        assert offered > gateway_mod.MAX_ATTACHED_IMAGES
        parts = _content(runner, state, _annotations(offered))

        images = [p for p in parts if p.get("type") == "image"]
        assert len(images) < offered, "every offered figure was attached"
        assert len(images) <= 16, "the count budget is not doing anything"

        problems = [e for e in hub.events if e.get("type") == "attachment_problems"]
        assert problems, "the dropped figures were not reported to the user"
        assert {p["reason"] for p in problems[-1]["problems"]} == {"too_many"}

        # The model is told too, so it does not describe a picture it never got.
        note = [
            p
            for p in parts
            if p.get("type") == "text" and "were NOT sent" in p.get("text", "")
        ]
        assert note, "the model was not told which figures are missing"
    finally:
        runner.close()


def test_one_oversized_image_is_dropped_without_taking_the_others_with_it(
    tmp_path, monkeypatch, _vision
):
    """A single enormous figure must not cost the user the rest of their pins."""
    hub = _Hub()
    runner = _runner(tmp_path, hub)
    try:
        # A fixed 64 MiB, not `MAX_IMAGE_BYTES + 1`. Deriving the oversized
        # value from the constant means raising the constant also raises what
        # counts as oversized, and the test passes with no budget at all --
        # the same self-reference the count test had.
        oversized = 64 * 1024 * 1024
        assert oversized > gateway_mod.MAX_IMAGE_BYTES
        sizes = iter([oversized, 1000, 1000])
        monkeypatch.setattr(gateway_mod, "_is_raster_image", lambda path: True)
        monkeypatch.setattr(
            gateway_mod,
            "_figure_with_pins",
            lambda path, pins: (b"x" * next(sizes), "image/png"),
        )
        monkeypatch.setattr(
            type(runner.store), "resolve_artifact_path", lambda self, art: "/fake.png"
        )
        state = runner._state(runner.store.new_frame(kind="turn", project_id="p"), "p")
        parts = _content(runner, state, _annotations(3))

        assert len([p for p in parts if p.get("type") == "image"]) == 2
        problems = [e for e in hub.events if e.get("type") == "attachment_problems"][-1]
        assert problems["problems"][0]["reason"] == "too_large"
    finally:
        runner.close()


def test_the_total_budget_stops_before_the_wire_not_after(
    tmp_path, monkeypatch, _vision
):
    """The cap is on what is assembled, so the request is never built oversized
    and then trimmed — the point is not to send it, not to notice afterwards.
    """
    hub = _Hub()
    runner = _runner(tmp_path, hub)
    try:
        # Each one comfortably under the per-item cap, so this isolates the
        # *total* budget. Sizing them at TOTAL//3+1 (an earlier attempt) puts
        # each above MAX_IMAGE_BYTES, so every one is rejected as `too_large`
        # and the total is never reached -- the test would then pass or fail
        # for a reason unrelated to what it claims to check.
        each = 3 * 1024 * 1024
        assert each < gateway_mod.MAX_IMAGE_BYTES, "per-item cap would fire first"
        assert each * 5 > gateway_mod.MAX_TOTAL_IMAGE_BYTES
        _install_fakes(monkeypatch, runner, image_bytes=each)
        state = runner._state(runner.store.new_frame(kind="turn", project_id="p"), "p")
        parts = _content(runner, state, _annotations(5))

        images = [p for p in parts if p.get("type") == "image"]
        assert sum(len(p["data"]) for p in images) <= gateway_mod.MAX_TOTAL_IMAGE_BYTES
        problems = [e for e in hub.events if e.get("type") == "attachment_problems"][-1]
        assert any(p["reason"] == "budget_exhausted" for p in problems["problems"])
    finally:
        runner.close()


def test_a_turn_within_budget_reports_nothing(tmp_path, monkeypatch, _vision):
    """A note that fires when nothing went wrong trains people to ignore it."""
    hub = _Hub()
    runner = _runner(tmp_path, hub)
    try:
        _install_fakes(monkeypatch, runner, image_bytes=1000)
        state = runner._state(runner.store.new_frame(kind="turn", project_id="p"), "p")
        parts = _content(runner, state, _annotations(2))

        assert len([p for p in parts if p.get("type") == "image"]) == 2
        assert not [e for e in hub.events if e.get("type") == "attachment_problems"]
        assert not [p for p in parts if "were NOT sent" in str(p.get("text", ""))]
    finally:
        runner.close()
