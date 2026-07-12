"""Optional Jupyter wire adapter around the existing OpenAI4S workers.

``ipykernel``/ZeroMQ are imported only by :func:`main` (or when explicitly
requesting a bridge class).  Scientific execution still travels through
``openai4s.kernel.manager.Kernel`` and its hardened JSON-line protocol.

This is intentionally a *standalone* namespace.  It does not attach to a Web
session, does not expose Host RPC, and does not claim Gateway artifact capture.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Callable

from openai4s import __version__

RuntimeFactory = Callable[[str, str], Any]


class JupyterBridgeUnavailable(RuntimeError):
    """The optional Jupyter wire dependency is absent or unusable."""


def _load_ipykernel() -> tuple[type, type]:
    try:
        from ipykernel.kernelapp import IPKernelApp
        from ipykernel.kernelbase import Kernel as JupyterKernel
    except (ImportError, ModuleNotFoundError) as exc:
        raise JupyterBridgeUnavailable(
            "Jupyter wire support is optional; install it with "
            "`python -m pip install 'ipykernel>=7,<8'`"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - a partial optional install
        raise JupyterBridgeUnavailable(
            "the optional Jupyter wire stack was found but could not be loaded "
            f"({type(exc).__name__}: {exc})"
        ) from exc
    return JupyterKernel, IPKernelApp


def _spawn_runtime(language: str, cwd: str) -> Any:
    if language == "python":
        from openai4s.kernel.manager import Kernel

        return Kernel(dispatcher=None, cwd=cwd, mode="jupyter")
    if language == "r":
        from openai4s.kernel.r_kernel import spawn_r_kernel

        return spawn_r_kernel(cwd=cwd)
    raise ValueError("language must be python or r")


def _language_info(language: str) -> dict[str, Any]:
    if language == "python":
        return {
            "name": "python",
            "version": f"{sys.version_info.major}.{sys.version_info.minor}",
            "mimetype": "text/x-python",
            "codemirror_mode": {"name": "python", "version": 3},
            "pygments_lexer": "python3",
            "file_extension": ".py",
        }
    return {
        "name": "R",
        "version": "unknown",
        "mimetype": "text/x-r-source",
        "codemirror_mode": "r",
        "pygments_lexer": "r",
        "file_extension": ".r",
    }


def _error_fields(error: Any, *, fallback: str = "OpenAI4SKernelError") -> dict:
    text = str(error or fallback)
    lines = text.splitlines() or [text]
    last = next((line.strip() for line in reversed(lines) if line.strip()), fallback)
    if ":" in last:
        name, value = last.split(":", 1)
        name = name.strip() or fallback
        value = value.strip() or last
    else:
        name, value = fallback, last
    return {"ename": name, "evalue": value, "traceback": lines}


def create_kernel_class(
    language: str,
    *,
    kernel_base: type | None = None,
    runtime_factory: RuntimeFactory | None = None,
) -> type:
    """Create the optional ``ipykernel.kernelbase.Kernel`` implementation.

    Dependency injection keeps the bridge's offline contracts testable without
    installing Jupyter.  Production callers omit both optional arguments.
    """

    language = str(language or "").strip().lower()
    if language not in {"python", "r"}:
        raise ValueError("language must be python or r")
    if kernel_base is None:
        kernel_base, _app = _load_ipykernel()
    factory = runtime_factory or _spawn_runtime
    info = _language_info(language)

    class OpenAI4SJupyterKernel(kernel_base):
        implementation = f"openai4s-{language}"
        implementation_version = __version__
        language_info = info
        banner = (
            f"OpenAI4S {language} standalone Jupyter bridge. "
            "Host RPC and Web-session sharing are unavailable."
        )
        help_links: list[dict[str, str]] = []

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._openai4s_runtime = factory(language, os.getcwd())
            self._openai4s_shutdown = False

        def _stream(self, name: str, text: Any) -> None:
            value = str(text or "")
            if value:
                self.send_response(
                    self.iopub_socket,
                    "stream",
                    {"name": name, "text": value},
                )

        def do_execute(
            self,
            code: str,
            silent: bool,
            store_history: bool = True,
            user_expressions: dict | None = None,
            allow_stdin: bool = False,
            **_kwargs: Any,
        ) -> dict:
            del store_history, user_expressions, allow_stdin
            streamed: list[str] = []

            def on_chunk(text: str) -> None:
                streamed.append(text)
                if not silent:
                    self._stream("stdout", text)

            try:
                result = self._openai4s_runtime.execute(
                    str(code or ""),
                    origin="user",
                    on_chunk=on_chunk,
                )
            except Exception as exc:  # noqa: BLE001 - convert to wire error
                fields = _error_fields(
                    f"{type(exc).__name__}: {exc}",
                    fallback=type(exc).__name__,
                )
                if not silent:
                    self.send_response(self.iopub_socket, "error", fields)
                return {
                    "status": "error",
                    "execution_count": self.execution_count,
                    **fields,
                }

            stdout = str(result.get("stdout") or "")
            streamed_text = "".join(streamed)
            if not silent and stdout:
                if streamed_text and stdout.startswith(streamed_text):
                    self._stream("stdout", stdout[len(streamed_text) :])
                elif not streamed_text:
                    self._stream("stdout", stdout)
                elif stdout != streamed_text:
                    # A non-conforming worker should not make final output
                    # disappear; at worst the frontend sees a duplicate prefix.
                    self._stream("stdout", stdout)
            if not silent:
                self._stream("stderr", result.get("stderr"))

            error = result.get("error")
            if result.get("interrupted") and not error:
                error = "KeyboardInterrupt"
            if error:
                if result.get("interrupted"):
                    fields = {
                        "ename": "KeyboardInterrupt",
                        "evalue": "",
                        "traceback": ["KeyboardInterrupt"],
                    }
                else:
                    fields = _error_fields(error)
                if not silent:
                    self.send_response(self.iopub_socket, "error", fields)
                return {
                    "status": "error",
                    "execution_count": self.execution_count,
                    **fields,
                }
            return {
                "status": "ok",
                "execution_count": self.execution_count,
                "payload": [],
                # The hardened worker protocol has no arbitrary expression
                # evaluation side channel; callers should execute a new Cell.
                "user_expressions": {},
            }

        async def interrupt_request(self, stream: Any, ident: Any, parent: Any) -> Any:
            # ``interrupt_mode=message`` means this handler owns delivery.
            # KernelBase.interrupt_request() sends SIGINT to the bridge (or its
            # whole process group); calling it after interrupting our exact
            # child would double-deliver the signal and can kill the bridge.
            try:
                self._openai4s_runtime.interrupt()
            except Exception as exc:  # noqa: BLE001 - encode a wire error
                content = {
                    "status": "error",
                    **_error_fields(
                        f"{type(exc).__name__}: {exc}",
                        fallback=type(exc).__name__,
                    ),
                }
            else:
                content = {"status": "ok"}
            session = getattr(self, "session", None)
            if session is not None:
                session.send(
                    stream,
                    "interrupt_reply",
                    content,
                    parent,
                    ident=ident,
                )
            return content

        def do_shutdown(self, restart: bool) -> dict:
            if not self._openai4s_shutdown:
                try:
                    self._openai4s_runtime.shutdown()
                except Exception:  # noqa: BLE001 - Jupyter still owns teardown
                    pass
                finally:
                    self._openai4s_shutdown = True
            return {"status": "ok", "restart": bool(restart)}

    OpenAI4SJupyterKernel.__name__ = (
        "OpenAI4SPythonJupyterKernel"
        if language == "python"
        else "OpenAI4SRJupyterKernel"
    )
    OpenAI4SJupyterKernel.__qualname__ = OpenAI4SJupyterKernel.__name__
    return OpenAI4SJupyterKernel


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m openai4s.adapters.jupyter.bridge",
        description="OpenAI4S optional Jupyter wire bridge",
        add_help=True,
    )
    parser.add_argument("--language", choices=("python", "r"), required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, wire_args = parser.parse_known_args(argv)
    try:
        kernel_base, app = _load_ipykernel()
    except JupyterBridgeUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    kernel_class = create_kernel_class(args.language, kernel_base=kernel_base)
    app.launch_instance(argv=wire_args, kernel_class=kernel_class)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "JupyterBridgeUnavailable",
    "build_parser",
    "create_kernel_class",
    "main",
]
