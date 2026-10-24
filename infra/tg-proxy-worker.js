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
// 6. Воркер → Settings → Variables and Secrets → переменная BOT_ID = числовая
//    часть токена бота до двоеточия (у токена 123456789:AAH… это 123456789;
//    секретную часть после двоеточия сюда НЕ вписывать). Зачем: без BOT_ID
//    воркер пересылает любой путь /bot<токен>/... — то есть работает открытым
//    релеем для чужих ботов за счёт квоты воркера (суточный лимит запросов
//    бесплатного тарифа). С BOT_ID пропускаются только пути /bot<BOT_ID>:...,
//    остальные получают 404.
// 7. Там же — СЕКРЕТ (Encrypt) PROXY_SECRET = длинная случайная строка
//    (например, `openssl rand -hex 32`), а на хостинге бота — та же строка в
//    env TG_PROXY_SECRET. Приложение шлёт её заголовком X-Proxy-Secret
//    (krisha.bot._proxy_headers); запросы без него или с другим значением
//    получают 404. Порядок без простоя: сначала TG_PROXY_SECRET в Space
//    (лишний заголовок старый воркер просто перешлёт), потом PROXY_SECRET
//    в воркере. PROXY_SECRET не задан — проверки нет, как раньше.
//
// Безопасность: воркер пересылает только пути вида /bot<token>/... — без
// валидного токена бота он бесполезен для Telegram, но квоту воркера тратит
// любой, кто знает его адрес. BOT_ID (шаг 6) публичен — это часть токена,
// которая видна в t.me-ссылках и логах; закрывает воркер только PROXY_SECRET
// (шаг 7). Без них остаётся прежнее поведение, чтобы уже развёрнутый воркер не
// сломался. Проверка пути идёт по url.pathname уже после нормализации адреса:
// «/..» и «%2e%2e» из пути «/bot<BOT_ID>:x/../bot<чужой>:y/...» схлопываются
// до проверки, и дальше уходит ровно тот путь, который проверили.

const SECRET_HEADER = "X-Proxy-Secret";

// Сравнение за постоянное время: по времени ответа секрет не подобрать.
function sameSecret(a, b) {
  const enc = new TextEncoder();
  const x = enc.encode(a);
  const y = enc.encode(b);
  let diff = x.length ^ y.length;
  for (let i = 0; i < y.length; i++) {
    diff |= (x[i % (x.length || 1)] || 0) ^ y[i];
  }
  return diff === 0;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/bot")) {
      return new Response("Not found", { status: 404 });
    }
    // PROXY_SECRET задан — без верного заголовка не пускаем никого.
    const secret = env && env.PROXY_SECRET ? String(env.PROXY_SECRET).trim() : "";
    if (secret && !sameSecret(request.headers.get(SECRET_HEADER) || "", secret)) {
      return new Response("Not found", { status: 404 });
    }
    // BOT_ID задан — пускаем только своего бота. Если в переменную по ошибке
    // вписан токен целиком, префикс «/bot<токен>:» никогда не совпадёт: воркер
    // закрыт для всех, а не открыт для всех.
    const botId = env && env.BOT_ID ? String(env.BOT_ID).trim() : "";
    if (botId && !url.pathname.startsWith(`/bot${botId}:`)) {
      return new Response("Not found", { status: 404 });
    }
    url.protocol = "https:";
    url.hostname = "api.telegram.org";
    url.port = "";
    // Секрет — только между приложением и воркером, в Telegram его не несём.
    const headers = new Headers(request.headers);
    headers.delete(SECRET_HEADER);
    return fetch(new Request(url, new Request(request, { headers })));
  },
};
