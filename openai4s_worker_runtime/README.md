# Worker-runtime compatibility alias

[中文说明](README_zh.md)

A single `__init__.py` that re-exports [`openai4s_compute_provider`](../openai4s_compute_provider) under a name that says what the package actually is. There is no second runtime in here, and nothing behaves differently when imported through this name.

## Where this fits

New provider or host code may import the public worker-runtime symbols from `openai4s_worker_runtime`; existing integrations keep importing the legacy package. Both names resolve to the same class and function objects. The private implementation modules and the executable entry point stay under `openai4s_compute_provider`.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Re-exports the legacy package's public surface: the provider contracts, the resident, the channel helpers, the limits, the error kinds, the paths, and the secret-scrub function. Every symbol is the same object under both names, so identity and `isinstance` checks hold across the two imports. |

## Compatibility boundaries

- The alias has no `__main__.py`. Launch the confined-process entry point through `openai4s_compute_provider`.
- Private modules such as `_resident.py` and `_protocol.py` are neither duplicated nor aliased as submodules.
- The alias adds nothing: no confinement, no persistence, no provider discovery, no maturity guarantee. The real trust and failure boundaries are described in the [primary runtime README](../openai4s_compute_provider/README.md).

## Related documentation

- [Primary worker runtime](../openai4s_compute_provider/README.md)
- [Compute backend](../openai4s/compute/README.md)
- [Package boundaries](../docs/package-architecture.md)
