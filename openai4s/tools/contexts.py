"""Narrow runtime ports available to concrete control tools.

Tools depend on these structural protocols instead of the large
``HostDispatcher``. The dispatcher remains the policy envelope and supplies
objects that implement the relevant port only after a call has been approved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol


class WorkspaceToolContext(Protocol):
    """Workspace path boundary used by file and search tools."""

    def workspace(self) -> Path: ...

    def relative(self, path: Path) -> str | None: ...

    def resolve(self, relative: str, *, must_exist: bool = False) -> Path: ...

    def is_secret_path(self, path: str) -> bool: ...

    def resolved_secret_checker(self) -> Callable[[Path], bool]: ...

    def verified_read_opener(self) -> Callable[[str | Path], Any]: ...

    def open_verified_read(self, relative: str | Path) -> Any: ...

    def open_verified_directory(self, relative: str | Path) -> Any: ...

    def secure_parent(
        self, relative: str | Path, *, create_parents: bool = False
    ) -> Any: ...


class EnvironmentToolContext(Protocol):
    """Mutable session hooks required by environment control tools."""

    active_env_bin: str | None
    active_r_env: str | None
    on_env_switch: Callable[[str], None] | None


class ScienceArtifactToolContext(Protocol):
    """Single-purpose Stage 10 result writer; no Store capability leaks in."""

    def record_science_artifact(self, result: dict[str, Any]) -> dict[str, Any]: ...


class ControlToolContext:
    """Live adapter supplied to every tool by ``HostDispatcher``.

    It implements both narrow protocols without exposing the dispatcher,
    database, permission broker, or gateway internals. Type annotations keep a
    tool coupled to the smallest relevant port; this is an API boundary for
    trusted tool code, not a security sandbox between built-ins.
    """

    def __init__(
        self,
        workspace: WorkspaceToolContext,
        *,
        get_active_env_bin: Callable[[], str | None],
        get_active_r_env: Callable[[], str | None],
        set_active_r_env: Callable[[str | None], None],
        get_on_env_switch: Callable[[], Callable[[str], None] | None],
        get_stage10_enabled: Callable[[], bool] | None = None,
        invoke_control: Callable[..., Any] | None = None,
        search_web: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self._workspace = workspace
        self._get_active_env_bin = get_active_env_bin
        self._get_active_r_env = get_active_r_env
        self._set_active_r_env = set_active_r_env
        self._get_on_env_switch = get_on_env_switch
        self._get_stage10_enabled = get_stage10_enabled
        self._invoke_control = invoke_control
        self._search_web = search_web

    def workspace(self) -> Path:
        return self._workspace.workspace()

    def relative(self, path: Path) -> str | None:
        return self._workspace.relative(path)

    def resolve(self, relative: str, *, must_exist: bool = False) -> Path:
        return self._workspace.resolve(relative, must_exist=must_exist)

    def is_secret_path(self, path: str) -> bool:
        return self._workspace.is_secret_path(path)

    def resolved_secret_checker(self) -> Callable[[Path], bool]:
        return self._workspace.resolved_secret_checker()

    def verified_read_opener(self) -> Callable[[str | Path], Any]:
        return self._workspace.verified_read_opener()

    def open_verified_read(self, relative: str | Path) -> Any:
        return self._workspace.open_verified_read(relative)

    def open_verified_directory(self, relative: str | Path) -> Any:
        return self._workspace.open_verified_directory(relative)

    def secure_parent(
        self, relative: str | Path, *, create_parents: bool = False
    ) -> Any:
        return self._workspace.secure_parent(relative, create_parents=create_parents)

    @property
    def active_env_bin(self) -> str | None:
        return self._get_active_env_bin()

    @property
    def active_r_env(self) -> str | None:
        return self._get_active_r_env()

    @active_r_env.setter
    def active_r_env(self, value: str | None) -> None:
        self._set_active_r_env(value)

    @property
    def on_env_switch(self) -> Callable[[str], None] | None:
        return self._get_on_env_switch()

    def invoke(self, method: str, *arguments: Any) -> Any:
        """Call one focused Host service from inside the policy envelope.

        Concrete orchestration tools own their schema and argument
        normalization, while the supplied callback reaches the existing
        focused Host service.  A context without that port fails closed.
        """

        if self._invoke_control is None:
            raise RuntimeError(f"control behavior is unavailable: {method}")
        return self._invoke_control(method, *arguments)

    def search_web(
        self,
        query: Any,
        *,
        num_results: int = 8,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        """Use the Host-bound primary search implementation.

        This callback is deliberately separate from ``invoke``.  It has no
        ``_m_*`` dispatcher handler and therefore cannot be addressed on the
        kernel Host wire to bypass the existing ``web_search`` permission,
        audit, and untrusted-output envelope.
        """

        if self._search_web is None:
            raise RuntimeError("primary web search is unavailable")
        return self._search_web(
            query,
            num_results=num_results,
            timeout=timeout,
        )

    def record_science_artifact(self, result: dict[str, Any]) -> dict[str, Any]:
        """Write a search result for the Gateway's trusted native capture."""

        from openai4s.host.stage10_science import write_search_artifact

        return write_search_artifact(self.workspace(), result)

    def stage10_enabled(self) -> bool:
        """Expose only the feature decision, never the full daemon config."""

        return bool(self._get_stage10_enabled and self._get_stage10_enabled())


__all__ = [
    "WorkspaceToolContext",
    "EnvironmentToolContext",
    "ScienceArtifactToolContext",
    "ControlToolContext",
]
