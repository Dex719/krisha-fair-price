"""Порядок шагов ночного прохода (.kiro/specs/rescrape-post-steps, AC-1.1).

2026-09-20 шаг Alerts без своего потолка съел бюджет джобы, и заливка базы,
стоявшая ПОСЛЕ него, не выполнилась: 4 ч 43 мин сбора пропали, прод двое суток
отвечал на старых данных. Критический путь обязан идти раньше вторичного.
"""

import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "rescrape.yml"


def _job() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["rescrape"]


def _index(steps: list[dict], prefix: str) -> int:
    return next(i for i, s in enumerate(steps) if str(s.get("name", "")).startswith(prefix))


def test_db_reaches_release_and_space_before_alerts():
    steps = _job()["steps"]
    alerts = _index(steps, "Alerts")
    for critical in ("Upload DB to release", "Restart Space", "Snapshot release"):
        assert _index(steps, critical) < alerts, f"«{critical}» должен идти до Alerts"


def test_alerts_has_its_own_ceiling_within_the_job_budget():
    job = _job()
    steps = job["steps"]
    alerts = steps[_index(steps, "Alerts")]
    rescrape_run = steps[_index(steps, "Rescrape")]["run"]
    budget = int(re.search(r"time_budget_min \|\| '(\d+)'", rescrape_run).group(1))

    assert alerts.get("continue-on-error") is True
    assert 0 < alerts["timeout-minutes"] <= 20
    # мягкий дедлайн прохода + потолок алертов + заливки с запасом < потолка джобы
    assert budget + alerts["timeout-minutes"] + 5 < job["timeout-minutes"]


def test_second_upload_only_after_successful_first_upload_and_alerts():
    steps = _job()["steps"]
    again_at = _index(steps, "Upload DB again")
    condition = steps[again_at]["if"]

    assert again_at > _index(steps, "Alerts")
    assert "steps.upload_db.outcome == 'success'" in condition
    assert "steps.alerts.outcome == 'success'" in condition
