"""Регрессии для HF runtime-оптимизаций.

Эти тесты защищают прод от двух классов поломок:
- startup не должен падать из-за best-effort warmup;
- тяжёлые train-only зависимости не должны вернуться в runtime install.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from krisha.api import app as app_mod

ROOT = Path(__file__).resolve().parents[1]


def test_startup_runs_warmup_without_db_download(monkeypatch, tmp_path):
    """Startup должен вызывать warmup и продолжать настраивать webhook.

    В HF это важно: база/релиз/модели могут быть недоступны временно, но порядок
    startup-операций должен оставаться контролируемым и тестируемым.
    """
    calls: list[str] = []

    monkeypatch.setattr(app_mod.db_release, "ensure_db", lambda: calls.append("ensure_db"))
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "missing.db")
    monkeypatch.setattr(app_mod, "_warmup_runtime_caches", lambda: calls.append("warmup"))
    monkeypatch.setattr(app_mod.bot, "setup_webhook", lambda: calls.append("webhook"))

    app_mod._startup()

    assert calls == ["ensure_db", "warmup", "webhook"]


def test_warmup_runtime_caches_calls_expected_loaders(monkeypatch):
    """Warmup должен заранее загрузить модели и пространственные индексы."""
    from krisha import geo, predict, spatial

    calls: list[str] = []
    monkeypatch.setattr(predict, "load_model", lambda: calls.append("model"))
    monkeypatch.setattr(predict, "load_interval_models", lambda: calls.append("interval"))
    monkeypatch.setattr(spatial, "load_spatial_ref", lambda: calls.append("spatial"))
    monkeypatch.setattr(geo, "load_poi_index", lambda: calls.append("poi"))

    app_mod._warmup_runtime_caches()

    assert calls == ["model", "interval", "spatial", "poi"]


def test_warmup_runtime_caches_is_fail_soft(monkeypatch):
    """Warmup — оптимизация, а не обязательное условие старта приложения."""
    from krisha import predict

    warnings: list[str] = []

    def boom():
        raise RuntimeError("model is temporarily unavailable")

    monkeypatch.setattr(predict, "load_model", boom)
    monkeypatch.setattr(
        app_mod.logger,
        "warning",
        lambda message, *args, **kwargs: warnings.append(str(message)),
    )

    app_mod._warmup_runtime_caches()

    assert warnings == ["runtime warmup failed"]


def test_train_only_dependencies_are_not_runtime_dependencies():
    """shap/matplotlib нужны для отчёта обучения, но не для HF runtime."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime = "\n".join(data["project"]["dependencies"])
    train = data["project"]["optional-dependencies"]["train"]

    assert "shap" not in runtime
    assert "matplotlib" not in runtime
    assert any(dep.startswith("shap") for dep in train)
    assert any(dep.startswith("matplotlib") for dep in train)
