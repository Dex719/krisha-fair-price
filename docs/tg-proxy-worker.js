// Прокси для Telegram Bot API через Cloudflare Worker.
//
// Зачем: некоторые хостинги (например, Hugging Face Spaces) не пропускают
// исходящие запросы к api.telegram.org — TLS handshake виснет до таймаута.
// Воркер живёт в сети Cloudflare и просто пересылает запросы дальше.
//
// Настройка (~5 минут):
// 1. dash.cloudflare.com → Workers & Pages → Create Worker.
// 2. Вставить этот файл целиком, Deploy.
// 3. Скопировать адрес воркера (https://<имя>.<аккаунт>.workers.dev).
// 4. На хостинге бота задать env-переменную:
//    TG_API_BASE=https://<имя>.<аккаунт>.workers.dev
// 5. Перезапустить приложение — webhook зарегистрируется сам.
//
// Безопасность: воркер пересылает только пути вида /bot<token>/... —
// без валидного токена бота он бесполезен.

export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/bot")) {
      return new Response("Not found", { status: 404 });
    }
    url.protocol = "https:";
    url.hostname = "api.telegram.org";
    url.port = "";
    return fetch(new Request(url, request));
  },
};
