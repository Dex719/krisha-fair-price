/* Счётчики посещаемости: Яндекс Метрика и Google Analytics 4.
   Сервер дописывает этот скрипт в <head>, только если в окружении заданы номера счётчиков
   (src/krisha/api/site_analytics.py), — номера приходят в data-ym и data-ga. Без них скрипт
   ничего не делает.
   Очереди ym() и gtag() создаются сразу, а сами библиотеки грузятся после load, когда браузер
   простаивает: первая отрисовка и LCP за счётчики не платят. События до загрузки ждут в очереди.
   window.bagamTrack(имя, параметры) — цель Метрики (reachGoal) и событие GA4 с тем же именем;
   страницы зовут его через bagam.track из site.js. Список целей — в README, раздел «Аналитика». */
(function (w, d) {
  var me = d.currentScript;
  var ym = me ? +me.getAttribute('data-ym') || 0 : 0;
  var ga = me ? me.getAttribute('data-ga') || '' : '';
  if (!ym && !ga) return;

  var libs = [];
  if (ym) {
    w.ym = w.ym || function () { (w.ym.a = w.ym.a || []).push(arguments); };
    w.ym.l = +new Date();
    w.ym(ym, 'init', { clickmap: true, trackLinks: true, accurateTrackBounce: true, webvisor: true });
    libs.push('https://mc.yandex.ru/metrika/tag.js');
  }
  if (ga) {
    w.dataLayer = w.dataLayer || [];
    w.gtag = w.gtag || function () { w.dataLayer.push(arguments); };
    w.gtag('js', new Date());
    w.gtag('config', ga);
    libs.push('https://www.googletagmanager.com/gtag/js?id=' + encodeURIComponent(ga));
  }

  w.bagamTrack = function (name, params) {
    try {
      if (ym) w.ym(ym, 'reachGoal', name, params || {});
      if (ga) w.gtag('event', name, params || {});
    } catch (e) {}
  };

  /* переход в Telegram-бота — главная конверсия сайта: ссылки на него есть на каждой странице */
  d.addEventListener('click', function (e) {
    var a = e.target && e.target.closest ? e.target.closest('a[href*="t.me/fairprice_kzbot"]') : null;
    if (a) w.bagamTrack('bot_click', { place: location.pathname });
  }, true);

  function load() {
    libs.forEach(function (src) {
      var s = d.createElement('script');
      s.async = true;
      s.src = src;
      d.head.appendChild(s);
    });
    libs = [];
  }
  function later() {
    if (w.requestIdleCallback) w.requestIdleCallback(load, { timeout: 3000 });
    else setTimeout(load, 1500);
  }
  if (d.readyState === 'complete') later();
  else w.addEventListener('load', later);
})(window, document);
