"""The local backend: this machine as a resource plane.

Every install has this one, which is why the reconciler's harder paths —
INV-8 reconciliation in particular — are implemented here rather than
stubbed: a backend that answered "of course this submission is new" would
leave the invariant untested everywhere except a cluster nobody has in CI.
"""

from openai4s.orchestration.local.backend import MAX_CONCURRENT, LocalBackend

__all__ = ["MAX_CONCURRENT", "LocalBackend"]
