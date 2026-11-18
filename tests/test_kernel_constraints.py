"""The model is told what a Cell cannot do here, instead of finding out.

A model that needed a missing R package spent a session installing into a
read-only environment, creating library directories under $HOME, reading
~/.condarc and scanning local ports for a proxy. The sandbox refused every
attempt; nothing the model could read had said it would.
"""

from __future__ import annotations

from pathlib import Path

from openai4s.kernel.constraints import expected_posture, kernel_constraints_note

_SANDBOXED = {"mode": "auto", "backend": "macOS Seatbelt", "raw_network": False}


def test_a_sandboxed_kernel_is_described_by_its_real_limits():
    note = kernel_constraints_note(posture=_SANDBOXED)
    assert note.startswith("Kernel environment")
    assert "macOS Seatbelt) with direct network blocked" in note
    assert "install.packages()" in note and "pip install" in note
    assert "do not retry them or look for a proxy" in note
    assert "web_fetch" in note and "web_download" in note
    assert "do not install into an existing environment" in note
    assert "env_setup / host.env.create" in note
    assert "only the session workspace" in note
    assert "Do not probe the machine" in note


def test_without_a_sandbox_the_note_does_not_claim_one():
    note = kernel_constraints_note(
        posture={"mode": "off", "backend": None, "raw_network": False}
    )
    assert "direct network blocked" not in note
    assert "without an OS sandbox" in note
    raw = kernel_constraints_note(
        posture={"mode": "auto", "backend": "bubblewrap", "raw_network": True}
    )
    assert "OPENAI4S_KERNEL_ALLOW_RAW_NETWORK is set" in raw


def test_the_posture_reads_configuration_without_running_anything(monkeypatch):
    seen: list[str] = []

    def which(name):
        seen.append(name)
        return f"/usr/bin/{name}"

    monkeypatch.delenv("OPENAI4S_KERNEL_SANDBOX", raising=False)
    monkeypatch.delenv("OPENAI4S_KERNEL_ALLOW_RAW_NETWORK", raising=False)
    assert expected_posture(platform="linux", which=which) == {
        "mode": "auto",
        "backend": "bubblewrap",
        "raw_network": False,
    }
    assert expected_posture(platform="darwin", which=which)["backend"] == (
        "macOS Seatbelt"
    )
    assert expected_posture(platform="darwin", which=lambda _n: None)["backend"] is None
    monkeypatch.setenv("OPENAI4S_KERNEL_SANDBOX", "off")
    assert expected_posture(platform="linux", which=which)["backend"] is None
    assert seen == ["bwrap", "sandbox-exec"]


def test_interpreters_are_named_with_home_written_as_tilde(monkeypatch):
    import openai4s.kernel.constraints as constraints
    import openai4s.kernel.r_kernel as r_kernel

    home = str(Path.home())
    monkeypatch.setattr(
        r_kernel,
        "resolve_r_interpreter",
        lambda _env=None: f"{home}/miniconda3/envs/r-mini/bin/Rscript",
    )
    monkeypatch.setattr(
        constraints.shutil, "which", lambda name: "/opt/homebrew/bin/Rscript"
    )
    note = kernel_constraints_note(posture=_SANDBOXED)
    assert "```r cells run on ~/miniconda3/envs/r-mini/bin/Rscript" in note
    assert "a different R is on PATH at /opt/homebrew/bin/Rscript" in note
    assert home not in note
    assert "```python cells run on" in note


def _observation(monkeypatch, posture, **result):
    import openai4s.kernel.constraints as constraints
    from openai4s.agent.runtime import format_observation

    monkeypatch.setattr(constraints, "expected_posture", lambda **_k: posture)
    return format_observation(result)


def test_a_cell_install_the_sandbox_refused_says_so(monkeypatch):
    pip = _observation(
        monkeypatch,
        _SANDBOXED,
        stdout="Collecting lifelines\n",
        stderr=(
            "WARNING: Retrying ... Failed to establish a new connection: "
            "[Errno 1] Operation not permitted\nERROR: Could not install "
            "packages due to an OSError"
        ),
    )
    assert "[system] Installing packages from a Cell is refused here" in pip
    assert "env_setup / host.env.create" in pip

    r = _observation(
        monkeypatch,
        _SANDBOXED,
        stderr=(
            'Warning in install.packages("survival") :\n  \'lib = '
            '"/opt/conda/envs/r-mini/lib/R/library"\' is not writable'
        ),
    )
    assert "Installing packages from a Cell is refused here" in r


def test_the_install_hint_stays_out_of_ordinary_output(monkeypatch):
    unsandboxed = {"mode": "off", "backend": None, "raw_network": False}
    refused = {
        "stderr": "ERROR: Could not install packages due to an OSError: "
        "[Errno 1] Operation not permitted"
    }
    assert "refused here" not in _observation(monkeypatch, unsandboxed, **refused)
    raw = {"mode": "auto", "backend": "bubblewrap", "raw_network": True}
    assert "refused here" not in _observation(monkeypatch, raw, **refused)
    # Mentioning pip is not an install the sandbox refused.
    plain = _observation(
        monkeypatch, _SANDBOXED, stdout="run `pip install lifelines` to reproduce"
    )
    assert "refused here" not in plain


def test_the_web_session_prompt_carries_the_note(tmp_path):
    from openai4s.config import Config, LLMConfig
    from openai4s.server.gateway import SessionRunner, SessionState

    class _Hub:
        def broadcast(self, *_args):
            return None

        def emitter(self, _root):
            return lambda _event: None

    runner = SessionRunner(
        Config(data_dir=tmp_path, llm=LLMConfig(provider="deepseek", api_key="test")),
        _Hub(),
        start_idle_sweeper=False,
    )
    frame_id = runner.store.new_frame(kind="turn", project_id="science", status="ready")
    state = SessionState(frame_id, "science", runner.workspace_for(frame_id))
    runner._seed_messages(state)
    system = state.messages[0]["content"]
    assert (
        "Kernel environment (the limits Cells and host.bash run under here)" in system
    )
    # The prompt no longer promises an open network to host.bash or raw Python.
    assert "networking is on" not in system
    assert "curl/wget/git/pip all work" not in system
    runner.store.close()
