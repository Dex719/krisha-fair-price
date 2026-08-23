# Обновлённые воркфлоу — применить вручную

GitHub App, из-под которого пришёл этот PR, не имеет права `workflows`, поэтому
файлы в `.github/workflows/` через него не пушатся. Здесь лежат ГОТОВЫЕ версии
изменённых воркфлоу — после мержа PR перенести их на место одной командой:

```bash
git checkout main && git pull
git mv ops/github-workflows/deploy-hf.yml .github/workflows/deploy-hf.yml
git mv ops/github-workflows/smoke.yml     .github/workflows/smoke.yml
git rm ops/github-workflows/README.md
git commit -m "ci: гейт деплоя по CI + смоук по выкаченной ревизии"
git push
```

Что меняется:

- **deploy-hf.yml** — новый job `gate`: деплой ждёт прогон CI по тому же
  коммиту и падает, если тот не зелёный (`workflow_dispatch` гейт пропускает).
  Плюс перед пушем снапшота пишется `data/build_revision.txt` с `$GITHUB_SHA` —
  его отдаёт `/api/health.revision` (код уже в этом PR).
- **smoke.yml** — ожидание ревизии теперь спрашивает сам прод
  (`/api/health.revision`) вместо метаданных HF (там у Space всегда свой sha,
  совпадения не бывало никогда) и **падает**, если за 15 минут выкаченная
  ревизия не поднялась, вместо `::warning` и прогона по старому контейнеру.

Если выдать приложению право `workflows` (Settings → Applications → Viktor →
Configure), эта папка больше не понадобится.
