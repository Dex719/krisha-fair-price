"""Общие настройки тестов."""

import os

# В тестах не скачиваем базу из GitHub Release при старте приложения
# (TestClient триггерит startup-событие FastAPI).
os.environ.setdefault("KRISHA_DB_AUTO", "0")
