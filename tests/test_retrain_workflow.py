"""retrain.yml: команды обучения в шагах реально разбираются scripts/train.py.

03.10.2026 в команду ретрейна аренды попал буквальный «\n» (ошибка
экранирования при правке) — train.py упал бы на лишнем аргументе, и
воскресный ретрейн аренды молча не состоялся бы.
"""

import shlex
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "retrain.yml"


def _train_commands() -> list[str]:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    cmds = []
    for job in jobs.values():
        for step in job.get("steps", []):
            for line in str(step.get("run", "")).splitlines():
                if "scripts/train.py" in line:
                    cmds.append(line.strip())
    return cmds


def test_no_literal_escapes_in_run_scripts():
    text = WORKFLOW.read_text(encoding="utf-8")
    backslash_n = chr(92) + "n"  # буквальные «\» + «n», а не перевод строки
    assert backslash_n not in text


def test_train_commands_parse(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "train_cli", Path(__file__).resolve().parents[1] / "scripts" / "train.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    seen = []
    monkeypatch.setattr(mod, "train", lambda **kw: seen.append(kw) or {
        "model": {"mae": 1, "mape": 0.1, "mdape": 0.1, "r2": 0.9},
        "baseline": {"mae": 1, "mape": 0.2, "mdape": 0.2, "r2": 0.5},
        "n_train": 1, "n_test": 1,
    })
    cmds = _train_commands()
    assert len(cmds) >= 3
    for cmd in cmds:
        argv = shlex.split(cmd)[1:]  # без «python»
        monkeypatch.setattr("sys.argv", argv)
        try:
            mod.main()
        except SystemExit as exc:  # argparse на лишнем аргументе
            raise AssertionError(f"не разбирается: {cmd}") from exc
    assert any(kw.get("old_meta_path") == "/tmp/old_rent_meta.json" for kw in seen)


def test_rent_model_download_failure_is_not_first_model():
    """Сбой скачивания старой модели аренды не должен выглядеть как «первой
    модели нет»: иначе гейт подменяется проверкой «MAPE < 20%»."""
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["retrain-rent"]["steps"]
    run = next(s["run"] for s in steps if s.get("id") == "old")
    assert "set -euo pipefail" in run
    assert "gh release view model-latest" in run
    assert "--rent --models --require" in run


def test_rent_rescrape_does_not_start_empty_on_gh_errors():
    """`if gh … | grep` не отличал «ассета нет» от «gh упал»: сбой GitHub
    запускал проход с пустой базой, и она затирала db-latest."""
    rent = WORKFLOW.with_name("rescrape-rent.yml").read_text(encoding="utf-8")
    assert "assets=$(gh release view db-latest" in rent
    assert "| grep -qx 'krisha_rent.db.gz'" not in rent
