"""Compatibility tests for the ``openai4s_worker_runtime`` alias package.

PR 09 of the refactor roadmap (docs/refactor-plan.md, section G) begins the
Option-4 rename: keep ``openai4s_compute_provider`` primary and add an
``openai4s_worker_runtime`` alias that re-exports the same public contract.
These tests pin that contract so neither name can silently drift:

  * both packages import cleanly (old imports keep working);
  * both declare the identical ``__all__``;
  * every public symbol is the *same object* under both names, so identity
    and ``isinstance`` checks are interchangeable across the two imports;
  * the alias is a pure re-export — it defines no public symbol of its own
    and does not shadow the runtime's private submodules.

All offline; no Docker, GPU, network, or secrets involved.
"""
import openai4s_compute_provider as legacy
import openai4s_worker_runtime as alias

# The key symbols of the worker-runtime public contract. A rename that drops
# any of these breaks provider shims (skills/remote-compute-*/provider.py).
KEY_SYMBOLS = (
    "ByocError",
    "ByocProvider",
    "ByocResident",
    "ExecResult",
    "ScrubWriter",
    "scrub_secret_env",
    "read_auth",
    "write_event",
    "write_ready",
    "BASE_ERROR_KINDS",
    "BASELINE_SECRET_PREFIXES",
    "COMPRESSED_CAP_DEFAULT",
    "EXIT_PROTOCOL",
    "IDLE_TIMEOUT_S",
    "STAGE_PREFIX",
    "TAIL_BYTES",
    "WORK",
)


def test_legacy_package_still_exports_key_symbols():
    """The primary (legacy-named) package keeps its full public contract."""
    for name in KEY_SYMBOLS:
        assert hasattr(legacy, name), f"openai4s_compute_provider lost {name!r}"
    assert set(legacy.__all__) == set(KEY_SYMBOLS)


def test_alias_exports_the_same_all():
    assert set(alias.__all__) == set(legacy.__all__)


def test_alias_symbols_are_identical_objects():
    """Not equal — *identical*. ``openai4s_worker_runtime.ByocError`` must be
    the same class object as ``openai4s_compute_provider.ByocError`` so
    except/isinstance clauses work across mixed imports."""
    for name in KEY_SYMBOLS:
        assert getattr(alias, name) is getattr(legacy, name), (
            f"alias re-export {name!r} is not the identical object from "
            "openai4s_compute_provider"
        )


def test_alias_is_pure_reexport():
    """The alias defines nothing public of its own beyond the re-exports."""
    extra_public = {
        name
        for name in vars(alias)
        if not name.startswith("_") and name not in set(alias.__all__)
        # 'annotations' is bound by the alias's `from __future__ import annotations`
        and name != "annotations"
    }
    assert extra_public == set(), f"unexpected public names in alias: {extra_public}"


def test_alias_has_no_main_entrypoint():
    """The runnable confined-process entrypoint stays under the primary name
    (``python -m openai4s_compute_provider``); the alias deliberately ships
    no ``__main__`` so there is exactly one entrypoint code path."""
    import importlib.util
    import pathlib

    pkg_dir = pathlib.Path(alias.__file__).parent
    assert not (pkg_dir / "__main__.py").exists()
    assert importlib.util.find_spec("openai4s_worker_runtime.__main__") is None


def test_alias_does_not_shadow_private_submodules():
    """Private runtime internals stay in the primary package only — the alias
    must not grow copies of _protocol/_resident/_channel/_constants."""
    import pathlib

    pkg_dir = pathlib.Path(alias.__file__).parent
    py_files = sorted(p.name for p in pkg_dir.glob("*.py"))
    assert py_files == ["__init__.py"], f"alias package grew files: {py_files}"
