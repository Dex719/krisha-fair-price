"""Аудит безопасности 2026-10-06 (инфраструктура и раскрытие):

* /.well-known/security.txt (RFC 9116) и SECURITY.md — раньше оба отдавали 404;
* воркер домена не пропускает CORS-заголовки прокси HF (он отражал любой Origin);
* воркер Telegram-прокси с переменной BOT_ID пересылает только своего бота.

Поведение JS-воркеров проверяется настоящим node (если он есть): воркер
импортируется как модуль, глобальный fetch подменён заглушкой. Без node
остаются текстовые проверки исходников.
"""

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from krisha.api import app as app_module
from krisha.api.app import app

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
SECURITY_TXT = "/.well-known/security.txt"
ADVISORY_URL = "https://github.com/Dex719/krisha-fair-price/security/advisories/new"
TELEGRAM_URL = "https://t.me/Hopepe1"
POLICY_URL = "https://github.com/Dex719/krisha-fair-price/blob/main/SECURITY.md"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="нет node — JS-воркеры проверены только текстом")


def _fields(text: str) -> dict[str, list[str]]:
    """Поля security.txt: имя → список значений (Contact встречается несколько раз)."""
    result: dict[str, list[str]] = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            name, _, value = line.partition(":")
            result.setdefault(name.strip(), []).append(value.strip())
    return result


# ------------------------------------------------------------- security.txt (app)


def test_security_txt_is_plain_text_with_cache_and_noindex(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://bagam.example")
    with TestClient(app) as client:
        r = client.get(SECURITY_TXT)

    assert r.status_code == 200
    assert r.headers["content-type"] == "text/plain; charset=utf-8"
    assert r.headers["cache-control"] == "public, max-age=86400"
    # служебный адрес: в индекс поисковика он не нужен
    assert r.headers["x-robots-tag"] == "noindex"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_security_txt_has_required_rfc9116_fields(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://bagam.example")
    with TestClient(app) as client:
        fields = _fields(client.get(SECURITY_TXT).text)

    # Contact и Expires обязательны по RFC 9116, Expires — ровно один раз
    assert fields["Contact"] == [ADVISORY_URL, TELEGRAM_URL]
    assert len(fields["Expires"]) == 1
    assert fields["Expires"][0] == "2027-10-01T00:00:00.000Z"
    assert fields["Preferred-Languages"] == ["ru, en"]
    assert fields["Policy"] == [POLICY_URL]
    # адрес на домене сайта, а не на Space (PUBLIC_BASE_URL, как у sitemap)
    assert fields["Canonical"] == ["https://bagam.example/.well-known/security.txt"]


def test_security_txt_canonical_defaults_to_site_domain(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("SPACE_HOST", "dex719-krisha-fair-price.hf.space")
    with TestClient(app) as client:
        text = client.get(SECURITY_TXT).text

    assert _fields(text)["Canonical"] == ["https://bagam.info/.well-known/security.txt"]
    assert "hf.space" not in text


def test_security_txt_expires_is_in_the_future_and_within_a_year():
    """Просроченный security.txt клиенты вправе игнорировать (RFC 9116 §2.5.5).

    Красный тест после даты — не поломка, а напоминание продлить
    SECURITY_TXT_EXPIRES (и не дальше чем на год вперёд, как просит RFC).
    """
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", app_module.SECURITY_TXT_EXPIRES)
    expires = datetime.strptime(app_module.SECURITY_TXT_EXPIRES, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)

    assert expires > now, "security.txt просрочен: продлите SECURITY_TXT_EXPIRES в app.py"
    assert (expires - now).days <= 366, "RFC 9116 советует Expires не дальше года вперёд"


def test_security_txt_head_has_no_body_but_real_length(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://bagam.example")
    with TestClient(app) as client:
        get = client.get(SECURITY_TXT)
        head = client.head(SECURITY_TXT)

    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-type"] == get.headers["content-type"]
    assert head.headers["cache-control"] == get.headers["cache-control"]
    assert head.headers["content-length"] == str(len(get.content))
    assert head.headers["x-robots-tag"] == "noindex"


def test_security_txt_is_hidden_from_schema_and_sitemap(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://bagam.example")
    with TestClient(app) as client:
        schema = client.get("/openapi.json").text
        sitemap = client.get("/sitemap.xml").text

    assert "security.txt" not in schema
    assert "security.txt" not in sitemap


def test_other_well_known_paths_stay_404_and_noindex():
    """Маршрут один: остальное под /.well-known/ — обычный 404, тоже noindex.
    Адрес со слэшем на конце не принимается за страницу (301 только у страниц)."""
    with TestClient(app) as client:
        other = client.get("/.well-known/nope")
        slash = client.get(SECURITY_TXT + "/", follow_redirects=False)

    assert other.status_code == 404 and other.headers["x-robots-tag"] == "noindex"
    assert slash.status_code == 404


def test_well_known_prefix_is_noindex():
    assert "/.well-known/" in app_module._NOINDEX_PREFIXES


# ------------------------------------------------------------------- SECURITY.md


def test_security_md_matches_security_txt():
    """SECURITY.md и security.txt — одни и те же контакты: расхождение
    отправило бы исследователя в канал, который автор не читает."""
    text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")

    for needle in (ADVISORY_URL, TELEGRAM_URL, "@Hopepe1", "1.x", "main", "bagam.info",
                   "dex719-krisha-fair-price.hf.space", "@fairprice_kzbot", "krisha.kz",
                   "DoS", "социальная инженерия"):
        assert needle in text, needle
    assert tuple(app_module.SECURITY_TXT_CONTACTS) == (ADVISORY_URL, TELEGRAM_URL)
    assert app_module.SECURITY_TXT_POLICY == POLICY_URL


# ------------------------------------------------------------ воркеры: текст


def test_domain_worker_strips_every_cors_header_and_caches_security_txt():
    worker = (INFRA / "domain-worker.js").read_text(encoding="utf-8")

    for name in ("access-control-allow-origin", "access-control-allow-credentials",
                 "access-control-allow-methods", "access-control-allow-headers",
                 "access-control-expose-headers", "access-control-max-age"):
        assert f'"{name}"' in worker, name
    assert '"/.well-known/security.txt"' in worker
    # остальное из test_page_privacy: воркер по-прежнему назван Cloudflare Worker
    assert "Cloudflare Worker" in worker


def test_tg_proxy_documents_bot_id_and_takes_env():
    worker = (INFRA / "tg-proxy-worker.js").read_text(encoding="utf-8")

    assert "async fetch(request, env)" in worker
    assert "BOT_ID" in worker
    # шаг настройки с объяснением, зачем (иначе релей открыт для любого токена)
    assert "6. Воркер" in worker and "открытым" in worker


# ------------------------------------------------------------ воркеры: node

DOMAIN_RUNNER = """
import worker from "./domain-worker.mjs";

const upstream = {
  "content-type": "application/json",
  "access-control-allow-origin": "https://evil.example",
  "access-control-allow-credentials": "true",
  "access-control-allow-methods": "POST",
  "access-control-allow-headers": "content-type",
  "access-control-expose-headers": "*",
  "access-control-max-age": "600",
  "link": '<https://huggingface.co/spaces/x>; rel="canonical"',
  "x-proxied-host": "hf",
  "x-custom": "kept",
};
const calls = [];
globalThis.fetch = async (url, init) => {
  calls.push({ url: String(url), cached: Boolean(init && init.cf) });
  return new Response(null, { status: 200, headers: upstream });
};

async function run(method, path, headers = {}) {
  calls.length = 0;
  const res = await worker.fetch(
    new Request("https://bagam.info" + path, { method, headers }),
    { PROXY_KEY: "k" },
  );
  return { status: res.status, headers: Object.fromEntries(res.headers), calls: calls.slice() };
}

console.log(JSON.stringify({
  preflight: await run("OPTIONS", "/api/predict", { origin: "https://evil.example" }),
  simple: await run("GET", "/api/stats", { origin: "https://evil.example" }),
  page: await run("GET", "/about"),
  security: await run("GET", "/.well-known/security.txt"),
  other_well_known: await run("GET", "/.well-known/nope"),
}));
"""

TG_RUNNER = """
import worker from "./tg-proxy-worker.mjs";

const seen = [];
globalThis.fetch = async (req) => { seen.push(req.url); return new Response("ok"); };

async function call(path, env) {
  seen.length = 0;
  const res = await worker.fetch(new Request("https://tg.example.workers.dev" + path), env);
  return { status: res.status, forwarded: seen.slice() };
}

console.log(JSON.stringify({
  // без BOT_ID — прежнее поведение: любой /bot*
  no_env: await call("/bot999:zzz/getMe", undefined),
  empty_var: await call("/bot999:zzz/getMe", { BOT_ID: "" }),
  not_a_bot_path: await call("/other", {}),
  own_bot: await call("/bot123:abc/getMe", { BOT_ID: "123" }),
  own_bot_number_var: await call("/bot123:abc/getMe", { BOT_ID: 123 }),
  own_bot_spaces: await call("/bot123:abc/getMe", { BOT_ID: " 123\\n" }),
  other_bot: await call("/bot999:zzz/getMe", { BOT_ID: "123" }),
  longer_id: await call("/bot1234:abc/getMe", { BOT_ID: "123" }),
  no_colon: await call("/bot123/getMe", { BOT_ID: "123" }),
  dot_segments: await call("/bot123:abc/../bot999:zzz/getMe", { BOT_ID: "123" }),
  encoded_dot_segments: await call("/bot123:abc/%2e%2e/bot999:zzz/getMe", { BOT_ID: "123" }),
  token_pasted_whole: await call("/bot123:abc/getMe", { BOT_ID: "123:abc" }),
  not_a_bot_path_with_id: await call("/other", { BOT_ID: "123" }),
}));
"""


def _run_node(tmp_path: Path, runner: str, worker_file: str) -> dict:
    # .mjs: у репозитория нет package.json с "type": "module", а воркер — ES-модуль
    (tmp_path / worker_file.replace(".js", ".mjs")).write_text(
        (INFRA / worker_file).read_text(encoding="utf-8"), encoding="utf-8"
    )
    script = tmp_path / "runner.mjs"
    script.write_text(runner, encoding="utf-8")
    done = subprocess.run(
        [shutil.which("node"), str(script)], capture_output=True, text=True,
        encoding="utf-8", timeout=60, cwd=tmp_path,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@needs_node
def test_node_checks_syntax_of_both_workers(tmp_path):
    for name in ("domain-worker.js", "tg-proxy-worker.js"):
        copy = tmp_path / name.replace(".js", ".mjs")
        copy.write_text((INFRA / name).read_text(encoding="utf-8"), encoding="utf-8")
        done = subprocess.run([shutil.which("node"), "--check", str(copy)], capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, f"{name}: {done.stderr}"


@needs_node
def test_domain_worker_drops_reflected_cors_headers(tmp_path):
    out = _run_node(tmp_path, DOMAIN_RUNNER, "domain-worker.js")

    for case in ("preflight", "simple", "page", "security"):
        headers = out[case]["headers"]
        assert out[case]["status"] == 200, case
        assert not [h for h in headers if h.startswith("access-control-")], (case, headers)
    # остальное из ответа апстрима остаётся как было (кроме уже вычищаемого прежде)
    headers = out["preflight"]["headers"]
    assert headers["content-type"] == "application/json" and headers["x-custom"] == "kept"
    assert "link" not in headers and "x-proxied-host" not in headers


@needs_node
def test_domain_worker_caches_security_txt_but_not_api(tmp_path):
    out = _run_node(tmp_path, DOMAIN_RUNNER, "domain-worker.js")

    assert out["security"]["calls"] == [
        {"url": "https://dex719-krisha-fair-price.hf.space/.well-known/security.txt", "cached": True}
    ]
    assert out["page"]["calls"][0]["cached"] is True
    assert out["preflight"]["calls"][0]["cached"] is False
    assert out["simple"]["calls"][0]["cached"] is False
    # другие адреса под /.well-known/ в кэш не попадают
    assert out["other_well_known"]["calls"][0]["cached"] is False


@needs_node
def test_tg_proxy_without_bot_id_keeps_old_behavior(tmp_path):
    out = _run_node(tmp_path, TG_RUNNER, "tg-proxy-worker.js")

    for case in ("no_env", "empty_var"):
        assert out[case]["status"] == 200, case
        assert out[case]["forwarded"] == ["https://api.telegram.org/bot999:zzz/getMe"], case
    assert out["not_a_bot_path"] == {"status": 404, "forwarded": []}


@needs_node
def test_tg_proxy_with_bot_id_forwards_only_own_bot(tmp_path):
    out = _run_node(tmp_path, TG_RUNNER, "tg-proxy-worker.js")

    for case in ("own_bot", "own_bot_number_var", "own_bot_spaces"):
        assert out[case]["status"] == 200, case
        assert out[case]["forwarded"] == ["https://api.telegram.org/bot123:abc/getMe"], case
    # чужой бот, чужой бот с общим началом id, путь без «:», обход через «..»
    # (адрес нормализуется до проверки) и токен целиком в переменной — закрыто
    for case in ("other_bot", "longer_id", "no_colon", "dot_segments", "encoded_dot_segments",
                 "token_pasted_whole", "not_a_bot_path_with_id"):
        assert out[case] == {"status": 404, "forwarded": []}, case
