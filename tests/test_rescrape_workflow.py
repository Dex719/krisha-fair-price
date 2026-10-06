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
    # дефолт мягкого дедлайна — в bash-расширении `${INPUT_TIME_BUDGET_MIN:-320}`
    budget = int(re.search(r"\$\{INPUT_TIME_BUDGET_MIN:-(\d+)\}", rescrape_run).group(1))

    assert alerts.get("continue-on-error") is True
    assert 0 < alerts["timeout-minutes"] <= 20
    # мягкий дедлайн прохода + потолок алертов + заливки с запасом < потолка джобы
    assert budget + alerts["timeout-minutes"] + 5 < job["timeout-minutes"]


def test_alerts_download_models_before_pricing():
    """Веса в приватном релизе, в checkout их нет: без скачивания окно
    алертов не оценивается (01.10–03.10 рассылка шла пустой при зелёном ране)."""
    steps = _job()["steps"]
    alerts = steps[_index(steps, "Alerts")]
    run = alerts["run"]

    assert "krisha.db_release --models" in run
    assert run.index("krisha.db_release --models") < run.index("send_alerts.py")
    assert alerts["env"].get("KRISHA_DB_TOKEN"), "приватный релиз качается только с токеном"


def test_second_upload_only_after_successful_first_upload_and_alerts():
    steps = _job()["steps"]
    again_at = _index(steps, "Upload DB again")
    condition = steps[again_at]["if"]

    assert again_at > _index(steps, "Alerts")
    assert "steps.upload_db.outcome == 'success'" in condition
    assert "steps.alerts.outcome == 'success'" in condition


def test_dispatch_inputs_come_through_env_not_expressions_in_run():
    """Аудит безопасности 2026-10-06: inputs workflow_dispatch раскрывались
    GitHub'ом прямо в тексте `run:` — shell-инъекция. Теперь значения идут
    через env шага, а в bash — ссылки "$VAR" с дефолтами `${VAR:-…}`."""
    steps = _job()["steps"]
    step = steps[_index(steps, "Rescrape")]
    env, run = step["env"], step["run"]

    assert "github.event.inputs" not in run
    assert env["INPUT_PAGES"] == "${{ github.event.inputs.pages }}"
    assert env["INPUT_MAX_NEW"] == "${{ github.event.inputs.max_new }}"
    assert env["INPUT_TIME_BUDGET_MIN"] == "${{ github.event.inputs.time_budget_min }}"
    # дефолты прежние: pages 250, time budget 320 (по расписанию inputs пусты)
    assert '"${INPUT_PAGES:-250}"' in run
    assert '"${INPUT_TIME_BUDGET_MIN:-320}"' in run


def test_empty_delays_and_max_new_still_mean_use_the_mode_preset():
    """issue #190 §2.3: пустой input = «возьми пресет режима». Явные дефолты
    у пауз и потолка вернули бы прежний баг: drain не наступал ни разу."""
    steps = _job()["steps"]
    step = steps[_index(steps, "Rescrape")]
    env, run = step["env"], step["run"]

    # паузы: пустая строка в KRISHA_DELAY_MIN/MAX без `|| '…'`
    for name, field in (("KRISHA_DELAY_MIN", "delay_min"), ("KRISHA_DELAY_MAX", "delay_max")):
        assert env[name] == "${{ github.event.inputs." + field + " }}"
        assert "||" not in env[name]
    # потолок деталей: --max-new только при непустом значении, без дефолта
    assert not re.search(r"\$\{INPUT_MAX_NEW:-[^}]", run), "у max_new не должно быть дефолта"
    assert 'if [ -n "${INPUT_MAX_NEW:-}" ]' in run
