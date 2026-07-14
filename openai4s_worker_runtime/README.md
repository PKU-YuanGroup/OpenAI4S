# Worker-runtime compatibility alias

[中文说明](README_zh.md)

This package provides the accurately named import alias for [`openai4s_compute_provider`](../openai4s_compute_provider). It contains no separate runtime implementation and changes no behavior.

## Place in the architecture

New provider or host code may import public worker-runtime symbols from `openai4s_worker_runtime`; existing integrations continue to import the legacy package. Both names resolve to the same class/function objects. Private implementation modules and the executable entry point remain under `openai4s_compute_provider`.

## Files

| File | Responsibility |
|---|---|
| [`__init__.py`](__init__.py) | Re-exports the legacy package's public provider contracts, resident, channel helpers, limits, error kinds, paths, and scrub function with object identity preserved. |

## Subdirectories

There are no tracked child directories in this package.

## Compatibility boundaries

- This alias has no `__main__.py`; launch the confined-process entry point through `openai4s_compute_provider`.
- Private modules such as `_resident.py` and `_protocol.py` are not duplicated or aliased as submodules.
- The alias adds no confinement, persistence, provider discovery, or maturity guarantee. See the [primary runtime README](../openai4s_compute_provider/README.md) for the actual trust and failure boundaries.

## Related documentation

- [Primary worker runtime](../openai4s_compute_provider/README.md)
- [Compute backend](../openai4s/compute/README.md)
- [Package boundaries](../docs/package-architecture.md)
