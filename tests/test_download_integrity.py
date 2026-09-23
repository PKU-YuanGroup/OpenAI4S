"""Source-declared dataset bytes must be verified before publication."""

from __future__ import annotations

import contextlib
import hashlib
import io
from pathlib import Path

import pytest

from openai4s import webtools

pytestmark = pytest.mark.stubbed_backend


def checksum(body: bytes, algorithm: str = "md5") -> str:
    return (
        algorithm
        + ":"
        + hashlib.new(algorithm, body, usedforsecurity=False).hexdigest()
    )


@pytest.mark.parametrize("algorithm", ["md5", "sha256"])
def test_stream_verifies_source_and_separately_returns_local_sha256(algorithm):
    body = b"wavelength,intensity\n500,0.75\n"
    output = io.BytesIO()
    size, local_sha256 = webtools._copy_capped(
        io.BytesIO(body),
        output,
        len(body),
        expected_size=len(body),
        expected_checksum=checksum(body, algorithm),
    )
    assert output.getvalue() == body
    assert size == len(body)
    assert local_sha256 == hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize("body,expected_size", [(b"abc", 4), (b"abcd", 3)])
def test_size_mismatch_is_not_success(body, expected_size):
    with pytest.raises(webtools.DownloadIntegrityError, match="size"):
        webtools._copy_capped(
            io.BytesIO(body), io.BytesIO(), 100, expected_size=expected_size
        )


def test_matching_size_with_different_bytes_is_refused():
    with pytest.raises(webtools.DownloadIntegrityError, match="checksum"):
        webtools._copy_capped(
            io.BytesIO(b"abd"),
            io.BytesIO(),
            3,
            expected_size=3,
            expected_checksum=checksum(b"abc"),
        )


def test_empty_file_is_a_verifiable_zero_not_an_absent_expectation():
    size, digest = webtools._copy_capped(
        io.BytesIO(b""),
        io.BytesIO(),
        1,
        expected_size=0,
        expected_checksum=checksum(b""),
    )
    assert size == 0 and digest == hashlib.sha256(b"").hexdigest()


@pytest.mark.parametrize(
    "expected_size,expected_checksum",
    [
        (True, None),
        (-1, None),
        (1.5, None),
        (None, ""),
        (None, "md5:bad"),
        (None, "sha1:" + "a" * 40),
    ],
)
def test_bad_declaration_does_not_fetch_or_create_destination(
    tmp_path, monkeypatch, expected_size, expected_checksum
):
    monkeypatch.setattr(
        webtools, "_open_http_response", lambda *_a, **_k: pytest.fail("must not fetch")
    )
    destination = tmp_path / "absent" / "input.csv"
    with pytest.raises(webtools.DownloadIntegrityError):
        webtools.web_download(
            "https://example.test/data",
            destination,
            expected_size=expected_size,
            expected_checksum=expected_checksum,
        )
    assert not destination.parent.exists()


def test_declared_over_budget_is_rejected_before_filesystem_and_network(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        webtools, "_open_http_response", lambda *_a, **_k: pytest.fail("must not fetch")
    )
    destination = tmp_path / "absent" / "input.csv"
    with pytest.raises(webtools.ResponseTooLarge, match="declared"):
        webtools.web_download(
            "https://example.test/data", destination, max_bytes=2, expected_size=3
        )
    assert not destination.parent.exists()


def stub_response(monkeypatch, body):
    @contextlib.contextmanager
    def opened(*_args, **_kwargs):
        yield io.BytesIO(body), "https://example.test/input.csv", "text/csv"

    monkeypatch.setattr(webtools, "_open_http_response", opened)


def test_bad_checksum_keeps_previous_destination_and_discards_staging(
    tmp_path, monkeypatch
):
    stub_response(monkeypatch, b"wrong")
    destination = tmp_path / "input.csv"
    destination.write_bytes(b"previous-good")
    with pytest.raises(webtools.DownloadIntegrityError):
        webtools.web_download(
            "https://example.test/input.csv",
            destination,
            expected_size=5,
            expected_checksum=checksum(b"right"),
        )
    assert destination.read_bytes() == b"previous-good"
    assert list(tmp_path.glob(".input.csv.download-*")) == []


def test_verified_download_publishes_and_reports_exact_checks(tmp_path, monkeypatch):
    body = b"real fixture bytes"
    stub_response(monkeypatch, body)
    destination = tmp_path / "input.csv"
    result = webtools.web_download(
        "https://example.test/input.csv",
        destination,
        expected_size=len(body),
        expected_checksum=checksum(body),
    )
    assert destination.read_bytes() == body
    assert result["sha256"] == hashlib.sha256(body).hexdigest()
    assert result["verified_expectation"] == {
        "size_bytes": len(body),
        "checksum": checksum(body),
    }


def test_cancel_before_start_has_no_network_or_disk_effects(tmp_path, monkeypatch):
    monkeypatch.setattr(
        webtools, "_open_http_response", lambda *_a, **_k: pytest.fail("must not fetch")
    )
    destination = tmp_path / "absent" / "input.csv"
    with pytest.raises(webtools.DownloadCancelled):
        webtools.web_download(
            "https://example.test/input.csv", destination, cancelled=lambda: True
        )
    assert not destination.parent.exists()


def test_cancel_after_read_does_not_write_chunk():
    stopped = False

    class Reader:
        def read(self, _size):
            nonlocal stopped
            stopped = True
            return b"must not be published"

    output = io.BytesIO()
    with pytest.raises(webtools.DownloadCancelled):
        webtools._copy_capped(Reader(), output, 100, cancelled=lambda: stopped)
    assert output.getvalue() == b""


def test_cancel_after_staging_preserves_previous_file(tmp_path, monkeypatch):
    body = b"verified bytes"
    stub_response(monkeypatch, body)
    destination = tmp_path / "input.csv"
    destination.write_bytes(b"previous")
    real_hash = webtools._hash_capped
    stopped = False

    def hashed(*args, **kwargs):
        nonlocal stopped
        result = real_hash(*args, **kwargs)
        stopped = True
        return result

    monkeypatch.setattr(webtools, "_hash_capped", hashed)
    with pytest.raises(webtools.DownloadCancelled):
        webtools.web_download(
            "https://example.test/input.csv",
            destination,
            expected_size=len(body),
            expected_checksum=checksum(body),
            cancelled=lambda: stopped,
        )
    assert destination.read_bytes() == b"previous"
