// Cloudflare Worker: bagam.info → Hugging Face Space.
//
// Свой домен у HF Spaces только на платном PRO, поэтому домен смотрит в
// Cloudflare, а этот воркер пересылает каждый запрос в Space как есть
// (метод, путь, тело, заголовки) и отдаёт ответ обратно.
//
// Настоящий IP посетителя приложение видит только через X-Bagam-Client-IP и
// только вместе с ключом X-Bagam-Proxy-Key (секрет PROXY_KEY — одинаковый в
// воркере и в Space). Без ключа rate-limit увидел бы IP Cloudflare и делил бы
// один лимит на всех посетителей домена; без проверки ключа заголовок с IP
// подделал бы кто угодно.
//
// Настройка:
// 1. dash.cloudflare.com → Add a domain → bagam.info → тариф Free.
//    У регистратора домена заменить DNS-серверы на два, что выдаст Cloudflare.
// 2. Workers & Pages → Create → Worker «bagam-proxy» → вставить этот файл, Deploy.
// 3. Воркер → Settings → Variables and Secrets → Secret PROXY_KEY = длинная
//    случайная строка. Та же строка — секрет PROXY_KEY в Settings Space.
// 4. Воркер → Settings → Domains & Routes → Add → Custom domain:
//    bagam.info и www.bagam.info (DNS-записи и сертификат Cloudflare создаст сам).
// 5. В переменные Space: PUBLIC_BASE_URL=https://bagam.info — на этот адрес
//    смотрят sitemap/robots и кнопка «Открыть приложение» в боте. Webhook бота
//    остаётся на адресе Space (bot.webhook_base_url) и от Cloudflare не зависит.
// 6. SSL/TLS → Edge Certificates → Always Use HTTPS = On, затем HSTS. Воркер и
//    сам переводит http на https (ниже), но правило Cloudflare срабатывает
//    раньше воркера.

const ORIGIN = "https://dex719-krisha-fair-price.hf.space";
const CANONICAL_HOST = "bagam.info";
// Адрес Space в Location: любая схема, необязательный порт, дальше — путь
const SPACE_LOCATION = /^https?:\/\/dex719-krisha-fair-price\.hf\.space(?::\d+)?(\/.*)?$/i;

// Что можно отдавать из кэша Cloudflare: html-страницы сайта и статика с версией в URL.
const PAGE_PATHS = new Set([
  "/", "/stats", "/about", "/bot", "/privacy", "/terms", "/robots.txt", "/sitemap.xml", "/llms.txt", "/favicon.ico",
]);
function isCacheablePath(pathname) {
  return PAGE_PATHS.has(pathname) || pathname.startsWith("/static/");
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.hostname !== CANONICAL_HOST || url.protocol !== "https:") {
      // www.bagam.info, прочие алиасы и http → один адрес для поисковиков, одним
      // редиректом. Раньше http://www.bagam.info/x уходил на http://bagam.info/x,
      // а там отвечал 200 — сайт целиком жил и по http.
      url.hostname = CANONICAL_HOST;
      url.protocol = "https:";
      url.port = "";
      return Response.redirect(url.toString(), 301);
    }
    // /about/ → /about одним 301 (приложение делает то же само, но так адрес
    // со слэшем не доходит до Space вовсе). Только страницы: GET/HEAD, не API.
    const isRead = request.method === "GET" || request.method === "HEAD";
    if (isRead && url.pathname.length > 1 && url.pathname.endsWith("/") &&
        !/^\/(static|api|tg)\//.test(url.pathname)) {
      url.pathname = url.pathname.replace(/\/+$/, "") || "/";
      return Response.redirect(url.toString(), 301);
    }

    const headers = new Headers(request.headers);
    headers.delete("x-bagam-client-ip");
    headers.delete("x-bagam-proxy-key");
    const ip = request.headers.get("cf-connecting-ip");
    if (ip && env.PROXY_KEY) {
      headers.set("x-bagam-client-ip", ip);
      headers.set("x-bagam-proxy-key", env.PROXY_KEY);
    }
    headers.set("x-forwarded-host", CANONICAL_HOST);
    headers.set("x-forwarded-proto", "https");

    const hasBody = !["GET", "HEAD"].includes(request.method);
    // Страницы и статика одинаковы для всех (живые цифры приходят по /api/*),
    // а первый байт из Space идёт ~1,5 с: держим их в кэше Cloudflare две
    // минуты, чтобы повторные заходы и соседи по региону не ждали Space.
    // /api/*, /tg/* и всё с телом запроса — только из origin.
    const cacheable = !hasBody && isCacheablePath(url.pathname);
    const response = await fetch(ORIGIN + url.pathname + url.search, {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
      redirect: "manual",
      cf: cacheable ? { cacheEverything: true, cacheTtlByStatus: { "200-299": 120, "404": 10, "500-599": 0 } } : undefined,
    });

    const out = new Response(response.body, response);
    // HF вешает на каждый ответ Link: <huggingface.co/spaces/…>; rel="canonical" —
    // поисковик склеил бы домен со страницей Space и не индексировал bagam.info.
    // Канонический адрес задаёт <link rel="canonical"> в самих страницах.
    out.headers.delete("link");
    // Внутренние заголовки прокси HF: наружу им незачем
    out.headers.delete("x-proxied-host");
    out.headers.delete("x-proxied-path");
    out.headers.delete("x-proxied-replica");
    // Редирект приложения на адрес Space → тот же путь на домене. За прокси HF
    // приложение видит http, поэтому адрес Space в Location бывает и http://, и
    // с портом; раньше переписывался только https:// — и посетитель
    // bagam.info/about/ уезжал на http://…hf.space/about.
    const location = response.headers.get("location");
    const m = location && location.match(SPACE_LOCATION);
    if (m) {
      out.headers.set("location", `https://${CANONICAL_HOST}${m[1] || "/"}`);
    }
    return out;
  },
};
