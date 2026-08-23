# space-keepalive.yml — применить вручную

GitHub App без права `workflows` не может пушить `.github/workflows/*`, поэтому
готовая версия лежит здесь. После мержа:

```bash
git checkout main && git pull
git mv ops/github-workflows/space-keepalive.yml .github/workflows/space-keepalive.yml
git rm ops/github-workflows/README-keepalive.md
git commit -m "ci: keepalive проверяет содержимое /api/health и алертит в Telegram"
git push
```

Что меняется: вместо «ответил ли сервер 200» keepalive запускает
`scripts/health_check.py` — тот проверяет `status`, `model_loaded` и
`tg_webhook`, шлёт в Telegram сообщение **только при смене состояния**
(упал / ожил / деградировал) и хранит прошлое состояние в `actions/cache`.

Скрипт существовал с самого начала, был покрыт тестами и **ни разу не
запускался**: воркфлоу `health-check.yml`, на который он ссылался, в
репозитории никогда не создавался.
