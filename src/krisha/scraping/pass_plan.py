"""issue #152: режим разгона сбора и бюджет прохода — считать, а не угадывать.

Двенадцать дней подряд докачка упиралась в потолок `--max-new 1000`, и это
выглядело как успех: отставание от выдачи (31.7k лотов backlog'а на 02.08)
было невидимым, потому что потолок задавался руками в воркфлоу и нигде не
сверялся ни с состоянием базы, ни с бюджетом времени прохода.

Здесь — чистые функции без I/O, чтобы тестировать отдельно от прохода:

- `DRAIN_MODE` / `STEADY_MODE` — два явных набора параметров: разгребание
  backlog'а и поддержание покрытия. Режим разгребания — не разовый запуск
  руками, а состояние, в котором проход живёт, пока backlog не опустится
  ниже порога;
- `choose_mode` — выбор режима ПО СОСТОЯНИЮ БАЗЫ (backlog), а не по флагу
  запуска и не по календарю;
- `estimate_timings` — фактические тайминги фаз из истории sweep_runs
  (реальная длительность страницы выдачи и детальной страницы измерена
  прошлыми проходами, а не арифметикой из текста issue);
- `plan_pass` — оценка прохода до его начала: сколько запросов и минут
  ожидается, укладываемся ли в мягкий дедлайн;
- `fit_detail_caps` — потолки докачки, урезанные заранее под фактический
  остаток бюджета и потолок вежливости, а не обнаруженные дедлайном
  на середине очереди.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

# Потолок вежливости — константой в коде, а не только в голове (issue #152):
# < 10 000 запросов в сутки и < 0.5 rps в среднем. Строго говоря, проходов
# ДВА в сутки с одного IP раннера (продажа и аренда), и каждый считает свои
# 10 000 независимо — формальный суммарный максимум 20 000/сутки с IP. Это
# сознательно оставленная неточность (ревью #170): проходы разнесены по
# времени, аренда мала (~5k лотов), а агрегированный темп всё равно держат
# паузы пресетов и rps-пол в fit_detail_caps — 10k/проход здесь защита от
# разгона ручного оверрайда, а не точный суточный бюджет IP. Оба ограничения
# почти совпадают при бюджете 320 мин (0.5 rps × 19200 с = 9600), но при
# ужатом --time-budget-min rps-потолок становится binding первым — проверять
# надо оба, и именно из констант, а не из пересчёта в уме.
MAX_REQUESTS_PER_PASS = 10_000
MAX_MEAN_RPS = 0.5

# Пороги перевода режима по backlog'у (лоты с sighting, но без деталей).
# Гистерезис, а не один порог: органический приток на проде 02.08 — 2.0–3.4k
# новых id в сутки (замер по discovered_new в sweep_runs), а steady-пресет
# дренирует 1500/сутки — при единственном пороге режим дребезжал бы через
# день. Разносим: в разгон входим при backlog ≥ 5000 (≈2 дня притока —
# остаток, который steady точно не удержит), выходим при < 3000 (дальше
# steady дожимает сам: 1500/сутки против притока, плюс backlog тают
# delist'ом недокачанных). Между порогами — держим прежний режим.
DRAIN_ENTER_BACKLOG = 5_000
DRAIN_EXIT_BACKLOG = 3_000

# Запас на пост-обработку прохода (апдейты last_seen/цен, delist, запись
# итогов) внутри мягкого бюджета: план и подрезка считаются по фазам
# запросов, а после них проходу ещё нужно пара минут работы с базой.
# Заливка базы в релиз в бюджет НЕ входит — её страхует зазор
# --time-budget-min 320 против timeout-minutes 350 в воркфлоу.
# Зажат сверху долей бюджета (см. _reserve): малые диагностические бюджеты
# (#152 делает такие прогоны частым инструментом) фиксированные 300 с
# обнуляли бы целиком.
RESERVE_SECONDS = 300.0


def _reserve(budget_s: float, reserve_s: float = RESERVE_SECONDS) -> float:
    """Запас на пост-обработку, но не более 10% бюджета."""
    return min(reserve_s, budget_s / 10)

# Фолбэк-навершия над паузой (latency + обработка ответа) на один запрос,
# пока в sweep_runs нет замеренных таймингов (первые проходы после мержа).
# Откалибровано по телеметрии прода 30.07–02.08 (summary-json снапшотов):
# latency p50 352–446 мс + разбор HTML ≈ 0.45–0.6 с. После ~3 проходов
# медиана истории заменяет константу (см. estimate_timings).
FALLBACK_OVERHEAD_S = 0.6
# Типичное число страниц выдачи за проход — замер прода 30.07–02.08:
# ~3400 запросов всего, из них ~1300 детали → ~2115 страниц.
FALLBACK_SEARCH_PAGES = 2_115
# Типичный суточный приток НОВЫХ id — замер прода 28.07–02.08 по
# discovered_new в sweep_runs: 2039–3378 (комментарий «~850 в день» в
# rescrape устарел — он про сезон до восстановительной волны).
FALLBACK_NEW_INFLOW = 2_000

# Два подряд прохода с BanDetected → возврат к steady-задержкам (issue #152:
# «разгон, который поймал бан и продолжает разгоняться, хуже отсутствия
# разгона: потеряем и день, и IP»). Один бан — эпизод (проход и так прерван
# early-abort'ом); два подряд — IP/отпечаток под наблюдением, темп снижаем
# сами, не дожидаясь третьего. Потолки докачки при этом не трогаем: с
# возвращёнными steady-задержками их урежет fit_detail_caps по бюджету
# времени — одна точка правды про «сколько влезает».
BAN_ROLLBACK_STREAK = 2


@dataclass(frozen=True)
class SweepMode:
    """Явный набор параметров режима прохода (issue #152).

    delay_range — паузы PoliteClient; max_new/max_refresh — потолки докачки
    новых и устаревших деталей; refresh_stale_days — возраст деталей, после
    которого лот встаёт в очередь обновления. В steady refresh щедрее
    (1200/45): покрытие набрано, и качество данных (свежесть редактируемых
    полей) важнее скорости разгребания.
    """

    name: str
    delay_range: tuple[float, float]
    max_new: int
    max_refresh: int
    refresh_stale_days: int


# Разгон: верхняя граница, а не пожелание — задержку ниже 1.5 с не опускать
# (вежливость и риск бана), потолок выше 4500 не поднимать: при реальном
# времени запроса ≈ 2.7 с (замер прода) 4500+800 деталей + ~2115 страниц
# выдачи ≈ 333 мин > бюджета 320 — хвост сам урежет fit_detail_caps.
DRAIN_MODE = SweepMode("drain", (1.5, 3.0), 4500, 800, 30)
STEADY_MODE = SweepMode("steady", (2.0, 4.0), 1500, 1200, 45)

_MODES = {m.name: m for m in (DRAIN_MODE, STEADY_MODE)}


def mode_by_name(name: str) -> SweepMode:
    """Пресет по имени; ValueError с перечнем — чтобы CLI падал понятно."""
    try:
        return _MODES[name]
    except KeyError:
        raise ValueError(
            f"неизвестный режим {name!r} — допустимые: {sorted(_MODES)}"
        ) from None


def choose_mode(
    backlog: int,
    prev_mode: str | None,
    enter: int = DRAIN_ENTER_BACKLOG,
    exit: int = DRAIN_EXIT_BACKLOG,
) -> tuple[SweepMode, str]:
    """Режим прохода по состоянию базы. Возвращает (пресет, причину для лога).

    Решение принимается в начале прохода и уезжает в итоги (stats["mode"] /
    stats["mode_reason"]) — из лога должно быть видно, в каком режиме шёл
    проход и почему. prev_mode — режим прошлого прохода из sweep_runs
    (гистерезис между порогами); None (истории нет) между порогами → steady:
    неизвестное состояние разгоняем только по явному превышению верхнего
    порога.
    """
    if backlog >= enter:
        return DRAIN_MODE, f"backlog {backlog} ≥ порога разгона {enter}"
    if backlog < exit:
        return STEADY_MODE, f"backlog {backlog} < порога выхода {exit}"
    if prev_mode == DRAIN_MODE.name:
        return DRAIN_MODE, (
            f"backlog {backlog} в гистерезисе [{exit}..{enter}), "
            "продолжаем разгон по прошлому проходу"
        )
    return STEADY_MODE, (
        f"backlog {backlog} в гистерезисе [{exit}..{enter}), steady по умолчанию"
    )


@dataclass(frozen=True)
class PhaseTimings:
    """Замеренные тайминги прохода для оценки (медианы по истории sweep_runs).

    overhead_* — секунды СВЕРХ средней паузы клиента на один запрос фазы
    (latency сервера + разбор ответа). Хранить надо именно навершие, а не
    полное время запроса: пауза — свойство ТЕКУЩЕГО режима (drain 2.25 с
    против steady 3.0 с), а навершие — свойство сервера; полное время из
    истории одного режима систематически соврёт для другого. samples=0 —
    истории нет, значения фолбэчные.
    """

    overhead_search: float
    overhead_detail: float
    search_pages: int
    samples: int


def _median_overhead(
    runs: list[dict], seconds_key: str, requests_key: str
) -> tuple[float | None, int]:
    """Медиана (сек/запрос − средняя пауза того прохода) по валидным строкам.

    Строки без замера (тайминги появились в #152 — история до мержа NULL)
    и вырожденные (0 запросов/секунд — убитые проходы) пропускаем. Навершие
    физически неотрицательно, но шум замера может дать около-нулевые значения
    — не фильтруем по знаку, медиана устойчива; нижний зажим — у вызывающего
    (см. estimate_timings).
    """
    samples = []
    for r in runs:
        seconds, requests = r.get(seconds_key), r.get(requests_key)
        lo, hi = r.get("delay_lo"), r.get("delay_hi")
        if not seconds or not requests or lo is None or hi is None:
            continue
        samples.append(seconds / requests - (lo + hi) / 2)
    if not samples:
        return None, 0
    return statistics.median(samples), len(samples)


def estimate_timings(runs: list[dict]) -> PhaseTimings:
    """Тайминги из истории sweep_runs (свежие первыми), фолбэки при пустой.

    Навершие зажато снизу 0.1 с: физически оно положительно (latency сети
    раннер→krisha.kz ненулевое), а около-нулевая оценка раздула бы план
    запросов ровно в том месте, где нужен запас.
    """
    oh_search, n1 = _median_overhead(runs, "search_seconds", "search_pages")
    oh_detail, n2 = _median_overhead(runs, "detail_seconds", "detail_requests")
    pages = [
        r["search_pages"] for r in runs
        if r.get("search_pages") and r.get("search_seconds")
    ]
    return PhaseTimings(
        overhead_search=max(0.1, oh_search) if oh_search is not None else FALLBACK_OVERHEAD_S,
        overhead_detail=max(0.1, oh_detail) if oh_detail is not None else FALLBACK_OVERHEAD_S,
        search_pages=int(statistics.median(pages)) if pages else FALLBACK_SEARCH_PAGES,
        samples=min(n1, n2) if (n1 or n2) else 0,
    )


def estimate_new_inflow(runs: list[dict]) -> int:
    """Типичный приток новых id за проход — медиана discovered_new истории.

    Нужен для оценки ДО прохода: очередь докачки к фазе деталей = старый
    backlog + свежие сайтинги этого прохода, и оценивать её одним backlog'ом
    значило бы занижать план ровно на органику (а на пустой истории —
    занулять: свежие находки составляют весь первый проход).
    """
    values = [r["discovered_new"] for r in runs if r.get("discovered_new") is not None]
    return int(statistics.median(values)) if values else FALLBACK_NEW_INFLOW


@dataclass(frozen=True)
class PassPlan:
    """Оценка прохода до его начала — печатается в лог (issue #152)."""

    est_search_pages: int
    est_detail_requests: int
    est_requests: int
    est_seconds: float
    budget_seconds: float
    fits: bool
    mean_rps: float


def plan_pass(
    max_new: int,
    max_refresh: int,
    timings: PhaseTimings,
    time_budget_min: float,
    delay_range: tuple[float, float],
    reserve_s: float = RESERVE_SECONDS,
) -> PassPlan:
    """Сколько запросов/минут ожидается при текущих параметрах и таймингах.

    max_new/max_refresh — уже ограниченные реальными очередями (не бюджет
    на пустую очередь): потолок 1200 при нулевой очереди refresh не должен
    раздувать оценку. Средняя пауза берётся из ФАКТИЧЕСКОГО delay_range
    прохода (режим мог быть переопределён env/откатом по бану), а не из
    пресета — иначе план откатившегося прохода считался бы по разгонным
    паузам.
    """
    avg_delay = (delay_range[0] + delay_range[1]) / 2
    budget_s = time_budget_min * 60.0
    est_search_s = timings.search_pages * (avg_delay + timings.overhead_search)
    est_details = max_new + max_refresh
    est_detail_s = est_details * (avg_delay + timings.overhead_detail)
    est_requests = timings.search_pages + est_details
    est_s = est_search_s + est_detail_s
    return PassPlan(
        est_search_pages=timings.search_pages,
        est_detail_requests=est_details,
        est_requests=est_requests,
        est_seconds=est_s,
        budget_seconds=budget_s,
        fits=est_s <= budget_s - _reserve(budget_s, reserve_s),
        mean_rps=est_requests / est_s if est_s > 0 else 0.0,
    )


@dataclass(frozen=True)
class FitResult:
    """Потолки докачки после подрезки под бюджет/вежливость."""

    max_new: int
    max_refresh: int
    trimmed: bool
    reason: str | None  # "time" | "politeness" | None
    budget_requests: int  # сколько детальных запросов влезло


def fit_detail_caps(
    want_new: int,
    want_refresh: int,
    *,
    requests_so_far: int,
    elapsed_s: float,
    budget_s: float,
    t_detail: float,
    reserve_s: float = RESERVE_SECONDS,
) -> FitResult:
    """Потолок докачки, который проход реально выдержит (issue #152).

    Зовётся ПОСЛЕ фазы выдачи, до входа в фазу докачки: фактическая цена
    выдачи (запросы и секунды) уже известна, и потолок режется заранее, а не
    обнаруживается дедлайном на середине очереди. Ограничения связанные:
    остаток мягкого бюджета (за вычетом запаса на пост-обработку) и потолок
    вежливости (MAX_REQUESTS_PER_PASS / MAX_MEAN_RPS от полного бюджета —
    выдача уже израсходовала часть).

    Сплит бюджета между new/refresh — пропорционально желаемому: соотношение
    пресета (у drain 4500:800 ≈ 85/15 — скорость разгребания; у steady
    1500:1200 — свежесть данных) это и есть намерение режима, резать одну
    сторону в ноль значило бы молча поменять режим.

    want_new/want_refresh — уже ограниченные реальными очередями вызывающим
    (потолок при пустой очереди не расходует бюджет).
    """
    want_total = want_new + want_refresh
    if want_total <= 0:
        return FitResult(0, 0, False, None, 0)
    # Пол 1/MAX_MEAN_RPS на цену запроса (ревью #170): оценка навершия может
    # соврать вниз (мало замеров, тёплый кэш), а паузы задавлены ручным
    # оверрайдом — тогда «по времени влезает» означало бы темп ВЫШЕ 0.5 rps
    # при соблюдённом счётчике 10 000. Пол делает потолок rps кодовым, а не
    # декларируемым: сколько бы ни стоил запрос по оценке, быстрее одного
    # за 2 с планировать нельзя.
    t_detail = max(t_detail, 1.0 / MAX_MEAN_RPS)
    by_time = max(
        0,
        int((budget_s - elapsed_s - _reserve(budget_s, reserve_s)) // t_detail),
    )
    politeness_total = min(MAX_REQUESTS_PER_PASS, int(MAX_MEAN_RPS * budget_s))
    by_politeness = max(0, politeness_total - requests_so_far)
    budget = min(want_total, by_time, by_politeness)
    if budget >= want_total:
        return FitResult(want_new, want_refresh, False, None, want_total)
    new = int(budget * want_new / want_total)
    refresh = budget - new
    reason = "time" if by_time <= by_politeness else "politeness"
    return FitResult(new, refresh, True, reason, budget)
