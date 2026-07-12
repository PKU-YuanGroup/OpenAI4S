"""Offline contracts for the optional Jupyter KernelSpec/wire adapter."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from openai4s.adapters.jupyter import bridge
from openai4s.adapters.jupyter.kernelspec import (
    KERNEL_NAMES,
    KernelSpecError,
    adapter_status,
    default_user_kernels_dir,
    install_kernelspecs,
    kernel_spec,
    write_kernelspecs,
)
from openai4s.cli import main as cli_main


def test_stdlib_kernelspec_import_does_not_load_optional_jupyter_stack():
    script = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.', 1)[0] in {'ipykernel', 'jupyter_client', 'zmq'}:
        raise AssertionError('optional Jupyter stack imported')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from openai4s.adapters.jupyter.kernelspec import adapter_status
from openai4s.adapters.jupyter import bridge
status = adapter_status()
assert status['execution_scope'] == 'standalone'
assert bridge.build_parser().prog.endswith('openai4s.adapters.jupyter.bridge')
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    status = adapter_status()
    assert status["execution_scope"] == "standalone"
    assert status["host_rpc"] is False
    assert status["internal_protocol"] == "hardened-jsonl"
    assert status["dependency"] == "ipykernel>=7,<8"
    assert "extra" not in status


@pytest.mark.parametrize("language", ["python", "r"])
def test_kernel_spec_is_a_real_namespaced_jupyter_manifest(language):
    spec = kernel_spec(language, python_executable="/opt/openai4s/python")
    assert spec["argv"] == [
        "/opt/openai4s/python",
        "-m",
        "openai4s.adapters.jupyter.bridge",
        "--language",
        language,
        "-f",
        "{connection_file}",
    ]
    assert spec["language"] == ("python" if language == "python" else "R")
    assert spec["interrupt_mode"] == "message"
    # The optional ipykernel installation advertises its own actual wire
    # version. Hard-coding a different version in the spec would make older
    # supported 7.x installations lie during discovery.
    assert "kernel_protocol_version" not in spec
    assert spec["metadata"] == {
        "openai4s": {
            "adapter_version": 1,
            "execution_scope": "standalone",
            "host_rpc": False,
            "internal_protocol": "hardened-jsonl",
            "language": language,
        }
    }
    assert KERNEL_NAMES[language].replace("-", "").isalnum()


def test_kernel_spec_preserves_the_installing_environment_executable():
    spec = kernel_spec("python", python_executable=sys.executable)
    assert spec["argv"][0] == str(Path(sys.executable).absolute())


def test_export_is_deterministic_and_replace_never_deletes_extra_files(tmp_path):
    written = write_kernelspecs(
        tmp_path,
        python_executable="/opt/openai4s/python",
    )
    assert [item["name"] for item in written] == [
        "openai4s-python",
        "openai4s-r",
    ]
    python_json = tmp_path / "openai4s-python" / "kernel.json"
    before = python_json.read_bytes()
    assert json.loads(before)["argv"][0] == "/opt/openai4s/python"
    extra = python_json.parent / "keep.txt"
    extra.write_text("keep", encoding="utf-8")

    with pytest.raises(KernelSpecError, match="already exists"):
        write_kernelspecs(tmp_path)

    replaced = write_kernelspecs(
        tmp_path,
        languages="python",
        replace=True,
        python_executable="/opt/openai4s/python",
    )
    assert len(replaced) == 1
    assert python_json.read_bytes() == before
    assert extra.read_text(encoding="utf-8") == "keep"


def test_prefix_install_and_platform_user_paths_are_stdlib_only(tmp_path):
    installed = install_kernelspecs(
        prefix=tmp_path,
        languages="r",
        python_executable="/venv/python",
    )
    expected = tmp_path / "share" / "jupyter" / "kernels" / "openai4s-r"
    assert installed[0]["path"] == str(expected)
    assert (expected / "kernel.json").is_file()
    assert (
        default_user_kernels_dir(
            home=tmp_path,
            platform="darwin",
            environ={},
        )
        == tmp_path / "Library" / "Jupyter" / "kernels"
    )
    assert (
        default_user_kernels_dir(
            home=tmp_path,
            platform="linux",
            environ={"XDG_DATA_HOME": str(tmp_path / "xdg")},
        )
        == tmp_path / "xdg" / "jupyter" / "kernels"
    )
    assert (
        default_user_kernels_dir(
            home=tmp_path,
            platform="win32",
            environ={"APPDATA": str(tmp_path / "appdata")},
        )
        == tmp_path / "appdata" / "jupyter" / "kernels"
    )


class _FakeBase:
    execution_count = 4

    def __init__(self, *args, **kwargs):
        del args, kwargs
        self.iopub_socket = object()
        self.responses = []
        self.base_interrupts = 0

    def send_response(self, socket, message_type, content):
        assert socket is self.iopub_socket
        self.responses.append((message_type, content))

    async def interrupt_request(self, stream, ident, parent):
        self.base_interrupts += 1
        return {"stream": stream, "ident": ident, "parent": parent}


class _FakeSession:
    def __init__(self):
        self.messages = []

    def send(self, stream, message_type, content, parent, ident=None):
        self.messages.append((stream, message_type, content, parent, ident))


class _Runtime:
    def __init__(self):
        self.calls = []
        self.interrupts = 0
        self.shutdowns = 0
        self.result = {
            "stdout": "live\ntail\n",
            "stderr": "warning\n",
            "error": None,
        }

    def execute(self, code, origin, on_chunk):
        self.calls.append((code, origin))
        on_chunk("live\n")
        return dict(self.result)

    def interrupt(self):
        self.interrupts += 1

    def shutdown(self):
        self.shutdowns += 1


def test_bridge_maps_execute_stream_error_interrupt_and_shutdown(monkeypatch):
    runtime = _Runtime()
    factory_calls = []

    def factory(language, cwd):
        factory_calls.append((language, Path(cwd)))
        return runtime

    kernel_type = bridge.create_kernel_class(
        "python",
        kernel_base=_FakeBase,
        runtime_factory=factory,
    )
    kernel = kernel_type()
    reply = kernel.do_execute("print('ok')", silent=False)
    assert reply == {
        "status": "ok",
        "execution_count": 4,
        "payload": [],
        "user_expressions": {},
    }
    assert runtime.calls == [("print('ok')", "user")]
    assert [item[0] for item in kernel.responses] == ["stream", "stream", "stream"]
    assert [item[1]["text"] for item in kernel.responses] == [
        "live\n",
        "tail\n",
        "warning\n",
    ]
    assert factory_calls[0][0] == "python"

    runtime.result = {
        "stdout": "",
        "stderr": "",
        "error": "ValueError: broken",
    }
    kernel.responses.clear()
    failed = kernel.do_execute("broken()", silent=False)
    assert failed["status"] == "error"
    assert failed["ename"] == "ValueError"
    assert failed["evalue"] == "broken"
    assert kernel.responses[-1][0] == "error"

    session = _FakeSession()
    kernel.session = session
    response = asyncio.run(kernel.interrupt_request("control", "id", {"x": 1}))
    assert runtime.interrupts == 1
    # Calling KernelBase here would signal the bridge/process group after the
    # exact child was already interrupted.
    assert kernel.base_interrupts == 0
    assert response == {"status": "ok"}
    assert session.messages == [
        ("control", "interrupt_reply", {"status": "ok"}, {"x": 1}, "id")
    ]
    assert kernel.do_shutdown(True) == {"status": "ok", "restart": True}
    assert kernel.do_shutdown(True) == {"status": "ok", "restart": True}
    assert runtime.shutdowns == 1


def test_bridge_interrupt_result_uses_standard_keyboard_interrupt_error():
    runtime = _Runtime()
    runtime.result = {
        "stdout": "",
        "stderr": "",
        "error": "Interrupted",
        "interrupted": True,
    }
    kernel_type = bridge.create_kernel_class(
        "python",
        kernel_base=_FakeBase,
        runtime_factory=lambda language, cwd: runtime,
    )
    kernel = kernel_type()
    reply = kernel.do_execute("while True: pass", silent=False)
    assert reply == {
        "status": "error",
        "execution_count": 4,
        "ename": "KeyboardInterrupt",
        "evalue": "",
        "traceback": ["KeyboardInterrupt"],
    }
    assert kernel.responses[-1] == (
        "error",
        {
            "ename": "KeyboardInterrupt",
            "evalue": "",
            "traceback": ["KeyboardInterrupt"],
        },
    )


def test_bridge_silent_execution_and_runtime_exception_are_protocol_safe():
    runtime = _Runtime()
    kernel_type = bridge.create_kernel_class(
        "r",
        kernel_base=_FakeBase,
        runtime_factory=lambda language, cwd: runtime,
    )
    kernel = kernel_type()
    reply = kernel.do_execute("summary(x)", silent=True)
    assert reply["status"] == "ok"
    assert kernel.responses == []

    def explode(code, origin, on_chunk):
        del code, origin, on_chunk
        raise RuntimeError("worker exited")

    runtime.execute = explode
    failed = kernel.do_execute("stop()", silent=False)
    assert failed["status"] == "error"
    assert failed["ename"] == "RuntimeError"
    assert "worker exited" in failed["evalue"]


@pytest.mark.integration
def test_bridge_executes_against_the_real_hardened_python_worker(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    kernel_type = bridge.create_kernel_class("python", kernel_base=_FakeBase)
    kernel = kernel_type()
    try:
        first = kernel.do_execute("answer = 40", silent=False)
        second = kernel.do_execute("print(answer + 2)", silent=False)
        no_host = kernel.do_execute("print('host' in globals())", silent=False)
        files = kernel.do_execute(
            "with open('standalone.txt', 'w') as f:\n"
            "    _ = f.write('ordinary file I/O')\n"
            "with open('standalone.txt') as f:\n"
            "    print(f.read())",
            silent=False,
        )
    finally:
        kernel.do_shutdown(False)

    assert (
        first["status"]
        == second["status"]
        == no_host["status"]
        == files["status"]
        == "ok"
    )
    stdout = "".join(
        content.get("text", "")
        for message_type, content in kernel.responses
        if message_type == "stream" and content.get("name") == "stdout"
    )
    assert "42" in stdout
    assert "False" in stdout
    assert "ordinary file I/O" in stdout


def test_bridge_main_reports_missing_dependency_and_forwards_connection_args(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        bridge,
        "_load_ipykernel",
        lambda: (_ for _ in ()).throw(bridge.JupyterBridgeUnavailable("missing")),
    )
    assert bridge.main(["--language", "python", "-f", "connection.json"]) == 2
    assert "missing" in capsys.readouterr().err

    launched = []

    class App:
        @classmethod
        def launch_instance(cls, **kwargs):
            launched.append(kwargs)

    monkeypatch.setattr(bridge, "_load_ipykernel", lambda: (_FakeBase, App))
    assert bridge.main(["--language", "r", "-f", "connection.json"]) == 0
    assert launched[0]["argv"] == ["-f", "connection.json"]
    assert launched[0]["kernel_class"].implementation == "openai4s-r"


def test_bridge_wraps_a_broken_optional_install(monkeypatch):
    real_import = __import__

    def broken_import(name, *args, **kwargs):
        if name == "ipykernel.kernelapp":
            raise RuntimeError("incompatible optional stack")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", broken_import)
    with pytest.raises(bridge.JupyterBridgeUnavailable, match="could not be loaded"):
        bridge._load_ipykernel()


def test_cli_describe_and_export_work_without_ipykernel(tmp_path, capsys, monkeypatch):
    daemon_state = tmp_path / "daemon-state"
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(daemon_state))
    assert cli_main(["jupyter", "describe", "--json"]) == 0
    described = json.loads(capsys.readouterr().out)
    assert described["adapter"] == "jupyter"
    assert described["execution_scope"] == "standalone"
    assert not daemon_state.exists()

    output = tmp_path / "specs"
    assert cli_main(["jupyter", "export", str(output), "--language", "python"]) == 0
    assert (output / "openai4s-python" / "kernel.json").is_file()
    assert "exported openai4s-python" in capsys.readouterr().out
    assert not daemon_state.exists()
