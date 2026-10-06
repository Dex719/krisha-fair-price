/* baǵam — общий скрипт всех страниц сайта.

   Подключение: <script src="/static/js/site.js" defer></script> в <head>. На главной и на
   «Рынке» — с атрибутом data-gsap: только там bagam.boot() подгружает GSAP + ScrollTrigger.
   Тему до первой отрисовки ставит маленький инлайн-скрипт в <head>; он же выставляет классы
   html.js / html.anim / html.lite / html.rm и заводит очередь window.bagam.ready(fn), поэтому
   постраничный код может обращаться к bagam откуда угодно — fn выполнится, когда этот файл готов.

   Наружу — window.bagam:
     ready(fn)              fn(bagam) после инициализации (до неё — в очередь, после — сразу)
     boot(fn)               fn после загрузки GSAP (только на data-gsap-страницах); на слабом
                            устройстве, при экономии трафика или медленной сети — сразу и без GSAP
     put(key, text)         текст во все [data-l=key]; бегущий счётчик подхватит его в конце
     live                   Promise<{stats, health}> — ответы /api/stats и /api/health (null — не пришёл).
                            <html data-live="off"> (страница 404): в API не ходим, live сразу {null, null},
                            элементы подвала .flive с живыми цифрами прячет CSS
     stats, health          последние ответы (свежие или из кэша bagam-stats)
     ru(n)                  целое с неразрывным пробелом в разрядах: 21 113
     fmt(n, digits=1)       число с запятой: 7,3 / 1 234,5
     pct(n, digits=1)       процент: 7,3%
     plural(n, [1,2,5])     склонение: plural(3, ['объявление','объявления','объявлений']) → 'объявления'
     esc(s)                 экранирование для innerHTML
     glide(y|fn, ms, done)  плавная прокрутка своими кадрами — всегда, в любом режиме
     reveal(selector)       мягкое появление блоков ниже первого экрана (IntersectionObserver + CSS)
     countUp(el)            счётчик от нуля до текущего текста элемента (сам запускается для [data-count])
     markCurrent()          пересчитать aria-current в шапке, меню и подвале (после history.replaceState)
     tg(fn)                 fn(Telegram.WebApp), когда SDK загружен (только внутри Telegram)
     theme(), setTheme(t)   текущая тема и её смена ('light' | 'dark', выбор запоминается)
     rm, lite               «меньше движения» в системе и лёгкий режим (слабое устройство, сеть)
   События на document: 'stats' (detail — /api/stats), 'health' (detail — /api/health),
   'bagam:theme' (detail — 'light' | 'dark'). */
(function (w, d) {
  'use strict';

  var root = d.documentElement;
  var me = d.currentScript;
  var NB = '\u00a0';
  var B = (w.bagam && typeof w.bagam === 'object') ? w.bagam : {};
  var queue = B.q || [];
  w.bagam = B;

  function mq(q) { return w.matchMedia ? w.matchMedia(q) : { matches: false }; }
  function $$(sel, ctx) { return Array.prototype.slice.call((ctx || d).querySelectorAll(sel)); }
  function on(el, ev, fn, opt) { el.addEventListener(ev, fn, opt || false); }
  function fire(name, detail) {
    try { d.dispatchEvent(new CustomEvent(name, { detail: detail })); } catch (e) {}
  }
  function later(fn) { if (w.requestIdleCallback) w.requestIdleCallback(fn, { timeout: 1200 }); else setTimeout(fn, 80); }

  /* «Меньше движения» в системе анимации НЕ выключает: у владельца в Windows анимации выключены,
     и сайт без входов, счётчиков и плавной прокрутки выглядел мёртвым — он прямо просил их
     вернуть (2026-10-02, снова 2026-10-06). Статичен только лёгкий режим: слабое устройство,
     экономия трафика, медленная сеть. B.rm — для тонкой настройки, если понадобится. */
  B.rm = mq('(prefers-reduced-motion: reduce)').matches;
  B.lite = root.classList.contains('lite');
  function still() { return B.lite; }

  /* ------------------------------------------------------------ форматирование */
  function isNum(x) { return typeof x === 'number' && isFinite(x); }
  function group(s) { return s.replace(/\B(?=(\d{3})+(?!\d))/g, NB); }
  function ru(n) {
    if (n === null || n === undefined || n === '' || !isFinite(+n)) return '—';
    n = Math.round(+n);
    return (n < 0 ? '−' : '') + group(String(Math.abs(n)));
  }
  function fmt(n, digits) {
    if (n === null || n === undefined || n === '' || !isFinite(+n)) return '—';
    var p = Math.abs(+n).toFixed(digits == null ? 1 : digits).split('.');
    var neg = +n < 0 && /[1-9]/.test(p.join(''));
    return (neg ? '−' : '') + group(p[0]) + (p[1] ? ',' + p[1] : '');
  }
  function pct(n, digits) { var s = fmt(n, digits); return s === '—' ? s : s + '%'; }
  function plural(n, f) {
    var a = Math.abs(n) % 100, b = a % 10;
    if (a > 10 && a < 20) return f[2];
    if (b > 1 && b < 5) return f[1];
    return b === 1 ? f[0] : f[2];
  }
  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  B.ru = ru; B.fmt = fmt; B.pct = pct; B.plural = plural; B.esc = esc;

  /* ------------------------------------------------------------ тема */
  /* Один ключ bagam-theme. Старый kfp-theme переносим и удаляем. Без сохранённого выбора
     тема следует системной (и её смене на лету) — это решает инлайн-скрипт в <head>. */
  var KEY = 'bagam-theme', OLD_KEY = 'kfp-theme';
  var BG = { dark: '#0A100C', light: '#F1F4EC' };
  function store() { try { return w.localStorage; } catch (e) { return null; } }
  (function migrate() {
    var s = store();
    if (!s) return;
    try {
      var old = s.getItem(OLD_KEY);
      if (old === null) return;
      if (!s.getItem(KEY) && (old === 'light' || old === 'dark')) s.setItem(KEY, old);
      s.removeItem(OLD_KEY);
    } catch (e) {}
  })();
  function saved() {
    var s = store();
    try { var v = s && s.getItem(KEY); return v === 'light' || v === 'dark' ? v : null; } catch (e) { return null; }
  }
  function theme() { return root.getAttribute('data-theme') === 'light' ? 'light' : 'dark'; }
  function paintTheme() {
    var t = theme(), light = t === 'light';
    $$('[data-theme-label]').forEach(function (e) { e.textContent = light ? 'светлая' : 'тёмная'; });
    $$('[data-theme-toggle]').forEach(function (b) {
      b.setAttribute('aria-label', light ? 'Включить тёмную тему' : 'Включить светлую тему');
    });
    /* цвет панели браузера — по выбранной теме, а не по системной */
    $$('meta[name="theme-color"]').forEach(function (m) { m.setAttribute('content', BG[t]); });
    tgHeader();
  }
  function setTheme(t, remember) {
    if (t !== 'light' && t !== 'dark') return;
    root.setAttribute('data-theme', t);
    if (remember !== false) { var s = store(); try { if (s) s.setItem(KEY, t); } catch (e) {} }
    paintTheme();
    fire('bagam:theme', t);
  }
  B.theme = theme;
  B.setTheme = setTheme;
  on(d, 'click', function (e) {
    var b = e.target.closest && e.target.closest('[data-theme-toggle]');
    if (!b) return;
    e.preventDefault();
    setTheme(theme() === 'light' ? 'dark' : 'light', true);
  });
  var sysLight = mq('(prefers-color-scheme: light)');
  function sysChanged() { if (!saved()) setTheme(sysLight.matches ? 'light' : 'dark', false); }
  if (sysLight.addEventListener) sysLight.addEventListener('change', sysChanged);
  else if (sysLight.addListener) sysLight.addListener(sysChanged);
  on(w, 'storage', function (e) {
    if (e.key === KEY && (e.newValue === 'light' || e.newValue === 'dark')) setTheme(e.newValue, false);
  });

  /* ------------------------------------------------------------ Telegram Mini App */
  var tgQueue = [];
  function tgApp() { return w.Telegram && w.Telegram.WebApp; }
  function flushTG() { var tg = tgApp(); if (!tg) return; while (tgQueue.length) tgQueue.shift()(tg); }
  function tgHeader() {
    var tg = tgApp();
    if (tg && tg.setHeaderColor && root.classList.contains('tma')) {
      try { tg.setHeaderColor(BG[theme()]); } catch (e) {}
    }
  }
  B.tg = function (fn) { tgQueue.push(fn); flushTG(); };
  var inTG = /tgWebApp/i.test(location.search + location.hash) || !!w.TelegramWebviewProxy || /Telegram/i.test(navigator.userAgent);
  if (inTG) {
    var tgs = d.createElement('script');
    tgs.src = 'https://telegram.org/js/telegram-web-app.js';
    tgs.async = true;
    tgs.onload = flushTG;
    d.head.appendChild(tgs);
  }
  B.tg(function (tg) {
    if (!tg.initData && !(tg.platform && tg.platform !== 'unknown')) return;
    root.classList.add('tma');
    try {
      tg.ready();
      tg.expand();
      tgHeader();
      if (tg.BackButton && location.pathname !== '/') {
        tg.BackButton.show();
        tg.BackButton.onClick(function () { if (history.length > 1) history.back(); else location.href = '/'; });
      }
    } catch (e) {}
  });

  /* ------------------------------------------------------------ текущая страница */
  /* Сравниваем путь, а не полный адрес: location.href с доменом никогда не совпадал с «/stats».
     /rent теперь часть «Рынка» (режим ?mode=rent). */
  function norm(p) {
    p = String(p || '/').replace(/\/index\.html$/, '/').replace(/\.html$/, '').replace(/\/+$/, '') || '/';
    return p === '/rent' ? '/stats' : p;
  }
  function modeOf(pathname, search) {
    if (String(pathname || '').replace(/\/+$/, '') === '/rent') return 'rent';
    try { return new URLSearchParams(search || '').get('mode') === 'rent' ? 'rent' : ''; } catch (e) { return ''; }
  }
  function setCurrent(a, yes) {
    if (yes) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  }
  function markCurrent() {
    var here = norm(location.pathname), mode = modeOf(location.pathname, location.search);
    $$('nav .menu a, #mmenu a.ml').forEach(function (a) {
      setCurrent(a, a.host === location.host && norm(a.pathname) === here);
    });
    $$('footer .flink').forEach(function (a) {
      if (a.host !== location.host) return;
      setCurrent(a, norm(a.pathname) === here && modeOf(a.pathname, a.search) === mode);
    });
  }
  B.markCurrent = markCurrent;
  markCurrent();

  /* ------------------------------------------------------------ мобильное меню */
  (function () {
    var burg = d.querySelector('.burg'), menu = d.getElementById('mmenu');
    if (!burg || !menu) return;
    var open = false;
    var wide = mq('(min-width:821px)');
    function focusables() {
      return [burg].concat($$('a[href],button:not([disabled])', menu)).filter(function (el) {
        return el === burg || el.offsetParent !== null || el.classList.contains('mclose');
      });
    }
    function outside(off) {
      $$('body > main, body > footer, body > .skip').forEach(function (el) {
        if (off) el.setAttribute('inert', ''); else el.removeAttribute('inert');
      });
    }
    function set(v, restoreFocus) {
      open = v;
      burg.setAttribute('aria-expanded', v ? 'true' : 'false');
      burg.setAttribute('aria-label', v ? 'Закрыть меню' : 'Меню');
      d.body.classList.toggle('mopen', v);
      menu.classList.toggle('open', v);
      menu.setAttribute('aria-hidden', v ? 'false' : 'true');
      outside(v);
      if (v) {
        /* visibility у .mmenu.open меняется без перехода (design.css), так что фокус встаёт сразу;
           повтор по кадрам — страховка, если стили ещё не применились */
        var first = menu.querySelector('a.ml[aria-current="page"]') || menu.querySelector('a.ml');
        var tries = 0;
        (function focusFirst() {
          if (!open || !first) return;
          first.focus({ preventScroll: true });
          if (d.activeElement !== first && ++tries < 10) w.requestAnimationFrame(focusFirst);
        })();
      } else if (restoreFocus !== false) {
        burg.focus({ preventScroll: true });
      }
    }
    on(burg, 'click', function () { set(!open); });
    $$('[data-mclose]', menu).forEach(function (b) { on(b, 'click', function () { set(false); }); });
    on(d, 'keydown', function (e) {
      if (!open) return;
      if (e.key === 'Escape') { e.preventDefault(); set(false); return; }
      if (e.key !== 'Tab') return;
      /* ловушка фокуса: Tab ходит по кнопке меню и пунктам, наружу не выходит. Шаг делаем сами,
         а не браузером: если пункт ещё не принимает фокус, фокус остаётся внутри, а не уходит на body */
      var list = focusables(), n = list.length, i = list.indexOf(d.activeElement);
      if (!n) return;
      e.preventDefault();
      list[i === -1 ? (e.shiftKey ? n - 1 : 0) : (i + (e.shiftKey ? n - 1 : 1)) % n].focus();
    });
    $$('a', menu).forEach(function (a) {
      on(a, 'click', function (e) {
        /* текущая страница — просто закрыть меню, без перезагрузки */
        if (a.getAttribute('aria-current') === 'page' && a.pathname === location.pathname && a.search === location.search && !a.hash) {
          e.preventDefault();
        }
        if (a.host === location.host) set(false, false);
      });
    });
    function onWide() { if (wide.matches && open) set(false, false); }
    if (wide.addEventListener) wide.addEventListener('change', onWide); else if (wide.addListener) wide.addListener(onWide);
  })();

  /* ------------------------------------------------------------ живые цифры */
  /* Разметка несёт снимок сборки (или «—»); здесь его заменяют ответы /api/stats и /api/health.
     Ключи [data-l]: total ppsm ppsmk medprice medpricem upd · mape mapeci mdape rmape r2 mae
     tvnote age status — и totalw: слово при числе total в нужной форме (формы — data-forms="1,2,5").
     Другие значения data-l скрипт не трогает. */
  var LKEYS = ['total', 'ppsm', 'ppsmk', 'medprice', 'medpricem', 'upd', 'mape', 'mapeci', 'mdape',
               'rmape', 'r2', 'mae', 'tvnote', 'age', 'status'];
  function liveEls() { return $$(LKEYS.map(function (k) { return '[data-l="' + k + '"]'; }).join(',')); }
  function put(key, text) {
    var sel = /^[\[.#]/.test(key) ? key : '[data-l="' + key + '"]';
    $$(sel).forEach(function (el) {
      el.__final = text;
      if (!el.__counting) el.textContent = text;
    });
  }
  B.put = put;
  /* цель счётчиков посещаемости (static/js/analytics.js); без счётчиков — ничего не делает */
  B.track = function (name, params) {
    if (typeof w.bagamTrack === 'function') w.bagamTrack(name, params);
  };
  function almatyDate(iso) {
    var t = Date.parse(iso);
    if (isNaN(t)) return null;
    var dt = new Date(t + 5 * 36e5); /* Алматы, UTC+5 */
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    return p(dt.getUTCDate()) + '.' + p(dt.getUTCMonth() + 1) + '.' + dt.getUTCFullYear();
  }
  function age(h) {
    if (h == null) return '';
    if (h < 1.5) return 'обновлено только что';
    if (h < 24) return 'обновлено ' + Math.round(h) + ' ч назад';
    var n = Math.round(h / 24);
    return 'обновлено ' + n + ' ' + plural(n, ['день', 'дня', 'дней']) + ' назад';
  }
  function apply(s, h) {
    if (s) {
      if (isNum(s.total_listings)) {
        var n = Math.round(s.total_listings);
        put('total', ru(n));
        /* слово при числе: формы для 1, 2, 5 — из data-forms, по умолчанию «объявление» */
        $$('[data-l="totalw"]').forEach(function (e) {
          var f = (e.getAttribute('data-forms') || 'объявление,объявления,объявлений').split(',');
          if (f.length === 3) e.textContent = plural(n, f);
        });
      }
      if (isNum(s.median_ppsm)) {
        put('ppsm', ru(s.median_ppsm));
        put('ppsmk', ru(s.median_ppsm / 1000) + NB + 'тыс' + NB + '₸/м²');
      }
      if (isNum(s.median_price)) {
        put('medprice', ru(s.median_price));
        put('medpricem', fmt(s.median_price / 1e6, 1) + NB + 'млн' + NB + '₸');
      }
      var upd = almatyDate(s.updated_at);
      if (upd) put('upd', upd);
      B.stats = s;
      w.__stats = s;
      fire('stats', s);
    }
    if (h) {
      if (isNum(h.model_error_pct)) put('mape', pct(h.model_error_pct));
      if (h.model_error_ci_pct && h.model_error_ci_pct.length === 2)
        put('mapeci', ', 95% ДИ ' + fmt(h.model_error_ci_pct[0]) + '–' + pct(h.model_error_ci_pct[1]));
      if (isNum(h.model_median_error_pct)) put('mdape', pct(h.model_median_error_pct));
      if (isNum(h.rent_model_error_pct)) put('rmape', pct(h.rent_model_error_pct));
      if (isNum(h.model_r2)) put('r2', h.model_r2.toFixed(3));
      if (isNum(h.model_mae)) put('mae', fmt(h.model_mae / 1e6, 2));
      if (h.model_temporal_validity === true)
        put('tvnote', 'Как читать эти цифры: временная валидность оценки подтверждена — состав данных не меняется вместе с временем, поэтому число описывает и будущие объявления, а не только сегодняшний сток.');
      if (h.data_age_hours != null) put('age', age(h.data_age_hours));
      var ok = h.status === 'ok' && h.model_loaded;
      $$('[data-l="status"]').forEach(function (e) {
        e.textContent = ok ? 'сервис работает' : 'сервис недоступен';
        e.classList.toggle('off', !ok);
      });
      B.health = h;
      fire('health', h);
    }
  }
  /* скелетоны: без свежего кэша живые числа и графики мерцают, пока не придут данные */
  function skel(onOff) {
    liveEls().forEach(function (e) { e.classList.toggle('sk', onOff); });
    $$('[data-skel]').forEach(function (e) { e.classList.toggle('loading', onOff); });
  }
  function getJSON(path) {
    var ctl = ('AbortController' in w) ? new AbortController() : null;
    if (ctl) setTimeout(function () { ctl.abort(); }, 12000);
    /* обычный HTTP-кэш: сервер сам ставит max-age, повторные заходы не ждут сеть */
    return fetch(path, { signal: ctl ? ctl.signal : undefined, credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }
  var CACHE_KEY = 'bagam-stats';
  /* 404 и другие страницы без живых цифр: ни запросов, ни скелетонов, ни кэша */
  var LIVE = root.getAttribute('data-live') !== 'off';
  var cached = false;
  if (LIVE) {
    try {
      var cc = JSON.parse(localStorage.getItem(CACHE_KEY) || 'null');
      if (cc && Date.now() - cc.t < 864e5) { apply(cc.s, cc.h); cached = true; }
    } catch (e) {}
  }
  if (LIVE && !cached) skel(true);
  B.live = !LIVE ? Promise.resolve({ stats: null, health: null }) :
    Promise.all([getJSON('/api/stats'), getJSON('/api/health')]).then(function (res) {
      var s = res[0], h = res[1];
      skel(false);
      if (s || h) {
        try { localStorage.setItem(CACHE_KEY, JSON.stringify({ t: Date.now(), s: s, h: h })); } catch (e) {}
        apply(s, h);
      } else {
        put('age', 'цифры из последнего успешного обновления');
      }
      return { stats: s || B.stats || null, health: h || B.health || null };
    });

  /* ------------------------------------------------------------ счётчики */
  /* [data-count]: число бежит от нуля до ТЕКУЩЕГО текста элемента. Цель перечитывается на каждом
     кадре, а в конце ставится последнее значение — свежий ответ API не перетирается старым. */
  var NUM_RX = /^([^\d−-]*)([−-]?\d[\d\s\u00a0]*(?:[.,]\d+)?)([\s\S]*)$/;
  function parseShown(str) {
    var m = NUM_RX.exec(String(str || '').trim());
    if (!m) return null;
    var raw = m[2].replace(/[\s\u00a0]/g, ''), neg = /^[−-]/.test(raw);
    raw = raw.replace(/^[−-]/, '');
    var parts = raw.split(/[.,]/), n = parseFloat(raw.replace(',', '.'));
    if (!isFinite(n)) return null;
    return {
      pre: m[1], post: m[3], n: neg ? -n : n, dec: (parts[1] || '').length,
      sep: raw.indexOf(',') >= 0 ? ',' : '.', grouped: /[\s\u00a0]/.test(m[2]) || n >= 1e4
    };
  }
  function shownLike(p, v) {
    var s = Math.abs(v).toFixed(p.dec).split('.');
    if (p.grouped) s[0] = group(s[0]);
    return p.pre + (v < 0 ? '−' : '') + s.join(p.sep) + p.post;
  }
  function countUp(el) {
    if (el.__counting || el.__counted) return;
    el.__counted = true;
    if (el.__final == null) el.__final = el.textContent;
    if (still() || !parseShown(el.__final)) return;
    el.__counting = true;
    var t0 = 0, dur = 1400;
    function frame(t) {
      if (!t0) t0 = t;
      var k = Math.min(1, (t - t0) / dur), e = k >= 1 ? 1 : 1 - Math.pow(2, -10 * k);
      var p = parseShown(el.__final);
      if (k < 1 && p) { el.textContent = shownLike(p, p.n * e); w.requestAnimationFrame(frame); }
      else { el.__counting = false; el.textContent = el.__final; }
    }
    w.requestAnimationFrame(frame);
  }
  B.countUp = countUp;
  (function () {
    var els = $$('[data-count]');
    if (!els.length || still() || !('IntersectionObserver' in w)) return;
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (en) {
        if (!en.isIntersecting) return;
        var el = en.target;
        io.unobserve(el);
        /* под скелетоном счёт не видно — ждём данных */
        if (el.classList.contains('sk')) B.live.then(function () { countUp(el); });
        else countUp(el);
      });
    }, { threshold: 0.6 });
    els.forEach(function (el) { io.observe(el); });
  })();

  /* ------------------------------------------------------------ прокрутка */
  /* Своими кадрами: время от первого кадра, цель пересчитывается на каждом кадре (над ней может
     что-то схлопнуться). Колесо или касание останавливают. Едет одинаково везде — и при «меньше
     движения» в системе, и в лёгком режиме: прокрутку просит сам человек кнопкой или ссылкой, а
     прыжок на тысячи пикселей теряет место на странице (владелец просил плавно, 2026-10-02). */
  function jump(y) {
    var prev = root.style.scrollBehavior;
    root.style.scrollBehavior = 'auto';
    w.scrollTo(0, y);
    root.style.scrollBehavior = prev;
  }
  function glide(target, dur, done) {
    var fn = typeof target === 'function' ? target : function () { return +target || 0; };
    var from = w.pageYOffset || root.scrollTop;
    if (Math.abs(fn() - from) < 2) { jump(fn()); if (done) done(); return; }
    var prev = root.style.scrollBehavior;
    root.style.scrollBehavior = 'auto';
    var t0 = 0, stopped = false;
    function stop() { stopped = true; }
    on(w, 'wheel', stop, { passive: true, once: true });
    on(w, 'touchstart', stop, { passive: true, once: true });
    function end() {
      root.style.scrollBehavior = prev;
      w.removeEventListener('wheel', stop);
      w.removeEventListener('touchstart', stop);
      if (done && !stopped) done();
    }
    function frame(t) {
      if (stopped) { end(); return; }
      if (!t0) t0 = t;
      var k = Math.min(1, (t - t0) / (dur || 600));
      var e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
      w.scrollTo(0, from + (fn() - from) * e);
      if (k < 1) w.requestAnimationFrame(frame); else end();
    }
    w.requestAnimationFrame(frame);
  }
  B.glide = glide;
  $$('.ftop').forEach(function (btn) {
    on(btn, 'click', function () {
      var from = w.pageYOffset || root.scrollTop;
      glide(0, Math.min(2400, Math.max(1200, from * 0.7)));
    });
  });
  /* якоря на странице (оглавление документов и т.п.): быстрый плавный переход вместо прыжка */
  on(d, 'click', function (e) {
    if (e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest && e.target.closest('a[href^="#"]');
    if (!a || a.classList.contains('skip')) return;
    var id = decodeURIComponent(a.getAttribute('href').slice(1)), el = id && d.getElementById(id);
    if (!el) return;
    e.preventDefault();
    var target = function () {
      var off = parseFloat(getComputedStyle(el).scrollMarginTop) || 0;
      var y = el.getBoundingClientRect().top + (w.pageYOffset || root.scrollTop) - off;
      return Math.max(0, Math.min(y, root.scrollHeight - w.innerHeight));
    };
    var dist = Math.abs(target() - (w.pageYOffset || root.scrollTop));
    glide(target, Math.min(650, Math.max(360, dist * 0.22)), function () {
      if (location.hash !== '#' + id && history.pushState) history.pushState(null, '', '#' + id);
    });
  });



  /* ------------------------------------------------------------ GSAP по требованию */
  var wantsGsap = !!(me && me.hasAttribute('data-gsap'));
  var gsapReady = null;
  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var el = d.createElement('script');
      el.src = src;
      el.async = false;
      el.onload = resolve;
      el.onerror = reject;
      d.head.appendChild(el);
    });
  }
  function slowNetwork() {
    /* сеть сама себя измерила: html полз дольше двух секунд — 65 КБ библиотеки важнее украшений */
    var nv = w.performance && performance.getEntriesByType && performance.getEntriesByType('navigation')[0];
    return !!(nv && (nv.responseEnd - nv.requestStart) > 2000);
  }
  function gsapLoad() {
    if (gsapReady) return gsapReady;
    gsapReady = new Promise(function (resolve) {
      var guard = setTimeout(resolve, 7000);
      function start() {
        if (slowNetwork()) {
          root.classList.remove('anim');
          root.classList.add('lite');
          B.lite = true;
          clearTimeout(guard);
          resolve();
          return;
        }
        loadScript('/static/js/gsap.min.js')
          .then(function () { return loadScript('/static/js/ScrollTrigger.min.js'); })
          .then(function () { clearTimeout(guard); resolve(); }, function () { clearTimeout(guard); resolve(); });
      }
      if (d.readyState === 'complete') later(start);
      else on(w, 'load', function () { later(start); });
    });
    return gsapReady;
  }
  function run(fn) { try { fn(); } catch (e) { setTimeout(function () { throw e; }); } }
  B.boot = function (fn) {
    if (!wantsGsap || still()) { run(fn); return; }
    gsapLoad().then(function () { run(fn); });
  };

  /* ------------------------------------------------------------ появление блоков (без GSAP) */
  /* Прячем только то, что ниже первого экрана: уже нарисованное не исчезает и не «влетает». */
  var REVEAL_DEFAULT = '.sect .shead,.bsplit>*';
  function reveal(sel) {
    if (still() || !('IntersectionObserver' in w)) return;
    var vh = w.innerHeight || root.clientHeight;
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (en) {
        if (!en.isIntersecting) return;
        var el = en.target;
        io.unobserve(el);
        el.setAttribute('data-rv', 'in');
        var done = function (e) {
          if (e && e.target !== el) return;
          el.removeAttribute('data-rv');
          el.style.removeProperty('--rvi');
          el.removeEventListener('transitionend', done);
        };
        on(el, 'transitionend', done);
        setTimeout(done, 1400);
      });
    }, { rootMargin: '0px 0px -6% 0px', threshold: 0.01 });
    $$(sel || REVEAL_DEFAULT).forEach(function (el) {
      if (el.hasAttribute('data-rv')) return;
      var r = el.getBoundingClientRect();
      if (r.top < vh || r.bottom <= 0 || !r.height) return;
      var p = el.parentNode;
      p.__rvn = (p.__rvn || 0) + 1;
      el.style.setProperty('--rvi', String(Math.min(p.__rvn - 1, 6)));
      el.setAttribute('data-rv', '');
      io.observe(el);
    });
  }
  B.reveal = reveal;
  if (!wantsGsap) reveal();

  /* ------------------------------------------------------------ формы-переходы на главную */
  /* Подстраницы не считают оценку сами: ссылка уходит на главную, /#check=<id>.
     Обработчик вешается сразу, а не после GSAP. Если форма всё же ушла обычным GET
     (Enter до загрузки скрипта) — подбираем ?url= и уводим туда же. */
  var LOT_RX = /krisha\.kz\/a\/show\/(\d+)/i;
  (function () {
    try {
      var u = new URLSearchParams(location.search).get('url');
      var m = u && LOT_RX.exec(u);
      if (m) location.replace('/#check=' + m[1]);
    } catch (e) {}
  })();
  $$('form[data-jump]').forEach(function (f) {
    var inp = f.querySelector('input'), err = f.parentElement.querySelector('[data-err]'), paste = f.querySelector('[data-paste]');
    if (!inp) return;
    function clear() { if (err) err.classList.remove('on'); inp.removeAttribute('aria-invalid'); }
    if (paste) {
      if (!navigator.clipboard || !navigator.clipboard.readText) paste.remove();
      else on(paste, 'click', function () {
        navigator.clipboard.readText().then(function (t) {
          t = (t || '').trim();
          if (t) { inp.value = t; clear(); }
          inp.focus();
        }, function () { inp.focus(); });
      });
    }
    on(inp, 'input', clear);
    on(f, 'submit', function (e) {
      e.preventDefault();
      var m = inp.value.trim().match(LOT_RX);
      if (!m) {
        inp.setAttribute('aria-invalid', 'true');
        if (err) { err.textContent = 'Нужна ссылка на объявление вида krisha.kz/a/show/1012607661'; err.classList.add('on'); }
        inp.focus();
        return;
      }
      if (f.getAttribute('aria-busy') === 'true') return;
      /* переход занимает время: показываем «Открываем…», а не молчим */
      var b = f.querySelector('.gobtn'), pb = f.querySelector('[data-paste]');
      if (b) { b.dataset.l0 = b.innerHTML; b.innerHTML = '<i class="spin" aria-hidden="true"></i>Открываем…'; b.disabled = true; }
      if (pb) pb.disabled = true;
      f.setAttribute('aria-busy', 'true');
      location.href='/#check=' + m[1];
    });
    /* возврат кнопкой «назад» из кэша страницы: форма снова рабочая */
    on(window, 'pageshow', function (ev) {
      if (!ev.persisted) return;
      var b = f.querySelector('.gobtn'), pb = f.querySelector('[data-paste]');
      if (b && b.dataset.l0 != null) { b.innerHTML = b.dataset.l0; delete b.dataset.l0; b.disabled = false; }
      if (pb) pb.disabled = false;
      f.setAttribute('aria-busy', 'false');
    });
  });

  /* ------------------------------------------------------------ вопросы-аккордеоны (.qa) */
  /* Без JS ответы открыты (html.js .a{height:0} — в стилях страницы). Закрытый ответ inert:
     его ссылки не ловят фокус. Высота анимируется CSS-переходом, при «меньше движения» — сразу. */
  (function () {
    var items = $$('.qa');
    if (!items.length) return;
    function height(pane, open) {
      if (still()) { pane.style.height = open ? 'auto' : '0px'; return; }
      var from = pane.getBoundingClientRect().height, to = open ? pane.scrollHeight : 0;
      pane.style.transition = 'none';
      pane.style.height = from + 'px';
      void pane.offsetHeight;
      pane.style.transition = 'height .42s cubic-bezier(.4,0,.2,1)';
      pane.style.height = to + 'px';
      var done = function (e) {
        if (e && e.target !== pane) return;
        pane.removeEventListener('transitionend', done);
        if (pane.parentNode.classList.contains('open')) pane.style.height = 'auto';
      };
      on(pane, 'transitionend', done);
    }
    function shut(qa) {
      var btn = qa.querySelector('.q'), pane = qa.querySelector('.a');
      qa.classList.remove('open');
      btn.setAttribute('aria-expanded', 'false');
      pane.inert = true;
      height(pane, false);
    }
    items.forEach(function (qa, i) {
      var btn = qa.querySelector('.q'), pane = qa.querySelector('.a');
      if (!btn || !pane) return;
      if (!pane.id) pane.id = 'qa' + i;
      if (!btn.id) btn.id = 'qb' + i;
      btn.setAttribute('aria-controls', pane.id);
      pane.setAttribute('role', 'region');
      pane.setAttribute('aria-labelledby', btn.id);
      var isOpen = qa.classList.contains('open');
      btn.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
      pane.inert = !isOpen;
      if (isOpen) pane.style.height = 'auto';
      on(btn, 'click', function () {
        var was = qa.classList.contains('open');
        $$('.qa.open').forEach(shut);
        if (!was) {
          qa.classList.add('open');
          btn.setAttribute('aria-expanded', 'true');
          pane.inert = false;
          height(pane, true);
        }
      });
    });
  })();

  /* ------------------------------------------------------------ нет сети */
  (function () {
    /* пропала сеть — говорим об этом, а не молчим */
    var bar = d.createElement('div');
    bar.className = 'offbar';
    bar.setAttribute('role', 'status');
    bar.innerHTML = '<i></i>Нет сети. Показываем последние сохранённые цифры';
    d.body.appendChild(bar);
    function net() { bar.classList.toggle('on', navigator.onLine === false); }
    on(w, 'online', net);
    on(w, 'offline', net);
    net();
  })();

  paintTheme();

  /* ------------------------------------------------------------ очередь bagam.ready */
  B.ready = function (fn) { run(function () { fn(B); }); };
  B.q = [];
  queue.forEach(B.ready);
})(window, document);
