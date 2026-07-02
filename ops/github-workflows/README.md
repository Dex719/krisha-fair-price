# GitHub Actions workflows (перенести вручную)

У бота Viktor нет права `workflows`, поэтому файлы лежат здесь, а не в
`.github/workflows/`. После мержа перенеси их одной командой:

```bash
git mv ops/github-workflows/rescrape.yml ops/github-workflows/retrain.yml .github/workflows/
git commit -m "ci: enable rescrape + retrain workflows"
git push
```

- `rescrape.yml` — ежедневный рескрейп (история цен, дни на рынке, снятые
  объявления, новые объявления), коммитит `data/krisha.db` в main.
- `retrain.yml` — еженедельное переобучение с метрическим гейтом
  (`scripts/model_gate.py`): новая модель коммитится только если не хуже.

⚠️ Первый запуск rescrape проверь вручную (Actions → Daily rescrape →
Run workflow): krisha.kz может отдавать 403 с IP GitHub Actions —
тогда job упадёт красным благодаря `--fail-empty`.
