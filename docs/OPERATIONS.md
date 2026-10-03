# Эксплуатация: воркфлоу, мониторинг, runbook

## Расписание (UTC; Алматы = UTC+5)

| Воркфлоу | Когда | Таймаут | Что делает | Куда сообщает |
|---|---|---|---|---|
| `rescrape.yml` Daily rescrape | 01:00 ежедн. + ручной | 350 мин (мягкий бюджет прохода 320) | база из `db-latest` → `rescrape.py --fail-empty --fail-below 8000` → `send_alerts.py` → VACUUM/gzip/sha256 → `db-latest` → рестарт Space → `snapshot-YYYY-MM-DD` | job summary (JSON счётчиков), утренний отчёт админу, алерты подписчикам, канал |
| `rescrape-rent.yml` | 15:00 ежедн. | 350 мин | то же для аренды (`--deal arenda`, `krisha_rent.db.gz`), вечерний отчёт `--scope rent` | админ |
| `retrain.yml` Weekly retrain | вс 02:00 | 60 мин | база → `train.py --compare-old` → `model_gate.py` → отчёт → `sync_readme_metrics.py` → коммит `models/ reports/ README.md` → `deploy-hf.yml` → `model-latest` | админ (метрики, дельта, вердикт гейта) |
| `ci.yml` | push в main, PR | 15 мин | ruff, pytest (без e2e/live), pip-audit по `requirements.lock --no-deps`; отдельный job — e2e в Chromium | статус коммита |
| `deploy-hf.yml` | push в main, ручной | 35 мин | job `gate` ждёт зелёный CI на этом SHA (до ~33 мин; ручной запуск гейт пропускает) → push снапшота репозитория + `data/build_revision.txt` в Space | — |
| `smoke.yml` Prod smoke | после успешного deploy; 03:17 и 15:17 | 25 мин | ждёт `health.revision == SHA` (до 15 мин) → `smoke_prod.py` (главная, health, demo, predict по демо-лоту, stats, heatmap, forecast/404) | админ при провале |
| `space-keepalive.yml` | каждые 6 ч в :30, ручной (`restart=true`) | 10 мин | `health_check.py`: up/degraded/down по содержимому `/api/health`, состояние между запусками в кэше Actions | админ **только при смене состояния** |
| `backfill-gap-cohort.yml` | только ручной | 20 мин | разовая ретро-разметка когорты восстановительного прохода (issue #156), по умолчанию dry-run | job summary |

Коммиты, меняющие только `data/subscriptions.json`, `tracked.json`, `usage_stats.json`, `channel_posted.json`, `prediction_log.json`, **не** запускают CI и деплой.

## Где смотреть

- **Состояние прода** — `/api/health` (свежесть данных, модель, webhook, ревизия), `/api/metrics` (нагрузка за жизнь процесса), `/readyz`.
- **Последний проход** — job summary рана `Daily rescrape`: `found_in_search`, `discovered_new`, `details_fetched`, `max_new_effective`, `detail_queue_after`, `mode`, `fresh_details_fetched`, `delisted`, `failed_shards`. Те же числа — в утреннем отчёте админу и в `sweep_runs` базы.
- **История проходов и шардов** — таблицы `sweep_runs`, `sweep_shard_stats`, `data_gaps` в `krisha.db` (скачать: `python -m krisha.db_release --require`).
- **История модели** — `models/metrics_history.jsonl`, `reports/model_comparison.json`, отчёты ретрейна в Telegram.
- **Логи Space** — вкладка Logs в HF (живут до рестарта контейнера). Искать по `X-Request-ID` из ответа.
- **Релизы** — `db-latest` (дата обновления asset'а = когда последний раз залилась база), `model-latest`, `snapshot-*`.

## Runbook

### Ночной проход упал

1. Открыть ран → шаг `Rescrape`. Три типичные причины:
   - `--fail-empty`/`--fail-below 8000` сработал: выдача отдала мало → сменилась вёрстка выдачи (`listing_parser.py`) или антибот. Проверить руками одну страницу `SEARCH_URL` с шардовыми параметрами; чинить парсер, фикстуру положить в `tests/fixtures/`.
   - `BanDetected` (403 подряд): алерт уже в Telegram. Не перезапускать сразу. На следующий день пройдёт с теми же паузами; если повторяется — поднять `delay_min/delay_max` инпутами и завести issue.
   - Таймаут раннера: бюджет `--time-budget-min 320` должен был подрезать докачку — если не подрезал, смотреть `fit_detail_caps` в `pass_plan.py` и реальное время запроса (`detail_seconds / detail_requests` в `sweep_runs`).
2. База в `db-latest` осталась предыдущей — ничего не потеряно. Провал одного дня не требует ремонта: следующий проход сам увидит разрыв > 2.5 суток (`RECOVERY_GAP_DAYS`) и пойдёт как восстановительный (пропустит детект снятий, запишет `data_gaps`).
3. Перезапуск руками: `Actions → Daily rescrape → Run workflow` с пустыми инпутами (пресет режима).

### Очередь докачки растёт / «докачано ровно max_new N дней подряд»

- Смотреть `sweep_runs`: `mode`, `max_new_details` vs `max_new_effective`, `detail_queue_after`. Если `mode=steady` при очереди > 5000 — режим не переключился, смотреть лог «Режим прохода» в начале рана.
- Если `max_new_effective` сильно меньше заявленного — режет бюджет времени: либо запросы медленные (`detail_seconds/detail_requests` ≫ 2.7 с), либо фаза выдачи съела бюджет (`search_seconds`).
- Если воркфлоу запущен с явным `max_new` — он перекрывает режим. Инпуты должны быть пустыми в расписании (так было сломано до 02.09.2026).
- Перекос по районам — `sweep_shard_stats`: `quota` vs `fetched` по шардам, `wrapped`, `zero_quota_streak`.

### Space не отвечает / `down` от keepalive

1. HF → Space → Logs. Типично: OOM при старте (2 воркера × ~600 МБ — норма), не скачалась база (`ensure_db` → sha256 mismatch или GitHub недоступен), падение импорта.
2. Рестарт: `Actions → Space keep-alive → Run workflow → restart=true` (нужен `HF_TOKEN`) или кнопка Restart в HF.
3. Если после деплоя: `smoke.yml` покажет, что именно не отвечает; откат — `git revert` в main (деплой пойдёт сам после CI).

### `degraded`: `tg_webhook != ok`

- `no_token` — секрет не задан в Space; `no_public_url` — нет `SPACE_HOST`/`PUBLIC_BASE_URL`; `unset`/`mismatch` — самолечение сработает при следующем `/api/health` (не чаще раза в час); `error` — api.telegram.org недоступен с HF (бывает по IPv6) → `TG_API_BASE` на прокси-воркер.
- Проверить извне: `getWebhookInfo` с токеном бота.

### `freshness: stale` при живом Space

База в контейнере старше 30 ч. Значит либо проход не заливал `db-latest`, либо Space не перезапустился на новой базе (`HF_TOKEN` не задан → шаг «Restart Space» пропущен). Рестартнуть Space руками.

### Ретрейн: гейт не прошёл

Это штатная ситуация, не инцидент: новая модель не опубликована, в проде старая, отчёт с дельтами пришёл админу. Разбирать, если повторяется 2–3 недели подряд: смотреть `metrics.old_model` vs `metrics.model` в job summary, `test_representativeness` (тест мог перекоситься по районам), `dedup.dropped_pct` (резкий рост = дубли в сборе). `old_model_error` в мете — набор фичей разошёлся, гейт fail-closed; это ошибка в PR, а не в данных.

### Ретрейн: коммит моделей не прошёл / деплой не запустился

`retrain.yml` коммитит от `github-actions[bot]` и дёргает `gh workflow run deploy-hf.yml`. Если ветка main защищена от пуша бота — коммит упадёт; если `GITHUB_TOKEN` не имеет `actions:write` — деплой не запустится, но модели уже в main → запустить `Deploy to HF Space` руками (гейт CI при ручном запуске пропускается — убедиться, что CI зелёный).

### Подписки/слежка «потерялись»

- Файлы шифруются ключом из `STATE_ENCRYPTION_KEY` или, если её нет, из `TELEGRAM_BOT_TOKEN`. Сменили токен без ключа → расшифровать нельзя. Восстановление: старый токен → `STATE_ENCRYPTION_KEY`, перешифровать.
- В логах Space `GITHUB_PAT/GITHUB_TOKEN не задан` → состояние жило только до деплоя. Выдать PAT.
- Конфликтные записи (два писателя) сливаются автоматически; `STATE_FORCE_OVERWRITE=1` — только если файл в репозитории битый и его нужно перезаписать локальной копией.

### Krisha сменила вёрстку

Симптомы: `parse_anomalies` растёт, `discovered_new` есть, а `details_fetched` ≈ 0, или предикт по любой ссылке даёт 502. Порядок: `window.data` обычно стабилен → сначала PARAMS-селекторы `detail_parser.py`; положить реальную страницу в `tests/fixtures/` и покрыть `tests/test_detail_parser.py`. Для выдачи — `listing_parser.py` (тесты выдачи живут рядом, в `tests/test_detail_parser.py`).

### Нужно откатить базу

`snapshot-YYYY-MM-DD` релизы: скачать нужный `krisha.db.gz`, проверить sha256, залить как asset в `db-latest` (перезаписать), рестартнуть Space. Перед этим — `scripts/restore_drill.py`, чтобы убедиться, что архив разворачивается.

### Учения и профилактика

- `python scripts/restore_drill.py` — раз в квартал или после изменений в `db_release.py`.
- `pip-audit` в CI падает на новой уязвимости → обновить зависимость в `pyproject.toml`, `make lock`, закоммитить все три лока одним PR.
- Dependabot открывает PR на actions и зависимости; мержить после зелёного CI.
- `scripts/loadtest.py` перед/после правок горячего пути — числа в CHANGELOG.

## Секреты и доступы

| Что | Где лежит | Кто использует |
|---|---|---|
| `HF_TOKEN` (write на Space) | GitHub Secrets | деплой, рестарт после рескрейпа, keepalive |
| `TELEGRAM_BOT_TOKEN`, `TG_ADMIN_CHAT_ID`, `TG_CHANNEL_ID` | GitHub Secrets **и** переменные Space | Actions — рассылки; Space — бот |
| `GITHUB_PAT` (fine-grained, contents:write), `STATE_ENCRYPTION_KEY`, `GEMINI_API_KEY`, `PREDICTION_LOG` | только Space | state-файлы, шифрование, Gemini, лог предиктов |
| `PROD_BASE_URL` | GitHub Variables | смоук |

Токен бота — один и тот же в двух местах; при ротации менять оба и `STATE_ENCRYPTION_KEY` задать до ротации.
