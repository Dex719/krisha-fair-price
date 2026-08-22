"""Тесты issue #152: выбор режима по backlog'у, тайминги из истории,
оценка прохода, подрезка потолка и второй проход планировщика — чистые
функции pass_plan/shard_plan, без I/O."""

import pytest

from krisha.scraping.pass_plan import (
    DRAIN_ENTER_BACKLOG,
    DRAIN_EXIT_BACKLOG,
    DRAIN_MODE,
    FALLBACK_NEW_INFLOW,
    FALLBACK_OVERHEAD_S,
    FALLBACK_SEARCH_PAGES,
    MAX_MEAN_RPS,
    MAX_REQUESTS_PER_PASS,
    STEADY_MODE,
    PhaseTimings,
    choose_mode,
    estimate_new_inflow,
    estimate_timings,
    fit_detail_caps,
    plan_pass,
)
from krisha.scraping.shard_plan import redistribute_leftover

# ---------- выбор режима по состоянию базы, а не по флагу ----------


def test_choose_mode_above_enter_threshold_is_drain():
    mode, reason = choose_mode(DRAIN_ENTER_BACKLOG, prev_mode="steady")
    assert mode is DRAIN_MODE
    assert str(DRAIN_ENTER_BACKLOG) in reason


def test_choose_mode_below_exit_threshold_is_steady():
    mode, reason = choose_mode(DRAIN_EXIT_BACKLOG - 1, prev_mode="drain")
    assert mode is STEADY_MODE
    assert str(DRAIN_EXIT_BACKLOG - 1) in reason


def test_choose_mode_hysteresis_keeps_previous_mode():
    mid = (DRAIN_ENTER_BACKLOG + DRAIN_EXIT_BACKLOG) // 2
    # В полосе гистерезиса — держим прежний режим: приток на проде (2–3.4k/сут)
    # больше steady-дренажа (1500/сут), при едином пороге режим дребезжал бы
    # через день.
    assert choose_mode(mid, prev_mode="drain")[0] is DRAIN_MODE
    assert choose_mode(mid, prev_mode="steady")[0] is STEADY_MODE


def test_choose_mode_cold_start_in_hysteresis_defaults_to_steady():
    """Истории режима нет (свежая база), backlog в полосе — не разгоняемся
    без явного превышения верхнего порога."""
    mid = (DRAIN_ENTER_BACKLOG + DRAIN_EXIT_BACKLOG) // 2
    assert choose_mode(mid, prev_mode=None)[0] is STEADY_MODE


def test_mode_presets_match_issue_parameters():
    """Числа из issue #152 — не отклоняться без отдельного решения."""
    assert DRAIN_MODE.delay_range == (1.5, 3.0)
    assert (DRAIN_MODE.max_new, DRAIN_MODE.max_refresh) == (4500, 800)
    assert STEADY_MODE.delay_range == (2.0, 4.0)
    assert (STEADY_MODE.max_new, STEADY_MODE.max_refresh) == (1500, 1200)
    assert STEADY_MODE.refresh_stale_days == 45


# ---------- тайминги из истории sweep_runs ----------


def _run(**kw):
    base = {
        "search_pages": 2000, "search_seconds": 10_000.0,
        "detail_requests": 1000, "detail_seconds": 5_000.0,
        "delay_lo": 2.5, "delay_hi": 5.0,  # средняя пауза 3.75
    }
    base.update(kw)
    return base


def test_estimate_timings_separates_overhead_from_delay():
    """Навершие = сек/запрос − средняя пауза ТОГО прохода: пауза — свойство
    текущего режима, навершие — свойство сервера. Полное время из истории
    одного режима соврёт для другого."""
    runs = [_run(), _run(search_seconds=12_000.0, detail_seconds=6_000.0)]
    t = estimate_timings(runs)
    # первая строка: 10000/2000 − 3.75 = 1.25; вторая: 6/стр − 3.75 = 2.25
    assert t.overhead_search == pytest.approx(1.75)  # медиана 1.25/2.25
    assert t.overhead_detail == pytest.approx(1.75)
    assert t.search_pages == 2000
    assert t.samples == 2


def test_estimate_timings_skips_unmeasured_and_degenerate_rows():
    runs = [
        _run(search_seconds=None),          # до мержа #152 — таймингов нет
        _run(detail_requests=0),            # убитый до деталей проход
        _run(delay_lo=None, delay_hi=None),  # странно, но пропускаем
        _run(),
    ]
    t = estimate_timings(runs)
    # search: валидны 2-я и 4-я строки; detail: 1-я и 4-я — обе медианы 1.25
    assert t.samples == 2
    assert t.overhead_search == pytest.approx(1.25)
    assert t.overhead_detail == pytest.approx(1.25)


def test_estimate_timings_fallbacks_on_empty_history():
    t = estimate_timings([])
    assert t.samples == 0
    assert t.overhead_search == FALLBACK_OVERHEAD_S
    assert t.overhead_detail == FALLBACK_OVERHEAD_S
    assert t.search_pages == FALLBACK_SEARCH_PAGES


def test_estimate_new_inflow_median_and_fallback():
    assert estimate_new_inflow([]) == FALLBACK_NEW_INFLOW
    runs = [{"discovered_new": 100}, {"discovered_new": 300}, {"discovered_new": None}]
    assert estimate_new_inflow(runs) == 200


# ---------- оценка прохода до его начала ----------


def test_plan_pass_fits_and_not_fits():
    timings = PhaseTimings(overhead_search=0.5, overhead_detail=0.5,
                           search_pages=2100, samples=3)
    # drain: 2100 стр × 2.75 + 5300 × 2.75 ≈ 337 мин > 320 → не влезает
    plan = plan_pass(4500, 800, timings, 320.0, (1.5, 3.0))
    assert plan.est_requests == 2100 + 5300
    assert plan.fits is False
    # steady: 2100 × 3.5 + 2700 × 3.5 ≈ 280 мин < 320 → влезает
    plan = plan_pass(1500, 1200, timings, 320.0, (2.0, 4.0))
    assert plan.fits is True
    assert plan.mean_rps == pytest.approx(plan.est_requests / plan.est_seconds)


# ---------- подрезка потолка до фазы докачки ----------


def test_fit_no_trim_when_everything_fits():
    fit = fit_detail_caps(
        4500, 800,
        requests_so_far=2100, elapsed_s=1_000.0, budget_s=19_200.0, t_detail=2.7,
    )
    assert (fit.max_new, fit.max_refresh) == (4500, 800)
    assert fit.trimmed is False and fit.reason is None


def test_fit_trims_by_time_proportionally():
    """План не влезает по времени — потолок урезан ЗАРАНЕЕ, сплит
    пропорциональный (4500:800), не нулением одной стороны."""
    # Осталось ~1000 с: при 2.5 с/запрос влезает 400 из 5300.
    fit = fit_detail_caps(
        4500, 800,
        requests_so_far=2100, elapsed_s=18_200.0 - 300.0, budget_s=19_200.0,
        t_detail=2.5,
    )
    assert fit.trimmed is True and fit.reason == "time"
    assert fit.budget_requests == fit.max_new + fit.max_refresh == 400
    assert fit.max_new / fit.budget_requests == pytest.approx(4500 / 5300, abs=0.02)


def test_fit_trims_by_politeness_ceiling():
    """Потолок вежливости константой: 10k/сутки и 0.5 rps. При большом бюджете
    времени binding становится rps-потолок (0.5 × 19 200 = 9 600 < 10 000)."""
    fit = fit_detail_caps(
        20_000, 0,
        requests_so_far=2100, elapsed_s=0.0, budget_s=19_200.0, t_detail=0.5,
    )
    assert fit.trimmed is True and fit.reason == "politeness"
    politeness_total = min(MAX_REQUESTS_PER_PASS, int(MAX_MEAN_RPS * 19_200.0))
    assert fit.budget_requests == politeness_total - 2100


def test_fit_t_detail_floored_at_max_mean_rps():
    """Ревью #170: цена запроса зажата снизу 1/MAX_MEAN_RPS. Без пола
    оптимистичная оценка навершия (0.8 с при задавленных оверрайдом паузах)
    давала бы план ~23k запросов за проход — темп ~1.2 rps при формально
    соблюдённом счётчике 10 000: «0.5 rps» держалось на пресетах, а не на
    коде. С полом быстрее одного запроса за 2 с планировать нельзя."""
    fit = fit_detail_caps(
        20_000, 0,
        requests_so_far=32, elapsed_s=1.0, budget_s=19_200.0, t_detail=0.8,
    )
    assert fit.trimmed is True and fit.reason == "time"
    # (19200 − 1 − 300 резерв) // 2 = 9449, а не // 0.8 = 23 623
    assert fit.budget_requests == (19_200 - 1 - 300) // 2
    assert fit.max_new == fit.budget_requests


def test_fit_zero_when_budget_already_eaten():
    fit = fit_detail_caps(
        4500, 800,
        requests_so_far=2100, elapsed_s=19_000.0, budget_s=19_200.0, t_detail=2.5,
    )
    assert (fit.max_new, fit.max_refresh, fit.budget_requests) == (0, 0, 0)
    assert fit.trimmed is True


def test_fit_zero_wants_is_noop():
    fit = fit_detail_caps(0, 0, requests_so_far=0, elapsed_s=0.0,
                          budget_s=19_200.0, t_detail=2.5)
    assert fit.trimmed is False and fit.budget_requests == 0


def test_fit_reserve_scales_down_on_tiny_budgets():
    """Фиксированные 300 с резерва обнуляли бы диагностический бюджет в
    6 минут — резерв зажат долей бюджета (10%)."""
    fit = fit_detail_caps(
        8, 0,
        requests_so_far=32, elapsed_s=192.0, budget_s=360.0, t_detail=3.6,
    )
    # с полными 300 с резерва влезло бы 0; с 36 с — (360−192−36)//3.6 = 36
    assert fit.trimmed is False


# ---------- второй проход планировщика: остаток квоты ----------


def test_redistribute_leftover_to_deep_measured_shards():
    """Приёмочный критерий: остаток от шарда с мелким backlog'ом уходит
    замеренному шарду с backlog'ом глубже квоты, пропорционально стоку."""
    quotas = {"A": 40, "B": 40, "C": 20}
    backlog = {"A": 10, "B": 100, "C": 100}  # A сжигает 30 впустую
    stock = {"A": 400, "B": 400, "C": 200}   # все замерены
    extra = redistribute_leftover(quotas, backlog, stock)
    assert extra["A"] == 0                      # донор ничего не получает
    # B и C глубокие: 30 делятся 2:1 по стоку
    assert extra["B"] == 20 and extra["C"] == 10
    # суммарный потолок сохранён: недобор A роздан полностью
    assert sum(extra.values()) == 30


def test_redistribute_leftover_unmeasured_gets_nothing():
    """Приёмочный критерий: сбойному/незамеренному шарду — ничего (ни квоты
    второго прохода): у него нет строки стока для сверки batch-TVD."""
    quotas = {"A": 40, "B": 40, "C": 20}
    backlog = {"A": 10, "B": 100, "C": 100}
    stock = {"A": 400, "B": 400, "C": None}  # C не покрыт этим проходом
    extra = redistribute_leftover(quotas, backlog, stock)
    assert extra["C"] == 0
    assert extra["B"] == 30  # весь остаток единственному замеренному глубокому


def test_redistribute_leftover_from_unmeasured_donor_stays():
    """Инвариант #166 сохраняется: перераспределяется остаток только
    успешно спланированных (замеренных) шардов. Квота упавшего шарда (по
    фолбэк-стоку) и её невыданный остаток остаются при нём — компенсируются
    его курсором в следующих проходах."""
    quotas = {"A": 40, "B": 40}
    backlog = {"A": 5, "B": 100}      # у A мелкий backlog...
    stock = {"A": None, "B": 400}     # ...но A НЕ замерен (упал)
    extra = redistribute_leftover(quotas, backlog, stock)
    assert extra["B"] == 0  # остаток A (35) НЕ перераспределён
    assert sum(extra.values()) == 0


def test_redistribute_leftover_respects_recipient_depth():
    """Получатель не берёт больше, чем его backlog глубже квоты: перелив
    возвращается в остаток и перераздаётся следующей итерацией."""
    quotas = {"A": 40, "B": 40, "C": 40}
    backlog = {"A": 0, "B": 45, "C": 200}  # B принять может лишь 5
    stock = {"A": 400, "B": 400, "C": 400}
    extra = redistribute_leftover(quotas, backlog, stock)
    assert extra["B"] == 5          # кэп по глубине
    assert extra["C"] == 35         # перелив от B ушёл C итерацией
    assert sum(extra.values()) == 40


def test_redistribute_leftover_deterministic_and_idempotent_shape():
    quotas = {"A": 10, "B": 10, "C": 10}
    backlog = {"A": 3, "B": 50, "C": 50}
    stock = {"A": 100, "B": 100, "C": 100}
    first = redistribute_leftover(quotas, backlog, stock)
    second = redistribute_leftover(quotas, backlog, stock)
    assert first == second  # детерминизм: план воспроизводим от прогона к прогону
    assert sum(first.values()) == 7


def test_redistribute_leftover_no_leftover_or_no_recipients():
    assert redistribute_leftover({"A": 5}, {"A": 10}, {"A": 100}) == {"A": 0}
    # все замеренные шарды мелкие — остатку некуда деться, он честно сгорает
    extra = redistribute_leftover({"A": 5, "B": 5}, {"A": 2, "B": 3},
                                  {"A": 100, "B": 100})
    assert extra == {"A": 0, "B": 0}
