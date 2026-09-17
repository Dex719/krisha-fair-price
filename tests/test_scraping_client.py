"""Тесты PoliteClient: семантика 403 (бан) vs 429 (троттлинг) vs 468 (anti-bot челлендж).

403/429 — issue #101; 468 (SafeLine WAF) — спека `.kiro/specs/safeline-468`.
"""

import pytest

from krisha.scraping.client import BanDetected, ChallengeBlocked, PoliteClient


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


# --- SafeLine WAF: HTTP 468 + страница proof-of-work -----------------------
# Ключевое отличие от 403/429: отказ привязан к СЕССИИ (cookie `sl-session`),
# поэтому ретрай обязан идти со свежим клиентом. Мокаем именно фабрику сессий,
# а не `_client.get`, — иначе после сброса подмена бы слетела.

CHALLENGE_BODY = (
    '<!DOCTYPE html><html><head><meta charset="utf-8">'
    '<link rel="icon" href="/.safeline/static/favicon.png" type="image/png">'
    '<title id="slg-title"></title></head><body>_pow</body></html>'
)


class _FakeSession:
    """Сессия, отдающая коды из общего итератора; помнит, закрывали ли её."""

    def __init__(self, codes, body_for):
        self.codes = codes
        self.body_for = body_for
        self.closed = False
        self.calls = 0

    def get(self, url):
        self.calls += 1
        code = next(self.codes)
        return _FakeResponse(code, text=self.body_for(code))

    def close(self):
        self.closed = True


def _challenge_client(codes, *, max_retries=3, body_for=None):
    """PoliteClient, у которого каждая новая сессия — свежий _FakeSession."""
    body_for = body_for or (lambda code: CHALLENGE_BODY if code == 468 else "ok")
    it = iter(codes)
    client = PoliteClient(
        delay_range=(0, 0), max_retries=max_retries, throttle_wait_s=0,
        raise_on_challenge=True,
    )
    sessions = []

    def factory():
        session = _FakeSession(it, body_for)
        sessions.append(session)
        return session

    client._new_session = factory
    client._client = factory()
    client.challenge_wait_s = 0
    return client, sessions


def test_468_retries_with_a_fresh_session_and_succeeds():
    """AC-1.1: два челленджа, потом 200 — клиент отдаёт HTML, счётчик считает 468."""
    client, sessions = _challenge_client([468, 468, 200])

    assert client.get("http://x/1") == "ok"
    assert client.counters["http_468"] == 2
    assert client.counters["http_200"] == 1


def test_468_creates_a_new_session_each_attempt():
    """AC-1.3: после челленджа внутренний клиент — ДРУГОЙ объект, старый закрыт."""
    client, sessions = _challenge_client([468, 468, 200])
    first = client._client

    client.get("http://x/1")

    assert len(sessions) == 3, "по сессии на каждую попытку"
    assert client._client is not first
    assert first.closed is True


def test_468_on_every_attempt_raises_challenge_blocked():
    """AC-1.2: исчерпали попытки — это внешний отказ, а не None."""
    client, _ = _challenge_client([468, 468, 468])

    with pytest.raises(ChallengeBlocked):
        client.get("http://x/1")


def test_challenge_page_served_with_http_200_is_not_parsed_as_listing():
    """AC-3.1: челлендж под кодом 200 распознаётся по разметке, а не уезжает в парсер."""
    client, _ = _challenge_client(
        [200, 200], max_retries=2, body_for=lambda code: CHALLENGE_BODY
    )

    with pytest.raises(ChallengeBlocked):
        client.get("http://x/1")
    assert client.counters["http_468"] == 2
    assert client.counters["http_200"] == 0


def test_challenge_does_not_count_toward_ban_streak():
    """Челлендж — не бан по IP: серию 403 он не продолжает и не сбрасывает в бан."""
    client, _ = _challenge_client([468, 468, 468], max_retries=3)
    client.ban_streak_threshold = 1

    with pytest.raises(ChallengeBlocked):
        client.get("http://x/1")
    assert client._ban_streak == 0


def test_mixed_challenge_and_404_returns_none_without_raising():
    """Не «все попытки челлендж» — значит обычный неуспех, без ChallengeBlocked."""
    client, _ = _challenge_client([468, 404], max_retries=2)

    assert client.get("http://x/1") is None
    assert client.counters["http_468"] == 1
    assert client.counters["http_404"] == 1


def test_crawler_default_returns_none_instead_of_raising():
    """Краулер ловит только BanDetected: ChallengeBlocked ему прилетать не должен.

    Ночной проход разбирает неуспех сам (непокрытый шард, пропущенный лот) —
    новое исключение по умолчанию уронило бы его трейсбеком на ровном месте.
    Смена сессии при этом работает для всех: она и есть польза для rescrape,
    где за один проход насчитали 3648 челленджей.
    """
    client, sessions = _challenge_client([468, 468, 468])
    client.raise_on_challenge = False

    assert client.get("http://x/1") is None
    assert client.counters["http_468"] == 3
    assert len(sessions) == 4, "сессия меняется и без подъёма исключения"
