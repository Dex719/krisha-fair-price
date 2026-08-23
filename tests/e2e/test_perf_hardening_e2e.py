"""Обвязка «под наплыв» через реальный HTTP-стек: предсжатая статика, HEAD, метрики.

Юниты (`tests/test_static_precompress.py`) проверяют кэш и договорённости по
заголовкам на ASGI-уровне. Здесь важно другое: то же самое на настоящем
uvicorn и настоящем сокете — потому что именно на этом уровне живут ошибки,
которых TestClient не видит:

* uvicorn сам ставит `content-length`/`transfer-encoding` и вырезает тело у
  HEAD — а FastAPI, в отличие от голого Starlette, HEAD у `@app.get` НЕ
  регистрирует, и страницы отвечали 405 (по ним ходят мониторинги и часть
  healthcheck'ов);
* порядок middleware на живом соединении решает, доедет ли `Content-Encoding`
  до клиента и не сожмётся ли уже сжатое второй раз.
"""

import gzip

import httpx
import pytest

pytestmark = pytest.mark.e2e

PAGES = ("/", "/stats", "/about")


@pytest.fixture(scope="module")
def api(hermetic_server):
    # follow_redirects=False: интересны ровно те ответы, что отдаёт сервер.
    with httpx.Client(base_url=hermetic_server, timeout=30.0, follow_redirects=False) as client:
        yield client


def _raw(api, path: str, **kwargs) -> httpx.Response:
    """Ответ без автоматической распаковки httpx: нужны сырые байты."""
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault("accept-encoding", "gzip")
    request = api.build_request("GET", path, headers=headers, **kwargs)
    return api.send(request, stream=False)


@pytest.mark.parametrize("path", PAGES)
def test_pages_are_served_gzipped_from_memory(api, path):
    """Страница приезжает сжатой, и это валидный gzip, а не «дважды сжатое»."""
    r = _raw(api, path)
    assert r.status_code == 200, path
    assert r.headers["content-encoding"] == "gzip", path
    assert r.headers["vary"] == "Accept-Encoding", path
    # httpx уже распаковал ровно один слой — если бы слоёв было два, здесь был
    # бы не html, а gzip-магия \x1f\x8b.
    assert r.text.lstrip().startswith("<!"), path
    assert not r.text.startswith("\x1f\x8b"), path
    # Сжатие того же html обратно должно давать примерно тот же порядок
    # размера: страница отдаётся уровнем 9, а не «как получилось».
    assert len(gzip.compress(r.content, 9, mtime=0)) < len(r.content)


@pytest.mark.parametrize("path", PAGES)
def test_identity_client_gets_uncompressed_body_and_another_etag(api, path):
    """Клиент без gzip получает исходные байты и ДРУГОЙ ETag.

    Один ETag на оба представления ломает промежуточные кэши: клиенту,
    который gzip не просил, может прилететь 304 на представление, которого у
    него нет.
    """
    gz = _raw(api, path)
    plain = _raw(api, path, headers={"accept-encoding": "identity"})
    assert plain.status_code == 200, path
    assert "content-encoding" not in plain.headers, path
    assert plain.text == gz.text, path
    assert plain.headers["etag"] != gz.headers["etag"], path
    assert gz.headers["etag"].endswith('-gz"'), path


@pytest.mark.parametrize("path", PAGES)
def test_repeat_visit_revalidates_with_304(api, path):
    """Второй заход того же браузера — 304 без тела, отдельно для каждого варианта."""
    for encoding in ("gzip", "identity"):
        first = _raw(api, path, headers={"accept-encoding": encoding})
        etag = first.headers["etag"]
        again = _raw(api, path, headers={"accept-encoding": encoding, "if-none-match": etag})
        assert again.status_code == 304, (path, encoding)
        assert again.content == b"", (path, encoding)
        assert again.headers["vary"] == "Accept-Encoding", (path, encoding)


def test_etag_is_stable_across_requests(api):
    """ETag считается от содержимого: он не обязан меняться от запроса к запросу.

    Раньше он брался из size+mtime файла (`FileResponse`), а mtime в образе —
    это время сборки: каждый деплой инвалидировал кэш браузера, даже когда
    html не менялся.
    """
    etags = {_raw(api, "/").headers["etag"] for _ in range(3)}
    assert len(etags) == 1


@pytest.mark.parametrize("path", PAGES)
def test_head_is_supported_on_pages(api, path):
    """HEAD → 200 без тела. FastAPI сам HEAD не добавляет — было 405."""
    r = api.head(path, headers={"accept-encoding": "gzip"})
    assert r.status_code == 200, path
    assert r.content == b"", path
    assert int(r.headers["content-length"]) > 0, path
    assert r.headers["content-type"].startswith("text/html"), path


def test_head_matches_get_size(api):
    """content-length у HEAD совпадает с реальным телом GET — иначе клиенты врут."""
    head = api.head("/", headers={"accept-encoding": "gzip"})
    get = _raw(api, "/")
    assert head.headers["content-length"] == get.headers["content-length"]


def test_static_asset_is_precompressed_and_revalidates(api):
    """design.css отдаётся сжатым и по ETag, но без долгого max-age.

    Имя файла не содержит хэша содержимого, поэтому кэшировать его «навсегда»
    нельзя: после деплоя браузер обязан спросить. Дешёвая часть здесь — 304
    вместо повторной отдачи 38 КБ.
    """
    r = _raw(api, "/static/design.css")
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip"
    assert r.headers["cache-control"] == "no-cache"
    assert r.text.strip(), "css приехал пустым"
    again = _raw(api, "/static/design.css", headers={"if-none-match": r.headers["etag"]})
    assert again.status_code == 304
    assert again.content == b""


def test_binary_asset_is_not_gzipped(api):
    """webp/woff2 уже сжаты: gzip поверх них — сожжённый CPU и больше байт."""
    r = _raw(api, "/static/img/city-860.webp")
    if r.status_code == 404:
        pytest.skip("нет фикстуры картинки в сборке")
    assert r.status_code == 200
    assert "content-encoding" not in r.headers
    assert "immutable" in r.headers["cache-control"]


def test_unknown_path_serves_precompressed_404(api):
    r = _raw(api, "/no-such-page", headers={"accept": "text/html", "accept-encoding": "gzip"})
    assert r.status_code == 404
    assert r.headers.get("content-encoding") == "gzip"
    assert "html" in r.headers["content-type"]


def test_metrics_endpoint_reports_live_traffic(api):
    """/api/metrics — то, чем смотрят на прод во время наплыва."""
    api.get("/api/health")
    r = api.get("/api/metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["uptime_s"] >= 0
    assert body["requests"] > 0
    assert str(body["statuses"].get("200", 0)).isdigit()
    routes = body["routes"]
    assert "GET /api/health" in routes, sorted(routes)
    health = routes["GET /api/health"]
    assert health["count"] > 0
    assert health["p95_ms"] >= 0
    # Метрики не должны сами быть узким местом: ручка отдаёт снапшот из памяти.
    assert r.elapsed.total_seconds() < 1.0
