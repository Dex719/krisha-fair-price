"""Тесты PoliteClient: разделение семантики 403 (бан) vs 429 (троттлинг), issue #101."""

import pytest

from krisha.scraping.client import BanDetected, PoliteClient


class _FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


def _client(max_retries=2, ban_streak_threshold=2) -> PoliteClient:
    return PoliteClient(
        delay_range=(0, 0),
        max_retries=max_retries,
        throttle_wait_s=0,
        ban_streak_threshold=ban_streak_threshold,
    )


def test_429_backs_off_and_succeeds_without_touching_ban_streak():
    codes = iter([429, 200])
    client = _client()
    client._client.get = lambda url: _FakeResponse(next(codes), text="ok")

    assert client.get("http://x/1") == "ok"
    assert client._ban_streak == 0


def test_429_alone_never_raises_ban_detected():
    client = _client(max_retries=1, ban_streak_threshold=1)
    client._client.get = lambda url: _FakeResponse(429)

    assert client.get("http://x/1") is None
    assert client._ban_streak == 0


def test_403_streak_raises_ban_detected_after_threshold():
    client = _client(max_retries=1, ban_streak_threshold=2)
    client._client.get = lambda url: _FakeResponse(403)

    assert client.get("http://x/1") is None  # 1-й URL: streak=1, ниже порога
    with pytest.raises(BanDetected):
        client.get("http://x/2")  # 2-й URL подряд: streak=2 >= порога


def test_403_streak_resets_on_success():
    responses = iter([_FakeResponse(403), _FakeResponse(200, text="ok"), _FakeResponse(403)])
    client = _client(max_retries=1, ban_streak_threshold=2)
    client._client.get = lambda url: next(responses)

    assert client.get("http://x/1") is None  # 403 → streak=1
    assert client.get("http://x/2") == "ok"  # успех → streak сброшен
    assert client.get("http://x/3") is None  # 403 → streak=1 снова, ниже порога


def test_mixed_403_and_429_on_one_url_does_not_count_toward_streak():
    """Один URL, где часть попыток 403 и часть 429 — не «все попытки 403»,
    не должен продвигать серию бана (это скорее нестабильный троттлинг)."""
    codes = iter([403, 429])
    client = _client(max_retries=2, ban_streak_threshold=1)
    client._client.get = lambda url: _FakeResponse(next(codes))

    assert client.get("http://x/1") is None
    assert client._ban_streak == 0


def test_network_error_resets_streak():
    import httpx

    def raise_error(url):
        raise httpx.ConnectError("boom")

    client = _client(max_retries=1, ban_streak_threshold=1)
    client._client.get = raise_error

    assert client.get("http://x/1") is None
    assert client._ban_streak == 0
