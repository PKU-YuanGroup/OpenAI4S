"""Object-level data provenance.

Runs INSIDE the kernel worker. The design goal: observe data flow at the OBJECT
level — attach the source `version_id` of each
artifact to the objects read from it, then when an object is written back out,
collect every input version_id it carries and report the lineage edges to the
host.

Mechanism:
 _PROV_ATTR = "_openai4s_src"..... a frozenset[version_id] attached to objects.
 OPENAI4S_PROVENANCE_OFF=1......... global escape hatch.
 _prov_wrap_reader............... tags a reader's return value with the source
 version resolved from its path argument.
 _prov_wrap_method_writer........ on save, gathers __openai4s_runtime_inputs__
 off the object and reports edges to the host.
 scalar/container propagation.... __getitem__ / slices / json.loads etc. carry
 the tag forward; scalar binops merge tags.
 _PROV_JSON_LEAF_CAP = 10_000.... beyond this, side-table-only (no per-leaf
 wrapper) to avoid O(n) blowup.

We can't set attributes on builtin scalars (int/str), so like openai4s we keep a
side WeakValueDictionary-ish id->tags table for those and only set _openai4s_src
on rich objects (DataFrame/ndarray/dict/list) that accept attributes.
"""
from __future__ import annotations

import builtins
import functools
import json as _json
import os
from typing import Any, Callable

_PROV_ATTR = "_openai4s_src"
_PROV_JSON_LEAF_CAP = 10_000

_installed = False
_host_call: Callable[[str, list], Any] | None = None
_current_cell_id: list[str | None] = [None]
# Stable filesystem root captured when the worker installs provenance. User
# code may chdir later; absolute identity follows that live cwd, while logical
# artifact names stay relative to the kernel's original execution root.
_execution_root: list[str | None] = [None]
# side table for objects that can't hold attributes (id(obj) -> frozenset)
_side_tags: dict[int, frozenset] = {}


def _off() -> bool:
    return os.environ.get("OPENAI4S_PROVENANCE_OFF") == "1"


def set_cell_id(cell_id: str | None) -> None:
    _current_cell_id[0] = cell_id


def get_tags(obj: Any) -> frozenset:
    """Return the source version_id tags carried by obj (empty if none)."""
    t = getattr(obj, _PROV_ATTR, None)
    if isinstance(t, frozenset):
        return t
    return _side_tags.get(id(obj), frozenset())


def set_tags(obj: Any, tags: frozenset) -> Any:
    """Attach tags to obj; fall back to the side table for atomic objects."""
    if not tags:
        return obj
    try:
        object.__setattr__(obj, _PROV_ATTR, frozenset(tags))
    except (AttributeError, TypeError):
        _side_tags[id(obj)] = frozenset(tags)
    return obj


def merge_tags(*objs: Any) -> frozenset:
    out: frozenset = frozenset()
    for o in objs:
        out |= get_tags(o)
    return out


def _canonical_path(path: Any) -> tuple[str, str] | None:
    """Resolve one filesystem argument in the worker's real cwd.

    The host process can have a different cwd from this kernel.  Send it an
    absolute path for identity and a cwd-relative filename for display/logical
    artifact naming.  Paths outside the worker cwd fall back to their basename.
    """
    try:
        raw = os.fsdecode(os.fspath(path))
        # Match Python's file APIs exactly: relative paths use cwd, while a
        # literal '~' is not expanded unless user code expanded it first.
        lexical = os.path.abspath(raw)
        # Identity must follow filesystem symlinks before collapsing `..`.
        # `abspath("link/../x")` is only lexical and can name a different file
        # from the one open() actually reached through `link`.
        absolute = os.path.realpath(raw)
    except (TypeError, ValueError, OSError):
        return None
    try:
        relative = os.path.relpath(
            lexical,
            os.path.abspath(_execution_root[0] or os.getcwd()),
        )
    except (ValueError, OSError):
        relative = os.path.basename(absolute)
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        relative = os.path.basename(absolute)
    elif relative == os.curdir:
        relative = os.path.basename(absolute)
    if not relative:
        return None
    return absolute, relative


def _resolve_version(path: Any) -> str | None:
    if _host_call is None or path is None:
        return None
    location = _canonical_path(path)
    if location is None:
        return None
    try:
        return _host_call("prov_resolve_path", [location[0]])
    except Exception:  # noqa: BLE001 - provenance must never break user code
        return None


def _first_path_arg(args: tuple, kwargs: dict, path_kw: str | None) -> Any:
    if path_kw and path_kw in kwargs:
        return kwargs[path_kw]
    return args[0] if args else None


def _prov_wrap_reader(fn: Callable, path_kw: str | None = None) -> Callable:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = fn(*args, **kwargs)
        if _off():
            return result
        path = _first_path_arg(args, kwargs, path_kw)
        vid = _resolve_version(path)
        if vid:
            set_tags(result, frozenset({vid}))
        return result

    wrapper._openai4s_wrapped = True  # type: ignore[attr-defined]
    return wrapper


def _report_location(location: tuple[str, str] | None, tags: frozenset) -> None:
    if _host_call is None or not tags:
        return
    if location is None:
        return
    try:
        _host_call(
            "prov_record",
            [
                {
                    "path": location[0],
                    "filename": location[1],
                    "input_version_ids": sorted(tags),
                    "producing_cell_id": _current_cell_id[0],
                }
            ],
        )
    except Exception:  # noqa: BLE001
        pass


def _report_write(path: Any, tags: frozenset) -> None:
    _report_location(_canonical_path(path), tags)


def _prov_wrap_method_writer(cls: type, name: str, path_argno: int = 0) -> None:
    orig = getattr(cls, name, None)
    if orig is None or getattr(orig, "_openai4s_wrapped", False):
        return

    @functools.wraps(orig)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        res = orig(self, *args, **kwargs)
        if _off():
            return res
        path = (
            kwargs.get("path_or_buf")
            or kwargs.get("fname")
            or (args[path_argno] if len(args) > path_argno else None)
        )
        tags = get_tags(self)
        if path is not None:
            _report_write(path, tags)
        return res

    wrapper._openai4s_wrapped = True  # type: ignore[attr-defined]
    try:
        setattr(cls, name, wrapper)
    except (AttributeError, TypeError):
        pass


def _prov_wrap_func_writer(
    mod: Any, name: str, path_argno: int = 0, obj_argno: int = 1
) -> None:
    orig = getattr(mod, name, None)
    if orig is None or getattr(orig, "_openai4s_wrapped", False):
        return

    @functools.wraps(orig)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        res = orig(*args, **kwargs)
        if _off():
            return res
        path = args[path_argno] if len(args) > path_argno else None
        obj = args[obj_argno] if len(args) > obj_argno else None
        tags = get_tags(obj)
        if path is not None:
            _report_write(path, tags)
        return res

    wrapper._openai4s_wrapped = True  # type: ignore[attr-defined]
    setattr(mod, name, wrapper)


# --- builtin open: "w" write fires lineage, read tags the handle ---------

_real_open = builtins.open


class _ProvFileWriter:
    """Wraps a write-mode file handle: on close, report lineage from what was
    written (tags gathered from any tagged strings that passed through write)."""

    def __init__(self, fh: Any, path: Any):
        self._fh = fh
        # Freeze the location when open() resolves the relative path. A user
        # may chdir before the handle closes, but that must not change identity.
        self._location = _canonical_path(path)
        self._tags: frozenset = frozenset()

    def write(self, s: Any) -> Any:
        self._tags |= get_tags(s)
        return self._fh.write(s)

    def writelines(self, lines: Any) -> Any:
        seq = list(lines)
        for ln in seq:
            self._tags |= get_tags(ln)
        return self._fh.writelines(seq)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._fh, item)

    def __enter__(self) -> "_ProvFileWriter":
        self._fh.__enter__()
        return self

    def __exit__(self, *exc: object) -> Any:
        res = self._fh.__exit__(*exc)
        _report_location(self._location, self._tags)
        return res

    def close(self) -> None:
        self._fh.close()
        _report_location(self._location, self._tags)


class _ProvFileReader:
    """Wraps a read-mode handle so.read/.readline(s) return TAGGED scalars,
    carrying the source version_id forward through downstream string ops."""

    def __init__(self, fh: Any, tags: frozenset):
        self._fh = fh
        self._tags = tags

    def read(self, *a: Any, **k: Any) -> Any:
        return _tag_scalar(self._fh.read(*a, **k), self._tags)

    def readline(self, *a: Any, **k: Any) -> Any:
        return _tag_scalar(self._fh.readline(*a, **k), self._tags)

    def readlines(self, *a: Any, **k: Any) -> Any:
        return [_tag_scalar(ln, self._tags) for ln in self._fh.readlines(*a, **k)]

    def __iter__(self):
        for ln in self._fh:
            yield _tag_scalar(ln, self._tags)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._fh, item)

    def __enter__(self) -> "_ProvFileReader":
        self._fh.__enter__()
        return self

    def __exit__(self, *exc: object) -> Any:
        return self._fh.__exit__(*exc)


# --- tagged scalar subclasses (openai4s _prov_wrap_scalar / _PROV_SCALAR_BINOPS)

# str/bytes methods that return a same-typed value we should keep tagged
_STR_PROPAGATING = frozenset(
    {
        "upper",
        "lower",
        "strip",
        "lstrip",
        "rstrip",
        "replace",
        "title",
        "capitalize",
        "swapcase",
        "format",
        "format_map",
        "expandtabs",
        "join",
        "zfill",
        "center",
        "ljust",
        "rjust",
        "casefold",
        "encode",
        "decode",
        "__add__",
        "__radd__",
        "__mul__",
        "__rmul__",
        "__getitem__",
        "__mod__",
    }
)


def _make_tagged_class(base: type) -> type:
    """Build a base-subclass whose propagating methods re-tag their results."""

    class _Tagged(base):  # type: ignore[misc, valid-type]
        _openai4s_src: frozenset = frozenset()

        def _wrap(self, value: Any) -> Any:
            if isinstance(value, (str, bytes)) and not isinstance(value, _Tagged):
                return _tag_scalar(value, self._openai4s_src)
            return value

    def _mk(name: str) -> Callable:
        orig = getattr(base, name)

        @functools.wraps(orig)
        def method(self: Any, *a: Any, **k: Any) -> Any:
            res = orig(self, *a, **k)
            return self._wrap(res)  # noqa: SLF001

        return method

    for _name in _STR_PROPAGATING:
        if hasattr(base, _name):
            setattr(_Tagged, _name, _mk(_name))
    return _Tagged


_TaggedStr = _make_tagged_class(str)
_TaggedBytes = _make_tagged_class(bytes)


def _tag_scalar(value: Any, tags: frozenset) -> Any:
    """Return a tagged view of a str/bytes scalar (or side-table for others)."""
    if not tags:
        return value
    try:
        if isinstance(value, str):
            out = _TaggedStr(value)
            out._openai4s_src = frozenset(tags)  # noqa: SLF001
            return out
        if isinstance(value, bytes):
            out = _TaggedBytes(value)
            out._openai4s_src = frozenset(tags)  # noqa: SLF001
            return out
    except Exception:  # noqa: BLE001
        pass
    return set_tags(value, tags)


def _prov_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
    fh = _real_open(file, mode, *args, **kwargs)
    if _off():
        return fh
    if "w" in mode or "a" in mode or "x" in mode:
        return _ProvFileWriter(fh, file)
    # read mode: wrap so.read results carry the source version tag forward
    vid = _resolve_version(file)
    if vid:
        return _ProvFileReader(fh, frozenset({vid}))
    return fh


# --- json.loads propagation ------------------------------------------------

_real_json_loads = _json.loads


def _prov_json_loads(s: Any, *args: Any, **kwargs: Any) -> Any:
    obj = _real_json_loads(s, *args, **kwargs)
    if _off():
        return obj
    tags = get_tags(s)
    if tags:
        set_tags(obj, tags)
    return obj


def install(host_call: Callable[[str, list], Any]) -> None:
    """Monkeypatch the readers/writers. Idempotent; safe if libs are absent."""
    global _installed, _host_call
    _host_call = host_call
    if _installed:
        return
    _execution_root[0] = os.getcwd()
    _installed = True

    # builtins
    builtins.open = _prov_open  # type: ignore[assignment]
    _json.loads = _prov_json_loads  # type: ignore[assignment]

    # pandas
    try:
        import pandas as pd

        for rname, pkw in (
            ("read_csv", "filepath_or_buffer"),
            ("read_parquet", "path"),
            ("read_json", "path_or_buf"),
            ("read_pickle", "filepath_or_buffer"),
            ("read_excel", "io"),
        ):
            if hasattr(pd, rname):
                setattr(pd, rname, _prov_wrap_reader(getattr(pd, rname), pkw))
        for cls in (pd.DataFrame, pd.Series):
            for wname in ("to_csv", "to_parquet", "to_json", "to_pickle"):
                _prov_wrap_method_writer(cls, wname, path_argno=0)
        _patch_dataframe_getitem(pd)
    except ImportError:
        pass

    # numpy
    try:
        import numpy as np

        np.load = _prov_wrap_reader(np.load, "file")  # type: ignore[assignment]
        _prov_wrap_func_writer(np, "save", path_argno=0, obj_argno=1)
    except ImportError:
        pass

    # matplotlib
    try:
        from matplotlib.figure import Figure

        _prov_wrap_method_writer(Figure, "savefig", path_argno=0)
    except ImportError:
        pass


def _patch_dataframe_getitem(pd: Any) -> None:
    """DataFrame[...] / Series[...] carry the parent's tags forward."""
    for cls in (pd.DataFrame, pd.Series):
        orig = cls.__getitem__
        if getattr(orig, "_openai4s_wrapped", False):
            continue

        @functools.wraps(orig)
        def wrapper(self: Any, key: Any, _orig=orig) -> Any:
            res = _orig(self, key)
            if not _off():
                tags = get_tags(self)
                if tags:
                    set_tags(res, tags)
            return res

        wrapper._openai4s_wrapped = True  # type: ignore[attr-defined]
        try:
            cls.__getitem__ = wrapper  # type: ignore[assignment]
        except (AttributeError, TypeError):
            pass


def uninstall() -> None:
    """Restore builtins (best-effort; used by tests)."""
    global _installed
    builtins.open = _real_open  # type: ignore[assignment]
    _json.loads = _real_json_loads  # type: ignore[assignment]
    _execution_root[0] = None
    _installed = False
