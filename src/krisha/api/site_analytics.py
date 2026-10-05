"""Счётчик посещаемости и подтверждение прав в поисковиках — из окружения.

Идентификатор счётчика и коды подтверждения живут в переменных Space, а не в
разметке: форк или локальный запуск без переменных не шлёт визиты в чужую
статистику, а пустое окружение означает «счётчика нет» — и CSP остаётся
прежней, без внешних адресов.

* ``GA4_MEASUREMENT_ID`` — идентификатор потока Google Analytics 4 (``G-…``);
* ``YANDEX_VERIFICATION`` — код из Яндекс Вебмастера (meta ``yandex-verification``);
* ``GOOGLE_SITE_VERIFICATION`` — код из Google Search Console
  (meta ``google-site-verification``).

Подтвердить права можно и DNS-записью TXT в Cloudflare — тогда два последних
кода не нужны. Сам счётчик подключает ``static/js/analytics.js``: сервер
дописывает его в ``<head>`` с ``data-ga``. Яндекс Метрику убрали 06.10.2026:
владелец решил обойтись GA4, а политика описывает только то, что подключено.
"""

from __future__ import annotations

import html
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

logger = logging.getLogger(__name__)

GA_ENV = "GA4_MEASUREMENT_ID"
YANDEX_VERIFICATION_ENV = "YANDEX_VERIFICATION"
GOOGLE_VERIFICATION_ENV = "GOOGLE_SITE_VERIFICATION"

_GA_RE = re.compile(r"G-[A-Z0-9]{4,16}")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{8,128}")


@dataclass(frozen=True)
class Config:
    ga: str | None = None
    yandex_verification: str | None = None
    google_verification: str | None = None

    @property
    def counters(self) -> bool:
        return bool(self.ga)


def _read(env: Mapping[str, str], name: str, rx: re.Pattern[str]) -> str | None:
    value = (env.get(name) or "").strip()
    if not value:
        return None
    if not rx.fullmatch(value):
        # значение попадает в атрибут разметки — мусор не пропускаем, но и не падаем
        logger.warning("%s: значение не похоже на настоящее, пропускаю", name)
        return None
    return value


def from_env(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    return Config(
        ga=_read(env, GA_ENV, _GA_RE),
        yandex_verification=_read(env, YANDEX_VERIFICATION_ENV, _TOKEN_RE),
        google_verification=_read(env, GOOGLE_VERIFICATION_ENV, _TOKEN_RE),
    )


# ------------------------------------------------------------------ CSP
# GA4 — набор адресов для gtag.js без рекламных функций (документация Google).
_GA_SCRIPT = ("https://www.googletagmanager.com",)
_GA_IMG = ("https://www.googletagmanager.com", "https://*.google-analytics.com")
_GA_CONNECT = (
    "https://www.googletagmanager.com", "https://*.google-analytics.com",
    "https://*.analytics.google.com", "https://*.google.com",
)


def csp_sources(cfg: Config) -> dict[str, tuple[str, ...]]:
    """Что дописать в директивы CSP ради подключённых счётчиков."""
    extra: dict[str, list[str]] = {}

    def add(directive: str, sources: tuple[str, ...]) -> None:
        extra.setdefault(directive, []).extend(sources)

    if cfg.ga:
        add("script-src", _GA_SCRIPT)
        add("img-src", _GA_IMG)
        add("connect-src", _GA_CONNECT)
    return {k: tuple(dict.fromkeys(v)) for k, v in extra.items()}


def extend_csp(csp: str, extra: Mapping[str, tuple[str, ...]]) -> str:
    """Дописывает источники в директивы CSP; новой директивы нет — добавляет её."""
    if not extra:
        return csp
    parts = [p.strip() for p in csp.split(";") if p.strip()]
    names = [p.split()[0] for p in parts]
    for directive, sources in extra.items():
        if directive in names:
            i = names.index(directive)
            have = parts[i].split()
            parts[i] = " ".join(have + [s for s in sources if s not in have])
        else:
            parts.append(" ".join((directive, *sources)))
            names.append(directive)
    return "; ".join(parts)


# ------------------------------------------------------------------ <head>


def head_snippet(cfg: Config, script_url: str) -> str:
    """Скрипт счётчиков для <head> каждой страницы; пусто — счётчиков нет."""
    if not cfg.counters:
        return ""
    attrs = "".join(
        f' data-{name}="{html.escape(value)}"' for name, value in (("ga", cfg.ga),) if value
    )
    return f'<script src="{html.escape(script_url)}" defer{attrs}></script>'


def verification_meta(cfg: Config) -> str:
    """Коды подтверждения прав для главной: Яндекс Вебмастер и Search Console."""
    tags = []
    if cfg.yandex_verification:
        tags.append(f'<meta name="yandex-verification" content="{html.escape(cfg.yandex_verification)}">')
    if cfg.google_verification:
        tags.append(f'<meta name="google-site-verification" content="{html.escape(cfg.google_verification)}">')
    return "\n".join(tags)
