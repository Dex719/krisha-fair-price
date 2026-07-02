# Docker-образ для Hugging Face Spaces (и любого другого Docker-хостинга).
# HF ожидает приложение на порту 7860 и непривилегированного пользователя.
FROM python:3.11-slim

RUN useradd -m -u 1000 app
WORKDIR /app

COPY --chown=app pyproject.toml README.md ./
COPY --chown=app src ./src
# -e: пакет остаётся в /app/src, чтобы ROOT_DIR указывал на /app (модели, база, статика)
RUN pip install --no-cache-dir -e .

COPY --chown=app models ./models
COPY --chown=app data ./data
COPY --chown=app static ./static

USER app
ENV PYTHONUNBUFFERED=1
EXPOSE 7860
CMD ["uvicorn", "krisha.api.app:app", "--host", "0.0.0.0", "--port", "7860"]
