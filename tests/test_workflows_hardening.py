"""Харденинг GitHub Actions (аудит безопасности 2026-10-06).

Четыре находки, по каждой — свои проверки:

1. `uses:` закреплены на мажорные теги (@v7), а тег можно переписать
   (инцидент tj-actions/changed-files) — теперь только полный SHA коммита.
2. Единственный аудит зависимостей жил в ci.yml и бежал лишь на push/PR;
   Dependabot не читает requirements*.lock — нужен ночной audit.yml.
3. inputs workflow_dispatch подставлялись выражениями прямо в `run:` — GitHub
   раскрывает их ДО shell, значение становится кодом (shell-инъекция).
4. deploy-hf.yml клал токен в URL `git push` и добавлял в снапшот ВЕСЬ чекаут
   через `git add -A`, хотя Space — публичное зеркало.

Статические проверки разбирают YAML; поведенческие гоняют настоящие bash-
фрагменты шагов с подставным `python` (печатает свои аргументы) и вредоносными
значениями inputs. Без рабочего bash они скипаются.
"""

import functools
import os
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

LOCKS = ("requirements.lock", "requirements-runtime.lock", "requirements-train.lock")


# --------------------------------------------------------------------------- #
# Общие помощники
# --------------------------------------------------------------------------- #


def _workflow_paths() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml"))


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _on(workflow: dict) -> dict:
    # PyYAML (YAML 1.1) читает ключ `on` как булев True
    return workflow["on"] if "on" in workflow else workflow[True]


def _steps(workflow: dict):
    for job in workflow["jobs"].values():
        yield from job.get("steps", [])


def _step(name: str, workflow: str) -> dict:
    return next(s for s in _steps(_load(workflow)) if s.get("name") == name)


def _code(run: str) -> str:
    """Скрипт шага без строк-комментариев: в них объяснено, ЧТО раньше было не так."""
    return "\n".join(ln for ln in run.splitlines() if not ln.lstrip().startswith("#"))


# --------------------------------------------------------------------------- #
# 1. Пины на SHA
# --------------------------------------------------------------------------- #

USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>\S+)(?P<tail>[^\n]*)$", re.M)
PINNED_REF = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}$")


def test_every_action_is_pinned_to_a_full_commit_sha_with_tag_comment():
    scanned = 0
    yaml_uses = 0
    for path in _workflow_paths():
        text = path.read_text(encoding="utf-8")
        for match in USES_LINE.finditer(text):
            scanned += 1
            ref = match["ref"]
            assert PINNED_REF.match(ref), f"{path.name}: «{ref}» не закреплён на 40-hex SHA"
            assert re.match(r"\s*#\s*v\d", match["tail"]), (
                f"{path.name}: у «{ref}» нет комментария с тегом (`# v7`) — "
                "без него Dependabot и человек не поймут, что за версия"
            )
        yaml_uses += sum("uses" in s for s in _steps(yaml.safe_load(text)))
    assert scanned >= 20, "разбор нашёл подозрительно мало uses — сломалась регулярка?"
    assert scanned == yaml_uses, "регулярка и YAML видят разное число uses"


def test_same_action_tag_is_pinned_to_the_same_sha_everywhere():
    """cache/restore и cache/save — один репозиторий actions/cache: SHA у них
    общий. Разъезд пинов между файлами означал бы частично забытое обновление."""
    shas: dict[tuple[str, str], set[str]] = defaultdict(set)
    for path in _workflow_paths():
        for match in USES_LINE.finditer(path.read_text(encoding="utf-8")):
            action, sha = match["ref"].split("@")
            repo = "/".join(action.split("/")[:2])
            tag = match["tail"].strip().lstrip("#").strip()
            shas[(repo, tag)].add(sha)
    assert shas
    for key, found in shas.items():
        assert len(found) == 1, f"{key}: разные SHA в разных воркфлоу: {sorted(found)}"


def test_dependabot_keeps_updating_pinned_actions():
    """Пин на SHA не должен протухнуть: обновляет его Dependabot."""
    cfg = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    assert "github-actions" in {u["package-ecosystem"] for u in cfg["updates"]}


# --------------------------------------------------------------------------- #
# 2. Ночной аудит зависимостей
# --------------------------------------------------------------------------- #


def _audit_run() -> str:
    return _step("Audit locks (pip-audit)", "audit.yml")["run"]


def test_audit_workflow_is_scheduled_daily_and_manual_not_on_push():
    workflow = _load("audit.yml")
    triggers = _on(workflow)

    crons = [item["cron"] for item in triggers["schedule"]]
    assert len(crons) == 1
    minute, hour, day_of_month, month, day_of_week = crons[0].split()
    assert day_of_month == month == day_of_week == "*", "аудит должен бежать каждый день"
    assert minute.isdigit() and hour.isdigit()
    assert "workflow_dispatch" in triggers
    # push/PR уже покрывает ci.yml; смысл этого воркфлоу — проверка БЕЗ коммитов
    assert "push" not in triggers
    assert "pull_request" not in triggers
    assert workflow["permissions"] == {"contents": "read"}


def test_audit_checks_every_lock_file_as_is_without_resolving_dependencies():
    run = _audit_run()
    audit_lines = [ln for ln in _code(run).splitlines() if ln.strip().startswith("pip-audit ")]
    looped = re.search(r"for lock in ([^;]+); do", run).group(1).split()

    assert audit_lines, "нет вызова pip-audit"
    assert sorted(looped) == sorted(LOCKS), "аудит обязан покрывать все три lock-файла"
    assert all((ROOT / lock).is_file() for lock in LOCKS)
    assert '-r "$lock"' in audit_lines[0]
    # аудитим lock, а не окружение раннера: зависимости уже перечислены
    for line in audit_lines:
        assert "--no-deps" in line
    # красный первый lock не должен скрывать остальные
    assert "|| status=1" in run
    assert 'exit "$status"' in run


def test_audit_uses_python_311_and_installs_pip_audit_before_running_it():
    steps = list(_steps(_load("audit.yml")))
    setup = next(s for s in steps if str(s.get("uses", "")).startswith("actions/setup-python@"))
    names = [s.get("name") for s in steps]

    assert setup["with"]["python-version"] == "3.11"
    install = next(s for s in steps if s.get("name") == "Install pip-audit")
    assert "pip install -q pip-audit" in install["run"]
    assert names.index("Install pip-audit") < names.index("Audit locks (pip-audit)")


def test_audit_notifies_telegram_on_failure_and_skips_without_secrets():
    steps = list(_steps(_load("audit.yml")))
    notify = next(s for s in steps if s.get("name") == "Notify Telegram on failure")

    assert notify["if"] == "failure()"
    assert notify["env"]["TELEGRAM_BOT_TOKEN"] == "${{ secrets.TELEGRAM_BOT_TOKEN }}"
    assert notify["env"]["TG_ADMIN_CHAT_ID"] == "${{ secrets.TG_ADMIN_CHAT_ID }}"
    assert '[ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TG_ADMIN_CHAT_ID" ]' in notify["run"]
    assert "exit 0" in notify["run"]
    assert "api.telegram.org" in notify["run"]
    assert steps[-1] is notify, "уведомление — последний шаг, иначе failure() не увидит аудит"


def _ignored_vulns(workflow: str) -> set[str]:
    runs = "\n".join(str(s.get("run", "")) for s in _steps(_load(workflow)))
    return set(re.findall(r"--ignore-vuln[ =]([\w-]+)", runs))


def test_ci_and_nightly_audit_ignore_the_same_vulnerabilities():
    """Исключение, добавленное только в ci.yml, оставило бы ночной аудит
    красным (и наоборот) — списки обязаны меняться вместе."""
    assert _ignored_vulns("ci.yml") == _ignored_vulns("audit.yml")


# --------------------------------------------------------------------------- #
# 3. inputs — через env, а не выражениями в run:
# --------------------------------------------------------------------------- #

EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.S)
UNTRUSTED = re.compile(r"\b(?:github\.event\.|inputs\.|github\.head_ref)")


def _untrusted_expressions(run: str) -> list[str]:
    return [e.strip() for e in EXPRESSION.findall(run) if UNTRUSTED.search(e)]


def test_run_scripts_never_interpolate_event_data():
    offenders = []
    for path in _workflow_paths():
        for step in _steps(yaml.safe_load(path.read_text(encoding="utf-8"))):
            for expr in _untrusted_expressions(str(step.get("run", ""))):
                offenders.append(f"{path.name} / {step.get('name') or step.get('id')}: {expr}")
    assert not offenders, "данные события внутри run: (shell-инъекция):\n" + "\n".join(offenders)


def test_untrusted_expression_detector_is_not_vacuous():
    assert _untrusted_expressions("""echo "${{ github.event.inputs.pages || '250' }}" """)
    assert _untrusted_expressions("""echo ${{ inputs.deal }}""")
    assert _untrusted_expressions("""git checkout ${{ github.head_ref }}""")
    assert _untrusted_expressions("""x=${{
        github.event.inputs.max_new && format('--max-new {0}', github.event.inputs.max_new) || ''
    }}""")
    # выходы шагов и секреты в run — не часть этой находки
    assert not _untrusted_expressions("""test "${{ steps.old.outputs.has_old }}" = 1""")


@functools.cache
def _bash() -> str | None:
    """Рабочий bash с полноценной средой. На Windows `bash` из PATH бывает
    заглушкой WSL (WindowsApps) — она не получает env и пути хоста, поэтому
    сначала пробуем Git Bash, а выбор подтверждаем пробой."""
    candidates: list[str | None] = []
    if os.name == "nt":
        for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            base = os.environ.get(var)
            if base:
                candidates += [
                    str(Path(base) / "Git" / "bin" / "bash.exe"),
                    str(Path(base) / "Git" / "usr" / "bin" / "bash.exe"),
                ]
    candidates.append(shutil.which("bash"))
    probe = 'printf %s "$HARDENING_PROBE"; command -v tee touch >/dev/null'
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            done = subprocess.run(
                [candidate, "-c", probe],
                env={**os.environ, "HARDENING_PROBE": "ok"},
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0 and done.stdout == "ok":
            return candidate
    return None


needs_bash = pytest.mark.skipif(_bash() is None, reason="нет рабочего bash")

# вместо настоящих скриптов — печать их аргументов по одному в строке «<арг>»
PRINT_ARGS = r"printf '<%s>\n'"


def _run_step(workflow: str, step_name: str, tmp_path: Path, env: dict[str, str],
              replace: dict[str, str] | None = None, prelude: str = ""):
    """Выполняет `run:` шага как это делает раннер (`bash -e файл`) с заданным env.

    prelude — shell-функции-заглушки перед скриптом (функция бьёт одноимённую
    программу из PATH, а порядок PATH под Git Bash на Windows не наш).
    """
    script = _step(step_name, workflow)["run"]
    for old, new in (replace or {}).items():
        assert old in script, f"в шаге «{step_name}» нет {old!r}"
        script = script.replace(old, new)
    script_file = tmp_path / "step.sh"
    script_file.write_text(prelude + script, encoding="utf-8", newline="\n")

    full_env = {k: v for k, v in os.environ.items() if not k.startswith(("INPUT_", "KRISHA_"))}
    full_env.update(env)
    return subprocess.run(
        [_bash(), "-e", script_file.as_posix()],
        env=full_env, cwd=tmp_path, capture_output=True,
        encoding="utf-8", errors="replace", timeout=60,
    )


def _printed_args(stdout: str) -> list[str]:
    return [ln[1:-1] for ln in stdout.splitlines() if ln.startswith("<") and ln.endswith(">")]


# вредоносные значения: каждое при подстановке в ТЕКСТ скрипта выполнило бы touch
PAYLOADS = (
    '1"; touch PWNED_QUOTE; echo "',
    "$(touch PWNED_SUBSHELL)",
    "`touch PWNED_BACKTICK`",
    "5 --fail-empty; touch PWNED_FLAG #",
)


def _assert_nothing_executed(tmp_path: Path):
    pwned = sorted(p.name for p in tmp_path.glob("PWNED*"))
    assert not pwned, f"инъекция сработала: {pwned}"


RESCRAPE_TAIL = ["--fail-empty", "--fail-below", "8000", "--summary-json", "/tmp/rescrape_stats.json"]


def _rescrape_args(tmp_path: Path, **env: str) -> list[str]:
    done = _run_step(
        "rescrape.yml", "Rescrape", tmp_path,
        {f"INPUT_{k.upper()}": v for k, v in env.items()},
        replace={"python scripts/rescrape.py": PRINT_ARGS},
    )
    assert done.returncode == 0, done.stderr
    return _printed_args(done.stdout)


@needs_bash
@pytest.mark.parametrize("env", [
    {},  # расписание: inputs нет вовсе
    {"pages": "", "max_new": "", "time_budget_min": ""},  # dispatch с очищенными полями
])
def test_rescrape_defaults_and_empty_max_new_keeps_the_mode_preset(tmp_path, env):
    args = _rescrape_args(tmp_path, **env)

    assert args == ["--pages", "250", "--time-budget-min", "320", *RESCRAPE_TAIL]
    assert "--max-new" not in args, "пустой max_new = пресет режима, флаг передавать нельзя"


@needs_bash
def test_rescrape_explicit_inputs_are_passed_through(tmp_path):
    args = _rescrape_args(tmp_path, pages="100", max_new="1500", time_budget_min="200")

    assert args == [
        "--pages", "100", "--max-new", "1500", "--time-budget-min", "200", *RESCRAPE_TAIL,
    ]


@needs_bash
@pytest.mark.parametrize("field", ["pages", "max_new", "time_budget_min"])
def test_rescrape_input_cannot_inject_shell_code(tmp_path, field):
    for payload in PAYLOADS:
        args = _rescrape_args(tmp_path, **{field: payload})
        assert payload in args, "значение должно дойти до скрипта ОДНИМ аргументом, как данные"
        _assert_nothing_executed(tmp_path)


def _rent_args(tmp_path: Path, **env: str) -> list[str]:
    done = _run_step(
        "rescrape-rent.yml", "Rescrape rent", tmp_path,
        {f"INPUT_{k.upper()}": v for k, v in env.items()},
        replace={"python scripts/rescrape.py": PRINT_ARGS},
    )
    assert done.returncode == 0, done.stderr
    return _printed_args(done.stdout)


RENT_TAIL = ["--fail-empty", "--summary-json", "/tmp/rescrape_rent_stats.json"]


@needs_bash
@pytest.mark.parametrize("env", [{}, {"pages": "", "max_new": ""}])
def test_rent_rescrape_keeps_its_defaults(tmp_path, env):
    assert _rent_args(tmp_path, **env) == [
        "--deal", "arenda", "--pages", "250", "--max-new", "1000", *RENT_TAIL,
    ]


@needs_bash
def test_rent_rescrape_explicit_inputs_and_injection(tmp_path):
    assert _rent_args(tmp_path, pages="40", max_new="300") == [
        "--deal", "arenda", "--pages", "40", "--max-new", "300", *RENT_TAIL,
    ]
    for field in ("pages", "max_new"):
        for payload in PAYLOADS:
            assert payload in _rent_args(tmp_path, **{field: payload})
            _assert_nothing_executed(tmp_path)


def _backfill_run(tmp_path: Path, **env: str):
    log = (tmp_path / "backfill.log").as_posix()
    summary = tmp_path / "summary.md"
    base = {
        "DB": "data/krisha_rent.db",
        "GITHUB_STEP_SUMMARY": summary.as_posix(),
        "INPUT_WINDOW_START": "2026-07-26 16:04:00",
        "INPUT_WINDOW_END": "2026-07-26 18:01:00",
        "INPUT_EXPECT": "5031",
    }
    base.update({f"INPUT_{k.upper()}": v for k, v in env.items()})
    done = _run_step(
        "backfill-gap-cohort.yml", "Backfill", tmp_path, base,
        replace={"python scripts/backfill_gap_cohort.py": PRINT_ARGS, "/tmp/backfill.log": log},
    )
    assert done.returncode == 0, done.stderr
    return _printed_args(done.stdout)


BACKFILL_HEAD = [
    "--db", "data/krisha_rent.db",
    "--window-start", "2026-07-26 16:04:00",
    "--window-end", "2026-07-26 18:01:00",
    "--expect", "5031",
]


@needs_bash
@pytest.mark.parametrize("apply", [None, "", "false", "False", "1", "yes"])
def test_backfill_is_a_dry_run_unless_apply_is_exactly_true(tmp_path, apply):
    env = {} if apply is None else {"apply": apply}
    assert _backfill_run(tmp_path, **env) == [*BACKFILL_HEAD, "--dry-run"]


@needs_bash
def test_backfill_apply_true_writes_without_dry_run_flag(tmp_path):
    assert _backfill_run(tmp_path, apply="true") == BACKFILL_HEAD


@needs_bash
def test_backfill_inputs_cannot_inject_shell_code(tmp_path):
    for field in ("window_start", "window_end", "expect"):
        for payload in PAYLOADS:
            args = _backfill_run(tmp_path, **{field: payload})
            assert payload in args
            _assert_nothing_executed(tmp_path)


# gh / sha256sum / gunzip-заглушки: шаг скачивания выбирает ассет, а не качает
FAKE_DOWNLOAD_TOOLS = "gh() { :; }\nsha256sum() { cat > /dev/null; }\ngunzip() { :; }\n"


@needs_bash
@pytest.mark.parametrize("deal, asset", [
    ("arenda", "krisha_rent.db.gz"),
    ("prodazha", "krisha.db.gz"),
])
def test_backfill_download_picks_the_asset_by_deal(tmp_path, deal, asset):
    github_env = tmp_path / "github_env"
    done = _run_step(
        "backfill-gap-cohort.yml", "Download DB from release", tmp_path,
        {"INPUT_DEAL": deal, "GITHUB_ENV": github_env.as_posix(), "KRISHA_DATA_REPO": "x/y"},
        prelude=FAKE_DOWNLOAD_TOOLS,
    )
    assert done.returncode == 0, done.stderr
    assert f"ASSET={asset}" in github_env.read_text(encoding="utf-8")


@needs_bash
def test_backfill_deal_cannot_inject_shell_code(tmp_path):
    github_env = tmp_path / "github_env"
    for payload in PAYLOADS:
        done = _run_step(
            "backfill-gap-cohort.yml", "Download DB from release", tmp_path,
            {"INPUT_DEAL": payload, "GITHUB_ENV": github_env.as_posix(),
             "KRISHA_DATA_REPO": "x/y"},
            prelude=FAKE_DOWNLOAD_TOOLS,
        )
        assert done.returncode == 0, done.stderr
        _assert_nothing_executed(tmp_path)
    # значение, не равное «arenda», — это продажа: безопасный выбор по умолчанию
    assert "ASSET=krisha.db.gz" in github_env.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 4. deploy-hf.yml: токен вне URL и argv, снапшот — по явному списку
# --------------------------------------------------------------------------- #


def _deploy_run() -> str:
    return _step("Push snapshot to Space", "deploy-hf.yml")["run"]


def _snapshot_add_paths(run: str) -> list[str]:
    """Пути из `git add …` (без -f для build_revision), с учётом переносов строк."""
    joined = re.sub(r"\\\n\s*", " ", _code(run))
    paths: list[str] = []
    for line in joined.splitlines():
        tokens = line.split()
        if tokens[:2] == ["git", "add"] and tokens[2:3] != ["-f"]:
            paths += tokens[2:]
    return paths


def _dockerfile_copy_sources() -> set[str]:
    sources: set[str] = set()
    for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
        if line.startswith("COPY "):
            parts = [p for p in line.split()[1:] if not p.startswith("--")]
            sources.update(p.rstrip("/") for p in parts[:-1])
    return sources


def test_deploy_token_is_not_in_the_push_url():
    code = _code(_deploy_run())

    assert "@huggingface.co" not in code
    assert not re.search(r"https?://[^\s'\"]*(?:HF_TOKEN|:\$)", code), "токен внутри URL"
    push = next(ln for ln in code.splitlines() if "push" in ln and "--force" in ln)
    assert "credential.helper" in code
    assert "https://huggingface.co/spaces/Dex719/krisha-fair-price" in push
    # токен читает helper из окружения шага, в argv лежит только литерал `$HF_TOKEN`
    assert _step("Push snapshot to Space", "deploy-hf.yml")["env"] == {
        "HF_TOKEN": "${{ secrets.HF_TOKEN }}",
    }


def test_deploy_snapshot_is_an_explicit_allowlist_not_the_whole_checkout():
    run = _deploy_run()
    code = _code(run)
    added = _snapshot_add_paths(run)

    assert not re.search(r"git add\s+(?:-A|--all|-u|\.|\*)(?:\s|$)", code), "снапшот «всё подряд»"
    assert added, "не нашли явный `git add <список>`"
    # всё, что копирует Dockerfile, обязано быть в снапшоте, иначе образ не соберётся
    missing = _dockerfile_copy_sources() - set(added)
    assert not missing, f"Dockerfile копирует то, чего нет в снапшоте: {sorted(missing)}"
    assert {"Dockerfile", ".dockerignore", ".gitattributes"} <= set(added)
    # публичное зеркало: ничего лишнего из чекаута
    private = {"tests", ".kiro", "docs", "scripts", "infra", "reports", "assets", "notebooks",
               ".github", ".env", ".env.example", "Makefile"}
    assert not private & set(added)
    # ревизию для /api/health кладём принудительно: она в .gitignore
    assert "git add -f data/build_revision.txt" in code


def test_deploy_snapshot_order_lfs_then_add_then_commit_then_push():
    code = _code(_deploy_run())
    order = [code.index(marker) for marker in
             ("git lfs track", "git add Dockerfile", "git add -f", "commit -m", "push --force")]
    assert order == sorted(order)


@pytest.mark.skipif(shutil.which("git") is None, reason="нет git")
def test_deploy_credential_helper_answers_with_the_token_from_env(tmp_path):
    """Helper из шага деплоя, выполненный настоящим `git credential fill`."""
    code = _code(_deploy_run())
    helper = re.search(r"-c credential\.helper='([^']+)'", code).group(1)
    token = "hf_unit_test_token_123"
    assert token not in code

    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    empty_config = tmp_path / "empty.gitconfig"
    empty_config.write_text("", encoding="utf-8")
    env.update(
        HF_TOKEN=token,
        GIT_CONFIG_NOSYSTEM="1",  # без системных helper'ов (Git Credential Manager и т.п.)
        GIT_CONFIG_GLOBAL=str(empty_config),
        GIT_TERMINAL_PROMPT="0",
    )
    done = subprocess.run(
        ["git", "-c", f"credential.helper={helper}", "credential", "fill"],
        input="protocol=https\nhost=huggingface.co\n\n", env=env, cwd=tmp_path,
        capture_output=True, encoding="utf-8", errors="replace", timeout=60,
    )

    assert done.returncode == 0, done.stderr
    answer = dict(ln.split("=", 1) for ln in done.stdout.splitlines() if "=" in ln)
    assert answer["username"] == "Dex719"
    assert answer["password"] == token
