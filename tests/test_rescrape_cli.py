"""Тесты CLI scripts/rescrape.py: коды выхода --fail-empty / --fail-below (issue #97)."""

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "rescrape.py"
_spec = importlib.util.spec_from_file_location("rescrape_cli", _SCRIPT_PATH)
rescrape_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rescrape_cli)


def _run(monkeypatch, argv, stats):
    monkeypatch.setattr(rescrape_cli, "sweep", lambda **kw: stats)
    monkeypatch.setattr(sys, "argv", ["rescrape.py", *argv])


def test_fail_empty_exits_on_zero(monkeypatch):
    stats = {"found_in_search": 0, "suspicious": False}
    _run(monkeypatch, ["--fail-empty"], stats)
    with pytest.raises(SystemExit) as exc:
        rescrape_cli.main()
    assert exc.value.code == 1


def test_fail_empty_exits_on_suspicious(monkeypatch):
    """issue #97: suspicious (просевший parse-rate) тоже красит CI, даже при found>0."""
    stats = {"found_in_search": 8000, "suspicious": True, "parse_rate_median_7": 40000}
    _run(monkeypatch, ["--fail-empty"], stats)
    with pytest.raises(SystemExit) as exc:
        rescrape_cli.main()
    assert exc.value.code == 1


def test_fail_empty_passes_when_healthy(monkeypatch):
    stats = {"found_in_search": 40000, "suspicious": False}
    _run(monkeypatch, ["--fail-empty"], stats)
    rescrape_cli.main()  # не должно поднять SystemExit


def test_fail_below_exits_under_threshold(monkeypatch):
    stats = {"found_in_search": 15000, "suspicious": False}
    _run(monkeypatch, ["--fail-below", "20000"], stats)
    with pytest.raises(SystemExit) as exc:
        rescrape_cli.main()
    assert exc.value.code == 1


def test_fail_below_passes_above_threshold(monkeypatch):
    stats = {"found_in_search": 40000, "suspicious": False}
    _run(monkeypatch, ["--fail-below", "20000"], stats)
    rescrape_cli.main()


def _capture(monkeypatch, argv):
    """Возвращает kwargs, с которыми CLI вызвал sweep."""
    captured = {}
    monkeypatch.setattr(rescrape_cli, "sweep", lambda **kw: captured.update(kw) or {"found_in_search": 1, "suspicious": False})
    monkeypatch.setattr(sys, "argv", ["rescrape.py", *argv])
    rescrape_cli.main()
    return captured


def test_mode_defaults_to_auto_and_caps_to_preset(monkeypatch):
    """issue #152: без флагов режим — auto (по backlog'у), потолки — None
    (берутся из пресета режима внутри sweep, а не зашиты в CLI)."""
    captured = _capture(monkeypatch, [])
    assert captured["mode"] == "auto"
    assert captured["max_new_details"] is None
    assert captured["max_refresh"] is None
    assert captured["refresh_stale_days"] is None


def test_mode_and_caps_passed_through(monkeypatch):
    """Явные флаги доезжают до sweep как есть: ручной запуск режима и
    диагностический --max-new 0 (не None!) не должны теряться."""
    captured = _capture(
        monkeypatch,
        ["--mode", "drain", "--max-new", "0", "--max-refresh", "50",
         "--refresh-stale-days", "20"],
    )
    assert captured["mode"] == "drain"
    assert captured["max_new_details"] == 0
    assert captured["max_refresh"] == 50
    assert captured["refresh_stale_days"] == 20
