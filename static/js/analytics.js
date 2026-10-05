/* Счётчик посещаемости: Google Analytics 4.
   Сервер дописывает этот скрипт в <head>, только если в окружении задан поток GA4
   (src/krisha/api/site_analytics.py), — идентификатор приходит в data-ga. Без него скрипт
   ничего не делает.
   Очередь gtag() создаётся сразу, а сама библиотека грузится после load, когда браузер
   простаивает: первая отрисовка и LCP за счётчик не платят. События до загрузки ждут в очереди.
   window.bagamTrack(имя, параметры) — событие GA4; страницы зовут его через bagam.track из
   site.js. Список событий — в README, раздел «Аналитика». */
(function (w, d) {
  var me = d.currentScript;
  var ga = me ? me.getAttribute('data-ga') || '' : '';
  if (!ga) return;
  /* Mini App в Telegram: в адресе страницы #tgWebAppData с профилем (id, имя, @username).
     Политика обещает, что данные профиля Telegram никуда не уходят, — внутри Mini App счётчика
     нет вовсе. */
  if (/tgWebApp/i.test(location.search + location.hash) || w.TelegramWebviewProxy) return;

  w.dataLayer = w.dataLayer || [];
  w.gtag = w.gtag || function () { w.dataLayer.push(arguments); };
  w.gtag('js', new Date());
  /* без рекламных функций Google: политика обещает «0 рекламы» */
  w.gtag('config', ga, { allow_google_signals: false, allow_ad_personalization_signals: false });

  w.bagamTrack = function (name, params) {
    try { w.gtag('event', name, params || {}); } catch (e) {}
  };

  /* переход в Telegram-бота — главная конверсия сайта: ссылки на него есть на каждой странице */
  d.addEventListener('click', function (e) {
    var a = e.target && e.target.closest ? e.target.closest('a[href*="t.me/fairprice_kzbot"]') : null;
    if (a) w.bagamTrack('bot_click', { place: location.pathname });
  }, true);

  function load() {
    var s = d.createElement('script');
    s.async = true;
    s.src = 'https://www.googletagmanager.com/gtag/js?id=' + encodeURIComponent(ga);
    d.head.appendChild(s);
  }
  function later() {
    if (w.requestIdleCallback) w.requestIdleCallback(load, { timeout: 3000 });
    else setTimeout(load, 1500);
  }
  if (d.readyState === 'complete') later();
  else w.addEventListener('load', later);
})(window, document);
