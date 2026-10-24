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
    # дефолт мягкого дедлайна — в bash-расширении `${INPUT_TIME_BUDGET_MIN:-300}`
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
    # дефолты: pages 250, time budget 300 (аудит 24.10: было 320) (по расписанию inputs пусты)
    assert '"${INPUT_PAGES:-250}"' in run
    assert '"${INPUT_TIME_BUDGET_MIN:-300}"' in run


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


# --------------------------------------------------------------------------- #
# Аудит 03.10 / 24.10
# --------------------------------------------------------------------------- #

WORKFLOWS = WORKFLOW.parent


def _wf(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _all_steps(name: str) -> list[dict]:
    return [s for job in _wf(name)["jobs"].values() for s in job.get("steps", [])]


def _named(steps: list[dict], prefix: str) -> dict:
    return steps[_index(steps, prefix)]


def test_no_workflow_uses_non_atomic_clobber_upload():
    """`gh release upload --clobber` удаляет ассет ДО заливки нового."""
    for path in WORKFLOWS.glob("*.yml"):
        for step in _all_steps(path.name):
            code = "\n".join(
                ln for ln in str(step.get("run", "")).splitlines() if not ln.lstrip().startswith("#")
            )
            assert "--clobber" not in code, f"{path.name}: {step.get('name')}"
            if "gh release upload" in code or "gh release create" in code:
                raise AssertionError(f"{path.name}: {step.get('name')} льёт мимо release_upload.py")


def test_db_and_model_uploads_go_through_release_upload():
    expected = {
        "rescrape.yml": ["Upload DB to release", "Upload DB again", "Save sweep state"],
        "rescrape-rent.yml": ["Upload rent DB to release", "Save sweep state"],
        "retrain.yml": ["Publish models release", "Publish rent model release"],
        "backfill-gap-cohort.yml": ["Upload DB to release"],
    }
    for wf, names in expected.items():
        steps = _all_steps(wf)
        for name in names:
            assert "scripts/release_upload.py" in _named(steps, name)["run"], f"{wf}: {name}"
    # model-latest и дневной снапшот создаются при отсутствии — без `|| create`
    for name in ("Publish models release", "Publish rent model release"):
        run = _named(_all_steps("retrain.yml"), name)["run"]
        assert run.count("--create-title") == 2


def test_restart_space_failure_does_not_skip_second_upload():
    steps = _job()["steps"]
    restart = _named(steps, "Restart Space")
    again = _named(steps, "Upload DB again")
    assert restart.get("continue-on-error") is True
    assert again["if"].startswith("always() && ")
    assert 0 < again["timeout-minutes"] <= 15


def test_time_budget_leaves_room_for_uploads():
    """Аудит 24.10: «≈340» при timeout 350 не учитывало установку и вторую
    заливку. Мягкий дедлайн 300 + все потолки шагов после прохода < 350."""
    job = _job()
    steps = job["steps"]
    run = _named(steps, "Rescrape")["run"]
    budget = int(re.search(r"\$\{INPUT_TIME_BUDGET_MIN:-(\d+)\}", run).group(1))
    assert budget == 300
    assert _named(steps, "Rescrape")["timeout-minutes"] < job["timeout-minutes"]
    after = sum(
        s.get("timeout-minutes", 0)
        for s in steps[_index(steps, "Rescrape") + 1:]
        if s.get("timeout-minutes")
    )
    assert budget + _named(steps, "Alerts")["timeout-minutes"] + _named(
        steps, "Upload DB again"
    )["timeout-minutes"] + 10 < job["timeout-minutes"]
    assert after > 0


def test_sweep_state_is_saved_always_and_restored_before_the_pass():
    for wf, job_name, deal in (
        ("rescrape.yml", "rescrape", "prodazha"),
        ("rescrape-rent.yml", "rescrape-rent", "arenda"),
    ):
        steps = _wf(wf)["jobs"][job_name]["steps"]
        restore = _index(steps, "Restore sweep state")
        rescrape = _index(steps, "Rescrape")
        save = _index(steps, "Save sweep state")
        assert _index(steps, "Download") < restore < rescrape < save
        assert f"sweep_state_file pull --deal {deal}" in steps[restore]["run"]
        assert steps[restore].get("continue-on-error") is True
        assert steps[save]["if"].startswith("always()")
        assert f"sweep_state_{deal}.json" in steps[save]["run"]
        # сохранение — до заливки базы: заливка может упасть по таймауту
        assert save < _index(steps, "Upload")


def test_rent_has_report_freshness_check_and_space_restart():
    steps = _wf("rescrape-rent.yml")["jobs"]["rescrape-rent"]["steps"]
    run = _named(steps, "Rescrape rent")["run"]
    assert "--fail-below" in run
    report = _named(steps, "Daily report")
    assert "krisha.daily_report --scope rent" in report["run"]
    assert report["if"] == "always()" and report.get("continue-on-error") is True
    assert report["env"]["TG_ADMIN_CHAT_ID"] == "${{ secrets.TG_ADMIN_CHAT_ID }}"
    restart = _named(steps, "Restart Space")
    assert restart.get("continue-on-error") is True
    assert "spaces/Dex719/krisha-fair-price/restart" in restart["run"]
    assert _index(steps, "Upload rent DB") < _index(steps, "Restart Space")


def test_daily_report_supports_rent_scope():
    from krisha import daily_report

    assert "rent" in daily_report._SCOPES
    assert daily_report._SCOPES["rent"][2].as_posix() == "/tmp/rescrape_rent_stats.json"


def test_rent_download_missing_db_is_an_error_without_bootstrap():
    wf = _wf("rescrape-rent.yml")
    on = wf["on"] if "on" in wf else wf[True]
    assert on["workflow_dispatch"]["inputs"]["bootstrap"]["default"] is False
    step = _named(wf["jobs"]["rescrape-rent"]["steps"], "Download rent DB")
    assert step["env"]["INPUT_BOOTSTRAP"] == "${{ github.event.inputs.bootstrap }}"
    assert "--rent --require --force --strict-checksum" in step["run"]
    assert "начинаем с нуля" in step["run"]


def test_backfill_concurrency_matches_the_target_db():
    wf = _wf("backfill-gap-cohort.yml")
    group = wf["concurrency"]["group"]
    assert group == "${{ inputs.deal == 'arenda' && 'krisha-rent-db-writes' || 'krisha-db-writes' }}"
    # имена групп — те же, что у писателей баз
    assert _wf("rescrape.yml")["concurrency"]["group"] == "krisha-db-writes"
    assert _wf("retrain.yml")["concurrency"]["group"] == "krisha-db-writes"
    assert _wf("rescrape-rent.yml")["concurrency"]["group"] == "krisha-rent-db-writes"


def test_keepalive_restores_health_state_after_checkout():
    steps = _wf("space-keepalive.yml")["jobs"]["keepalive"]["steps"]
    uses = [str(s.get("uses", "")) for s in steps]
    checkout = next(i for i, u in enumerate(uses) if u.startswith("actions/checkout@"))
    restore = next(i for i, u in enumerate(uses) if u.startswith("actions/cache/restore@"))
    save = next(i for i, u in enumerate(uses) if u.startswith("actions/cache/save@"))
    check = _index(steps, "Health check")
    assert checkout < restore < check < save
