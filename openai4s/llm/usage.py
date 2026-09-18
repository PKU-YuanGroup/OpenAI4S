"""Private metering evidence carried beside the compatible public usage dict.

Trust is a property of a value's TYPE here, not of the keys it happens to
carry. That is forced by CPython: ``dict(x)`` on a ``dict`` subclass builds a
plain ``dict`` through the concrete fast path, and there is no dunder to
intercept it -- so ``dict()``, ``{**}``, a comprehension and a JSON round trip
CANNOT be made to preserve provenance. A meter therefore refuses a plain
mapping rather than re-deriving counters from one. Every copy channel Python
does route through a dunder is sealed on ``_EvidenceCarrier``.

The direction matters. Refusing an unattested value costs an
``llm_*_unknown`` row in the governance ledger, which is loud. Trusting one
certified counters the evidence had refused: an unmeasured reply entering the
Auto Mode budget commit and the team quota ledger as though it had been
measured, which is silent.
"""

from __future__ import annotations

import copy as _copy
import logging
from dataclasses import dataclass
from typing import Any, Mapping, Union

_LOG = logging.getLogger("openai4s.llm.usage")

_UNSET: Any = object()

#: The only counter names a ledger may charge.
_COUNTER_KEYS = (
    "input_tokens",
    "output_tokens",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cache_read",
    "cache_write",
    "reasoning_tokens",
)


@dataclass(frozen=True)
class UsageEvidence:
    raw: Mapping[str, Any]
    counters: Mapping[str, int]
    final: bool
    invalid: frozenset[str] = frozenset()
    #: False only for a value that reached a typed boundary carrying no
    #: provenance at all. Defaults True so every existing positional
    #: construction keeps meaning exactly what it meant.
    attested: bool = True


#: What a value gets when it arrives with nothing: counters unknown.
UNATTESTED = UsageEvidence({}, {}, final=False, invalid=frozenset(), attested=False)


def _items(value: Any) -> dict:
    """Accept a Mapping or an iterable of pairs; swallow anything else.

    ``dataclasses.asdict`` rebuilds a dict field as ``type(obj)(generator)``,
    so a Mapping-only constructor made that rebuild either raise or silently
    produce an empty value.
    """
    if isinstance(value, Mapping):
        return dict(value)
    try:
        return dict(value or ())
    except (TypeError, ValueError):
        return {}


class _EvidenceCarrier(dict):
    """A usage dict whose provenance survives every copy Python can intercept."""

    def _rebuild(self, values: Any) -> "_EvidenceCarrier":
        raise NotImplementedError

    def copy(self) -> "_EvidenceCarrier":  # dict.copy would return a plain dict
        return self._rebuild(dict(self))

    def __copy__(self) -> "_EvidenceCarrier":
        return self._rebuild(dict(self))

    def __deepcopy__(self, memo: dict) -> "_EvidenceCarrier":
        return self._rebuild(_copy.deepcopy(dict(self), memo))

    def __or__(self, other: Any) -> "_EvidenceCarrier":
        return self._rebuild(dict(self) | dict(other))

    def __ror__(self, other: Any) -> "_EvidenceCarrier":
        return self._rebuild(dict(other) | dict(self))


class RawUsage(_EvidenceCarrier):
    """Provider counters, with whether they describe the finished generation."""

    def __init__(self, value: Any = None, *, final: Any = _UNSET) -> None:
        super().__init__(_items(value))
        # Finality is stated by the adapter that knows it. A reconstruction
        # that cannot state it -- ``dataclasses.asdict`` -- gets the unknown
        # answer rather than inheriting "measured" from a default. Every
        # in-tree construction passes ``final=`` explicitly.
        self.final = final is True

    def _rebuild(self, values: Any) -> "RawUsage":
        return RawUsage(values, final=self.final)

    def __reduce__(self) -> tuple:
        return (_rebuild_raw, (dict(self), self.final))


def _rebuild_raw(values: Any, final: bool) -> RawUsage:
    return RawUsage(values, final=final)


class UsageMetrics(_EvidenceCarrier):
    """JSON still sees only historical keys; accounting also sees their source."""

    def __init__(self, values: Any = (), evidence: Any = None) -> None:
        super().__init__(_items(values))
        # Optional, and unattested when omitted. A required positional made
        # ``asdict`` raise TypeError -- loud, but it leaves no usable value.
        # An honest "unknown" is loud enough (the meter refuses) and survivable.
        self.evidence = evidence if isinstance(evidence, UsageEvidence) else UNATTESTED

    def _rebuild(self, values: Any) -> "UsageMetrics":
        return UsageMetrics(values, self.evidence)

    def __reduce__(self) -> tuple:
        return (UsageMetrics, (dict(self), self.evidence))


class MeasuredUsage(_EvidenceCarrier):
    """The verdict of a measurement: the only value a ledger may charge.

    Constructed by :func:`measured_usage` alone. It exists because the one
    production path that meters twice -- ``ledger._canonical_usage`` hands its
    result to ``record_session_llm_usage``, which measures again -- must carry
    its verdict in its type. Otherwise the second call has to re-derive the
    verdict from the counters' shape, and a fallback that does that cannot
    tell an already-vetted dict from a stripped copy.
    """

    def __init__(self, counters: Any = (), evidence: Any = None) -> None:
        super().__init__(_items(counters))
        self.evidence = (
            evidence
            if isinstance(evidence, UsageEvidence)
            else UsageEvidence({}, dict(self), final=True, attested=True)
        )

    def _rebuild(self, values: Any) -> "MeasuredUsage":
        return MeasuredUsage(values, self.evidence)

    def __reduce__(self) -> tuple:
        return (MeasuredUsage, (dict(self), self.evidence))


#: What a meter accepts. A bare ``Mapping`` is deliberately not in it.
AttestedUsage = Union[UsageMetrics, RawUsage, MeasuredUsage]


def copy_usage(value: Any, values: Mapping[str, Any] | None = None) -> UsageMetrics:
    """Preserve private evidence when a typed boundary copies/projects counters."""
    projected = values if values is not None else value
    projected = dict(projected) if isinstance(projected, Mapping) else {}
    evidence = getattr(value, "evidence", None)
    if not isinstance(evidence, UsageEvidence):
        if isinstance(value, RawUsage):
            evidence = usage_evidence(value, _scan(value) if value.final else {})
        else:
            # Was ``usage_evidence(raw, measured_usage(raw))``, which minted a
            # first-class UsageEvidence asserting final=True over an untrusted
            # key scan. That did not merely fail to restore lost provenance --
            # it forged it, so a value that had only *lost* its evidence came
            # out looking audited to every later reader. Laundering a dropped
            # copy is worse than the drop.
            evidence = UNATTESTED
    return UsageMetrics(projected, evidence)


def _scan(value: Mapping[str, Any]) -> dict[str, int]:
    """The allow-listed key scan, now reachable only from a *typed* value."""
    result = {
        key: item
        for key, item in value.items()
        if key in _COUNTER_KEYS and type(item) is int and item >= 0
    }
    for canonical, alias in (
        ("input_tokens", "prompt_tokens"),
        ("output_tokens", "completion_tokens"),
    ):
        if canonical not in result and alias in result:
            result[canonical] = result[alias]
    return result


def measured_usage(value: Any) -> MeasuredUsage:
    """Only explicitly measured non-negative integers may enter a ledger."""
    if isinstance(value, MeasuredUsage):
        return value  # already vetted; never re-derive a verdict
    if isinstance(value, RawUsage):
        return MeasuredUsage(_scan(value) if value.final else {})
    evidence = getattr(value, "evidence", None)
    if not isinstance(evidence, UsageEvidence):
        if isinstance(value, Mapping) and _scan(value):
            # A refusal is otherwise undiagnosable: governance swallows every
            # exception, so a legitimate producer nobody attested surfaces days
            # later as a team-wide quota lockout. Counters that are *visible*
            # and still unattested is exactly that signature.
            _LOG.debug(
                "refusing unattested usage carrying counters: %s",
                sorted(_scan(value)),
            )
        return MeasuredUsage({})
    if not (evidence.final and evidence.attested):
        return MeasuredUsage({})
    return MeasuredUsage(evidence.counters)


def charge_call(sink: Any, outcome: Any) -> None:
    """Hand one provider call's usage to a meter, on either path.

    ``outcome`` is the reply mapping on success or the raised exception on
    failure. A call that never left the process is not billable -- that is
    ``llm.chat``'s ``llm_not_started`` evidence, and reading it in one place
    keeps the call sites that must honour it from drifting apart. Before it
    existed, a failure raised before the first attempt was counted read as
    *started* and became two lockout-grade ``llm_*_unknown`` rows.

    Never raises. A meter that fails must not change what its caller returns:
    three of the callers are security screeners whose contract is to fail open,
    so a metering exception there would silently downgrade a real UNSAFE
    verdict to SAFE -- the screening would look like it had run and passed.
    """
    if sink is None:
        return
    try:
        if isinstance(outcome, BaseException):
            if getattr(outcome, "llm_not_started", False):
                return
            sink(getattr(outcome, "usage", None))
        else:
            sink((outcome or {}).get("usage"))
    except Exception:  # noqa: BLE001 - metering never changes an answer
        _LOG.debug("usage sink failed", exc_info=True)


def measured_total(value: Any) -> int | None:
    counters = measured_usage(value)
    if "input_tokens" in counters and "output_tokens" in counters:
        return max(
            counters["input_tokens"] + counters["output_tokens"],
            counters.get("total_tokens", 0),
        )
    return counters.get("total_tokens")


def usage_evidence(
    raw: Mapping[str, Any],
    counters: Mapping[str, int],
    invalid: frozenset[str] = frozenset(),
) -> UsageEvidence:
    return UsageEvidence(
        _copy.deepcopy(dict(raw)),
        dict(counters),
        getattr(raw, "final", True),
        invalid,
        True,  # normalize_usage is the attestation seam
    )
