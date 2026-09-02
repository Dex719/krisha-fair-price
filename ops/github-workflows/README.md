# Правки воркфлоу, ожидающие переноса в `.github/workflows/`

Токен GitHub App, которым работает Viktor, не имеет права `workflows` — пуш в
`.github/workflows/*` отклоняется. Поэтому изменённые файлы лежат здесь;
перенести их одной командой из корня репо (после мержа PR):

```bash
cp ops/github-workflows/{ci,rescrape,retrain}.yml .github/workflows/ && git rm -r ops/github-workflows && git commit -am "ci: применить правки воркфлоу из #190" && git push
```

Что меняется (issue #190):

| Файл | Правка | Зачем |
|---|---|---|
| `rescrape.yml` | `--max-new` и паузы больше не подставляются принудительно (`1000`, `2.5–5.0`); пустой input = пресет режима | Режим drain (#152: 4500 деталей, паузы 1.5–3.0) существовал только в коде — воркфлоу перекрывал его каждый день. Очередь 23–30k без деталей и 49% активных лотов без деталей — следствие |
| `retrain.yml` | шаг `sync_readme_metrics.py` перед коммитом, `README.md` в `git add` | README отставал от меты; тест теперь гоняет `--check` |
| `ci.yml` | `paths-ignore` для state-файлов рантайма; `pip-audit` без `continue-on-error` | CI не гоняется на data-коммиты; уязвимости не висят жёлтыми |

После переноса первый ночной проход rescrape пойдёт в drain: ожидаемо
~3–4 тыс. деталей за проход вместо ~1000 (бюджет 320 мин, `fit_detail_caps`
урежет сам). Смотреть `sweep_runs.max_new_effective` и `detail_queue_after`.
