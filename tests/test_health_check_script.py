"""scripts/health_check.py: устаревшие данные — это деградация, а не «up»."""

import importlib.util
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("health_check", ROOT / "scripts" / "health_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _respond(monkeypatch, hc, payload):
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(hc.urllib.request, "urlopen",
                        lambda url, timeout=None: Resp(json.dumps(payload).encode()))


def test_stale_data_is_degraded(monkeypatch):
    hc = _load()
    healthy = {"status": "ok", "model_loaded": True, "tg_webhook": "ok", "freshness": "ok"}
    _respond(monkeypatch, hc, healthy)
    assert hc.probe("http://x/api/health") == ("up", "")

    _respond(monkeypatch, hc, {**healthy, "freshness": "stale"})
    status, detail = hc.probe("http://x/api/health")
    assert status == "degraded" and "freshness='stale'" in detail
