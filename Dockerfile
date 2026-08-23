# Docker-образ для Hugging Face Spaces (и любого другого Docker-хостинга).
# HF ожидает приложение на порту 7860 и непривилегированного пользователя.
# Пин на конкретный минор + digest (issue #119) — плавающий `3.11-slim`
# молча подтягивает новый патч на каждый ребилд без кэша.
FROM python:3.11.15-slim@sha256:baf89808ec37adeaab83cec287adb4a2afa4a11c1d51e961c7ec737877e61af6

# curl только для HEALTHCHECK ниже (issue #119) — несколько МБ, не тянет
# обратно plotting-хвост, который мы только что убрали.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 app
WORKDIR /app

COPY --chown=app pyproject.toml README.md requirements-runtime.lock ./
# requirements-runtime.lock = requirements.lock минус plotting-хвост catboost
# (matplotlib/plotly/graphviz/pillow/... — ~100+ МБ, нужны только plot_*/
# calc_feature_statistics, не fit/predict/save_model) — см. scripts/gen_runtime_lock.py
# (issue #119). --no-deps обязателен: лок уже полностью плоский, без него pip
# заново подтянет весь хвост из Requires-Dist самого catboost.
# Кэшируемый слой: сначала зафиксированные версии зависимостей (issue #67,
# находка #3 аудита — плавающие `>=` без лока давали разъезд prod/train).
RUN pip install --no-cache-dir --no-deps -r requirements-runtime.lock
COPY --chown=app src ./src
# -e --no-deps: пакет остаётся в /app/src, чтобы ROOT_DIR указывал на /app
# (модели, база, статика); зависимости уже стоят из лока выше, повторно их
# не резолвим.
RUN pip install --no-cache-dir -e . --no-deps

COPY --chown=app models ./models
COPY --chown=app data ./data
COPY --chown=app static ./static

USER app
# HF free tier обычно даёт 2 vCPU: ограничиваем native-пулы потоков,
# чтобы один predict не забивал весь Space и latency был стабильнее.
ENV PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=2 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1
EXPOSE 7860
# issue #119: HF keepalive пинговал только "жив ли процесс" — не отличал
# рабочий Space от одного с протухшей моделью/базой (см. /api/health).
# Бюджет намеренно щедрый: /api/health раз в час дёргает Telegram
# (webhook_status самолечит вебхук), а это до 10 с на коннект и до 15 с на
# ответ — при недоступном api.telegram.org (типичная ситуация на HF Spaces
# по IPv6) прежние 5 с гарантированно истекали, три раза подряд, и Docker
# объявлял здоровый контейнер мёртвым. --max-time дополнительно ограничивает
# сам curl, чтобы проба не висела дольше своего timeout.
HEALTHCHECK --interval=60s --timeout=35s --start-period=60s --retries=3 \
    CMD curl -fsS --max-time 30 http://localhost:7860/api/health || exit 1
# Воркеров по числу ядер (HF free tier — 2 vCPU). Один процесс упирался в GIL:
# пока он сериализует статистику или считает признаки, остальные запросы ждут.
# Два процесса делят один сокет и удваивают пропускную способность на
# python-тяжёлых путях; память — ~600 МБ на воркер при 16 ГБ на Space.
# Переменной WEB_CONCURRENCY=1 всё возвращается к прежнему поведению.
# Совместная работа воркеров разведена явно: старт под флоком (одна закачка
# базы и одни миграции), дедуп телеграм-апдейтов в таблице tg_updates,
# статистика использования сливается при флаше, а не перетирается.
ENV WEB_CONCURRENCY=2
CMD ["sh", "-c", "exec uvicorn krisha.api.app:app --host 0.0.0.0 --port 7860 --workers ${WEB_CONCURRENCY:-2}"]
