"""Public contract every BYOC provider implements.

A provider shim (``skills/remote-compute-<id>/provider.py``) exports
``PROVIDER = <impl of ByocProvider>``; the resident drives it through exactly
this surface. Kept separate from the resident so a shim can import the contract
without pulling in the hardening/lifecycle machinery.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable, NoReturn, Protocol


class ExecResult(Protocol):
    """A running command inside a sandbox: two byte streams plus a blocking
    ``wait()`` returning the exit code."""

    stdout: Iterable[bytes]
    stderr: Iterable[bytes]

    def wait(self) -> int: ...


class ByocProvider(Protocol):
    """The full provider surface. ``list_dir`` / ``list_volumes`` /
    ``read_file`` are optional — a provider without a browsable persistent
    store simply omits them and the matching op surfaces ``invalid_request``.
    """

    secret_env_prefixes: tuple[str, ...]
    token_scrub_regex: re.Pattern[str]

    def import_and_patch(self) -> None: ...

    def apply_auth(self, creds: dict[str, str]) -> None: ...

    def install_unauth_hook(self, on_expired: Callable[[], NoReturn]) -> None: ...

    def create_sandbox(
        self, spec: dict[str, Any], install_id: str, tags: dict[str, str] | None = None
    ) -> str:
        """Provision a sandbox. ``tags`` are host-built identity tags
        (openai4s-session/openai4s-job/…, already sanitized to the
        provider's tag constraints); implementations that support tagging
        MUST apply them at create time and merge the
        ``openai4s-install-id=install_id`` owner tag LAST so an incoming
        entry can never override ownership."""
        ...

    def exec(
        self,
        sandbox_id: str,
        argv: list[str],
        *,
        stdin: Iterable[bytes] | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> ExecResult: ...

    def list_owned(self, install_id: str) -> list[dict[str, Any]]: ...

    def read_owner(self, sandbox_id: str) -> str | None: ...

    def terminate(self, sandbox_id: str) -> None: ...

    def list_dir(
        self, root: str, path: str, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """List ``path`` inside the provider's persistent store named ``root``.
        Returns ``[{name, type: "file"|"dir", size, mtime}, ...]``. ``limit``
        caps the entry count so very wide directories don't serialize the full
        iterator through the helper. Providers without a browsable store omit
        this method; ``_op_list_dir`` surfaces that as ``invalid_request``."""
        ...

    def list_volumes(self) -> list[dict[str, Any]]:
        """List the provider's persistent stores. Returns
        ``[{name, created_at}, ...]``. Backs the file browser's landing view
        at ``/``. Optional for the same reason as ``list_dir``."""
        ...

    def read_file(self, root: str, path: str) -> Iterable[bytes]:
        """Stream ``path`` from the provider's persistent store named ``root``
        as a bytes iterator. Backs the file browser's Import and Download
        actions for byoc. Optional for the same reason as ``list_dir``."""
        ...


class ByocError(Exception):
    """A structured provider failure. ``kind`` is one of
    ``BASE_ERROR_KINDS``; anything else is coerced to ``transient`` before it
    reaches the host."""

    def __init__(self, kind: str, msg: str = ""):
        self.kind = kind
        self.msg = msg
