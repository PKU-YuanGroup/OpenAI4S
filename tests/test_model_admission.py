"""Connector-neutral checkpoint admission requires a real matching canary."""

from __future__ import annotations

import pytest

from openai4s.host.model_admission import ModelAdmissionError, ModelAdmissionLedger


def _key(ledger: ModelAdmissionLedger) -> str:
    key = ledger.key(
        operation="predict",
        backend_revision="0123456789abcdef",
        checkpoint_digest="a" * 64,
        execution_target="local",
    )
    assert key is not None
    return key


def test_staged_checkpoint_is_not_self_admitting():
    ledger = ModelAdmissionLedger("example-connector")

    with pytest.raises(ModelAdmissionError, match="not admitted"):
        ledger.require(_key(ledger))


def test_matching_canary_admits_only_the_exact_runtime_identity():
    ledger = ModelAdmissionLedger("example-connector")
    key = _key(ledger)

    admitted = ledger.admit(
        key,
        canary_attempt_id="canary-001",
        operation="predict",
        backend_revision="0123456789abcdef",
        requested_checkpoint_digest="a" * 64,
        observed_checkpoint_digest="a" * 64,
        execution_target="local",
        verified_at="2026-08-22T00:00:00+00:00",
    )

    assert ledger.require(key) == admitted
    remote_key = ledger.key(
        operation="predict",
        backend_revision="0123456789abcdef",
        checkpoint_digest="a" * 64,
        execution_target="ssh:lab",
    )
    with pytest.raises(ModelAdmissionError, match="not admitted"):
        ledger.require(remote_key)


def test_canary_digest_mismatch_never_creates_admission():
    ledger = ModelAdmissionLedger("example-connector")
    key = _key(ledger)

    with pytest.raises(ModelAdmissionError, match="different"):
        ledger.admit(
            key,
            canary_attempt_id="canary-bad",
            operation="predict",
            backend_revision="0123456789abcdef",
            requested_checkpoint_digest="a" * 64,
            observed_checkpoint_digest="b" * 64,
            execution_target="local",
            verified_at="2026-08-22T00:00:00+00:00",
        )

    assert ledger.get(key) is None


def test_new_ledger_requires_a_fresh_canary_after_process_restart():
    first = ModelAdmissionLedger("example-connector")
    key = _key(first)
    first.admit(
        key,
        canary_attempt_id="canary-001",
        operation="predict",
        backend_revision="0123456789abcdef",
        requested_checkpoint_digest="a" * 64,
        observed_checkpoint_digest="a" * 64,
        execution_target="local",
        verified_at="2026-08-22T00:00:00+00:00",
    )

    restarted = ModelAdmissionLedger("example-connector")
    with pytest.raises(ModelAdmissionError, match="not admitted"):
        restarted.require(_key(restarted))
