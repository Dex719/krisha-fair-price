"""issue #157: предупреждение «подозрительно дёшево» от границы интервала.

Раньше здесь была балльная система с порогами 12/20/30% от точечной оценки,
регулярками на «задаток/срочно», счётчиком фото и длиной описания. Она давала
иллюзию строгости: каждое слагаемое взято из головы, а сумма баллов разной
природы не означает ничего проверяемого. Плюс регулярки ловили честных
продавцов — слово «задаток» пишут и они.

Теперь порог не выдуман: нижняя граница интервала строится квантильной
моделью и калибруется CQR под фактическое покрытие 80%. «Ниже неё» — это
откалиброванное утверждение, а не произвольный процент.
"""

from krisha.scam import DEEP_BELOW_PCT, FRESH_DAYS, assess_scam_risk

FAIR_LOW = 40_000_000


def test_price_inside_interval_is_never_flagged():
    """Цена внутри интервала — рынок, а не аномалия.

    Даже у самой нижней границы: интервал на то и интервал, чтобы 10% честных
    квартир оказывались ниже точечной оценки без всякого умысла.
    """
    assert assess_scam_risk(FAIR_LOW, FAIR_LOW) is None
    assert assess_scam_risk(FAIR_LOW, FAIR_LOW + 1) is None
    assert assess_scam_risk(FAIR_LOW, 55_000_000) is None


def test_below_interval_is_medium_by_default():
    """Ниже границы — повод присмотреться, но не более: лот может просто
    висеть давно и быть уценён по понятной причине."""
    risk = assess_scam_risk(FAIR_LOW, 36_000_000, days_on_market=40)

    assert risk is not None and risk["level"] == "medium"
    assert risk["below_pct"] == 10.0
    assert any("ниже нижней границы" in r for r in risk["reasons"])


def test_deep_below_and_fresh_is_high():
    """Глубоко ниже границы И свежее — самое похожее на приманку.

    Объявление-приманка живёт недолго: его снимают по жалобам или после сбора
    предоплат. Поэтому свежесть повышает уровень.
    """
    price = FAIR_LOW * (1 - (DEEP_BELOW_PCT + 5) / 100)
    risk = assess_scam_risk(FAIR_LOW, price, days_on_market=1)

    assert risk is not None and risk["level"] == "high"
    assert any("свежее" in r for r in risk["reasons"])


def test_deep_below_but_long_on_market_stays_medium():
    """Тот же дисконт, но лот висит третью неделю — уровень не повышаем.

    Если бы это была приманка, её бы уже сняли. Скорее что-то другое:
    состояние, документы, неудачный дом.
    """
    price = FAIR_LOW * (1 - (DEEP_BELOW_PCT + 5) / 100)
    risk = assess_scam_risk(FAIR_LOW, price, days_on_market=FRESH_DAYS + 14)

    assert risk is not None and risk["level"] == "medium"


def test_unknown_days_on_market_does_not_raise_level():
    """Нет данных о сроке — не додумываем. Отсутствие сигнала это не сигнал."""
    price = FAIR_LOW * (1 - (DEEP_BELOW_PCT + 5) / 100)
    risk = assess_scam_risk(FAIR_LOW, price, days_on_market=None)

    assert risk is not None and risk["level"] == "medium"


def test_missing_inputs_return_none():
    assert assess_scam_risk(FAIR_LOW, None) is None
    assert assess_scam_risk(None, 30_000_000) is None
    assert assess_scam_risk(0, 30_000_000) is None
