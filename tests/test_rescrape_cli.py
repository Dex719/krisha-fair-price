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
