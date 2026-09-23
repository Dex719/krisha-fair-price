# Micro Spec: прод отдаёт webp/woff2 как octet-stream и жмёт их gzip-ом

**Type:** Bug Fix (прод)
**Effort:** 1 час
**Date:** 2026-09-23

## What
На проде (`python:3.11-slim`, `starlette==1.3.1` из lock) бинарная статика отдаётся так:

| Файл | Content-Type | gzip |
|---|---|---|
| `/static/img/city-860.webp` | `application/octet-stream` | да |
| `/static/fonts/*.woff2` (6 шт.) | `application/octet-stream` | да |
| `/static/avatar.png` | `image/png` | да |

При этом на всех ответах `X-Content-Type-Options: nosniff`.

## Why
- **Тип.** `StaticFiles` берёт тип из `mimetypes.guess_type`, а в slim-образе нет
  системного `/etc/mime.types`, встроенная же таблица Python 3.11 не знает
  `.webp`/`.woff2`/`.woff` (проверено: Windows/3.12 даёт `None`; WSL с
  `/etc/mime.types` — правильные типы). Неверный тип при `nosniff` — нарушение
  контракта HTTP и лотерея по браузерам.
- **gzip.** `GZipMiddleware` в `starlette 1.3.1` жмёт всё, кроме
  `text/event-stream`: уже сжатые webp/woff2/png гоняются через gzip-9 на каждом
  запросе — CPU на 2 vCPU без выигрыша в размере.
- **Почему CI молчал.** Тест `test_static_precompress::…images_stay_immutable`
  ловит ровно это, но CI ставит зависимости из `pyproject` без пинов (starlette
  1.7.0) и на Ubuntu-раннере с `/etc/mime.types` — там тест зелёный. Локально
  (WSL, Windows) — на прод-версиях из lock — он красный с 2026-09-17 и числился
  «шумом среды». Выравнивание CI с lock — отдельная micro-спека `ci-uses-lock`.

## How
- `app.py`: явная таблица типов бинарной статики (`.webp`, `.woff2`, `.woff`,
  `.png`, `.jpg`, `.jpeg`) — `_CachedStatic.file_response` ставит `Content-Type`
  по ней, не завися от `mimetypes` платформы.
- `app.py`: `GZipMiddleware` → подкласс, который не жмёт запросы к этим
  расширениям (решение по пути, до ответа; не зависит от версии starlette).
- Тесты: тип и отсутствие `Content-Encoding` для webp и woff2.

## Acceptance
`pytest tests/test_static_precompress.py` зелёный на прод-версиях (starlette 1.3.1,
WSL и Windows); после выката `curl -I --compressed` по webp/woff2/png на проде —
верный `Content-Type`, без `Content-Encoding`.

## Files
- `src/krisha/api/app.py`, `tests/test_static_precompress.py`
