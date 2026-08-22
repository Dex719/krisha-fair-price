"""Live-smoke: реальная база, реальная модель, один живой поход на krisha.kz.

НЕ гоняется по умолчанию и в CI (см. addopts в pyproject.toml) — только
вручную: ``pytest -m live``. Требует data/krisha.db (``python -m krisha.db_release``)
и доступ в интернет. Вежливость: ровно один /api/predict за прогон.
"""

import httpx
import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.live


def test_live_health_is_fresh(live_server):
    r = httpx.get(live_server + "/api/health", timeout=30.0)
    assert r.status_code == 200
    body = r.json()
    assert body["model_loaded"] is True
    assert body["freshness"] == "ok", f"база протухла: age={body['data_age_hours']}ч"
    assert body["model_error_pct"] is not None


def test_live_stats_and_heatmap_nonempty(live_server):
    stats = httpx.get(live_server + "/api/stats", timeout=60.0).json()
    assert stats["total_listings"] > 1000
    assert len(stats["by_district"]) == 8
    heatmap = httpx.get(live_server + "/api/heatmap", timeout=60.0).json()
    assert len(heatmap) > 100


def test_live_demo_predict_full_flow_in_browser(page, live_server):
    """Кнопка «Показать на примере»: демо-лот из базы → живой predict → отчёт."""
    page.set_default_timeout(60_000)
    page.goto(live_server + "/")

    demo = page.locator("#demo-link")
    expect(demo).to_be_visible()
    demo.click()

    # Живой поход на krisha.kz + инференс: ждём либо отчёт, либо честную ошибку.
    receipt = page.locator("#receipt")
    verdict = receipt.locator(".scale-l .cur")
    status = page.locator("#status")
    page.wait_for_function(
        """() => {
            const cur = document.querySelector('#receipt .scale-l .cur');
            const st = document.getElementById('status');
            return (cur && cur.textContent.trim()) ||
                   (st && st.classList.contains('err') && st.textContent.trim());
        }""",
        timeout=60_000,
    )

    if status.get_attribute("class") and "err" in (status.get_attribute("class") or ""):
        pytest.skip(f"живой predict не прошёл (сеть/бан?): {status.text_content()}")

    assert verdict.text_content().strip() in ("занижено", "справедливо", "завышено", "без цены")
    expect(receipt.locator(".factors .fr").first).to_be_visible()
