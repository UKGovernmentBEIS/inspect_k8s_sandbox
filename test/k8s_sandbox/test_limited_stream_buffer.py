import pytest

from k8s_sandbox._pod.buffer import LimitedBuffer


@pytest.fixture
def invalid_utf8() -> bytes:
    return b"\x80"


def test_does_not_truncate():
    sut = LimitedBuffer(1024)

    sut.append(b"abcde")
    sut.append(b"fghij")
    actual = str(sut)

    assert actual == "abcdefghij"
    assert not sut.truncated


def test_does_not_truncate_on_limit():
    sut = LimitedBuffer(5)

    sut.append(b"abcde")
    actual = str(sut)

    assert actual == "abcde"
    assert not sut.truncated


def test_truncates_ascii_one_append():
    sut = LimitedBuffer(5)

    sut.append(b"abcdefghij")
    actual = str(sut)

    assert actual == "abcde"
    assert sut.truncated


def test_truncates_ascii_multiple_appends():
    sut = LimitedBuffer(5)

    sut.append(b"abcde")
    sut.append(b"fghij")
    actual = str(sut)

    assert actual == "abcde"
    assert sut.truncated


def test_truncates_unicode():
    sut = LimitedBuffer(7)

    # a: 1 byte, Ǟ: 2 bytes, 😀: 4 bytes
    sut.append("aǞ😀xxx".encode("utf-8"))
    actual = str(sut)

    assert actual == "aǞ😀"
    assert _count_bytes(actual) == 7
    assert sut.truncated


def test_truncates_unicode_without_raising_decode_error():
    sut = LimitedBuffer(5)

    # 😀: 4 bytes
    sut.append("abcd😀".encode("utf-8"))
    actual = str(sut)

    # The 4-byte character is simply discarded.
    assert actual == "abcd"
    assert _count_bytes(actual) == 4
    assert sut.truncated


def test_replaces_interior_invalid_utf8(invalid_utf8: bytes):
    sut = LimitedBuffer(1024)

    sut.append(b"abcde" + invalid_utf8 + b"fghij")

    assert str(sut) == "abcde�fghij"
    assert not sut.truncated


def test_replaces_invalid_utf8_at_end_if_not_truncated(invalid_utf8: bytes):
    sut = LimitedBuffer(1024)

    sut.append(b"abcde" + invalid_utf8)

    assert str(sut) == "abcde�"
    assert not sut.truncated


def _count_bytes(string: str) -> int:
    return len(string.encode("utf-8"))
