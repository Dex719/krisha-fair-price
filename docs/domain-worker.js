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

const ORIGIN = "https://dex719-krisha-fair-price.hf.space";
const CANONICAL_HOST = "bagam.info";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.hostname !== CANONICAL_HOST) {
      // www.bagam.info и прочие алиасы → один адрес для поисковиков
      url.hostname = CANONICAL_HOST;
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
    const response = await fetch(ORIGIN + url.pathname + url.search, {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
      redirect: "manual",
    });

    // Редирект приложения на адрес Space → тот же путь на домене
    const location = response.headers.get("location");
    if (location && location.startsWith(ORIGIN)) {
      const rewritten = new Response(response.body, response);
      rewritten.headers.set("location", `https://${CANONICAL_HOST}${location.slice(ORIGIN.length)}`);
      return rewritten;
    }
    return response;
  },
};
