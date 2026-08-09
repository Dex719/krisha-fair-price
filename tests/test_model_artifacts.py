"""Тесты реальных артефактов модели в models/ — согласованность meta ↔ модель ↔ история.

В отличие от остальных тестов (синтетика во tmp_path) здесь открываются
настоящие файлы репозитория: model.cbm, model_quantile.cbm, model_meta.json,
metrics_history.jsonl, model_gate_samples.json, spatial_ref.json. Цель —
поймать «разъехавшиеся» артефакты (модель перезаписана без меты, история
метрик побилась, квантили перепутаны), не завязываясь на точные значения
метрик: только инварианты и разумные диапазоны, чтобы еженедельный ретрейн
тесты не ронял. На свежем клоне без артефактов весь модуль скипается.
"""

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from catboost import CatBoostRegressor, Pool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import model_gate  # noqa: E402
MODELS_DIR = ROOT / "models"
MODEL_PATH = MODELS_DIR / "model.cbm"
META_PATH = MODELS_DIR / "model_meta.json"
QUANTILE_PATH = MODELS_DIR / "model_quantile.cbm"
HISTORY_PATH = MODELS_DIR / "metrics_history.jsonl"
GATE_SAMPLES_PATH = MODELS_DIR / "model_gate_samples.json"
SPATIAL_REF_PATH = MODELS_DIR / "spatial_ref.json"

pytestmark = pytest.mark.skipif(
    not (MODEL_PATH.exists() and META_PATH.exists()),
    reason="реальные артефакты models/ отсутствуют (свежий клон без модели)",
)

# Типовые листинги для sanity-предиктов: фичи в model_gate_samples.json не
# сохраняются (там только пары APE), поэтому вход строится кодом фичей.
# Последние два отличаются ТОЛЬКО площадью — пара для теста монотонности.
SAMPLE_LISTINGS = [
    {
        "rooms": 1, "area": 38.0, "floor": 4, "total_floors": 5, "year_built": 1985,
        "lat": 43.255, "lon": 76.930, "district": "Almalinskiy_r-n", "photos_count": 6,
    },
    {
        "rooms": 2, "area": 60.0, "floor": 5, "total_floors": 9, "year_built": 2015,
        "lat": 43.238, "lon": 76.889, "district": "Bostandykskiy_r-n", "photos_count": 8,
    },
    {
        "rooms": 3, "area": 90.0, "floor": 7, "total_floors": 12, "year_built": 2020,
        "lat": 43.235, "lon": 76.955, "district": "Medeuskiy_r-n", "photos_count": 12,
    },
    {
        "rooms": 2, "area": 40.0, "floor": 3, "total_floors": 9, "year_built": 2010,
        "lat": 43.220, "lon": 76.850, "district": "Auezovskiy_r-n", "photos_count": 7,
    },
    {
        "rooms": 2, "area": 100.0, "floor": 3, "total_floors": 9, "year_built": 2010,
        "lat": 43.220, "lon": 76.850, "district": "Auezovskiy_r-n", "photos_count": 7,
    },
]
AREA_SMALL_IDX, AREA_LARGE_IDX = 3, 4

# Разумные рамки цены квартиры в Алматы: предикт вне их — признак сломанного
# артефакта (не тот таргет, не та шкала, перепутанные фичи).
PRICE_SANE_MIN = 5_000_000       # 5 млн ₸
PRICE_SANE_MAX = 2_000_000_000   # 2 млрд ₸


@pytest.fixture(scope="module")
def meta() -> dict:
    return json.loads(META_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def model() -> CatBoostRegressor:
    m = CatBoostRegressor()
    m.load_model(str(MODEL_PATH))
    return m


@pytest.fixture(scope="module")
def quantile_model() -> CatBoostRegressor:
    if not QUANTILE_PATH.exists():
        pytest.skip("model_quantile.cbm отсутствует — легальный legacy-режим (пара lo/hi)")
    q = CatBoostRegressor()
    q.load_model(str(QUANTILE_PATH))
    return q


@pytest.fixture(scope="module")
def sample_pool(meta) -> Pool:
    """Типовые листинги через боевой код фичей (listing_to_frame + ppsm_maps + spatial_ref)."""
    from krisha.features import listing_to_frame
    from krisha.spatial import load_spatial_ref

    spatial_ref = load_spatial_ref()
    frames = [
        listing_to_frame(listing, ppsm_maps=meta.get("ppsm_maps"), spatial_ref=spatial_ref)
        for listing in SAMPLE_LISTINGS
    ]
    df = pd.concat(frames, ignore_index=True)
    return Pool(df[meta["features"]], cat_features=meta["cat_features"])


# --- Контракт model_meta.json ----------------------------------------------


def test_meta_required_keys_and_types(meta):
    """Обязательные ключи меты: метрики конечны, счётчики положительны,
    trained_at — ISO-дата не из будущего, cat_features ⊆ features."""
    metrics = meta["metrics"]
    for section in ("model", "baseline"):
        for key in ("mae", "mape", "r2"):
            val = metrics[section][key]
            assert isinstance(val, (int, float)) and math.isfinite(val), (section, key, val)
    assert metrics["model"]["mae"] > 0
    assert 0 < metrics["model"]["mape"] < 1
    assert metrics["model"]["r2"] <= 1
    assert metrics["n_train"] > 0
    assert metrics["n_test"] > 0

    trained_at = datetime.fromisoformat(metrics["trained_at"])
    assert trained_at.tzinfo is not None, "trained_at должен быть timezone-aware"
    assert trained_at <= datetime.now(timezone.utc) + timedelta(hours=1)

    assert meta["features"], "features пуст"
    assert all(isinstance(f, str) for f in meta["features"])
    assert set(meta["cat_features"]) <= set(meta["features"])

    interval = metrics["interval"]
    assert "target_coverage" in interval
    assert "coverage_test" in interval


def test_meta_features_match_code(meta):
    """Код фичей и артефакт не разъехались: meta['features'] == ALL_FEATURES
    (точный порядок — CatBoost чувствителен к нему), cat_features == CAT_FEATURES."""
    from krisha.features import ALL_FEATURES, CAT_FEATURES

    assert meta["features"] == ALL_FEATURES
    assert meta["cat_features"] == CAT_FEATURES


def test_meta_dedup_and_split_counters_consistent(meta):
    """Арифметика dedup-блока: before - dropped == after, dropped_pct согласован,
    train+test+purged не превышает строк после дедупа."""
    metrics = meta["metrics"]
    dedup = metrics["dedup"]
    assert dedup["rows_before"] - dedup["dropped"] == dedup["rows_after"]
    expected_pct = dedup["dropped"] / dedup["rows_before"] * 100
    assert dedup["dropped_pct"] == pytest.approx(expected_pct, abs=0.06)
    total = metrics["n_train"] + metrics["n_test"] + metrics["n_purged"]
    assert total <= dedup["rows_after"]


# --- Согласованность meta ↔ реальные модели --------------------------------


def test_model_feature_names_match_meta(model, meta):
    """model.cbm обучен ровно на meta['features']: и состав, и порядок."""
    assert model.feature_names_ == meta["features"]
    assert model.tree_count_ > 0


def test_quantile_model_consistent_with_main(model, quantile_model, meta):
    """Квантильная модель видит тот же вход, что и основная, а порядок квантилей
    в loss зафиксирован: predict.py жёстко считает столбец 0 = q10, 1 = q90."""
    assert quantile_model.feature_names_ == model.feature_names_
    assert quantile_model.feature_names_ == meta["features"]
    loss = str(quantile_model.get_params().get("loss_function"))
    assert loss == "MultiQuantile:alpha=0.1,0.9"


def test_load_interval_models_prefers_real_multiquantile(quantile_model):
    """Пока models/model_quantile.cbm существует, load_interval_models() обязан
    вернуть именно её, а не legacy-пару model_lo/model_hi."""
    import krisha.predict as predict_mod

    predict_mod.load_interval_models.cache_clear()
    try:
        loaded = predict_mod.load_interval_models()
        assert isinstance(loaded, CatBoostRegressor)
        assert not isinstance(loaded, predict_mod._LegacyQuantilePair)
    finally:
        predict_mod.load_interval_models.cache_clear()


# --- Качество: модель бьёт бейзлайн, CI согласован --------------------------


def test_model_beats_baseline(meta):
    """Модель обязана бить наивный бейзлайн по всем трём метрикам, а бутстреп-CI
    MAPE — накрывать точечную оценку."""
    metrics = meta["metrics"]
    m, b = metrics["model"], metrics["baseline"]
    assert m["mape"] < b["mape"]
    assert m["mae"] < b["mae"]
    assert m["r2"] > b["r2"]

    ci = metrics["model_mape_ci"]
    assert ci["lo"] <= ci["point"] <= ci["hi"]
    assert ci["point"] == pytest.approx(m["mape"], abs=1e-4)


def test_model_not_worse_than_old_within_gate_tolerance(meta):
    """Новая модель не хуже предыдущей в смысле реального гейта: при наличии
    model_gate_samples.json scripts/model_gate.py решает по нижней границе
    бутстреп-CI разницы средних APE (та же bootstrap_ape_diff и тот же допуск),
    и только без сэмплов — по плоскому допуску на точечной оценке. Если бы
    критерий гейта нарушался, model_gate.py не пустил бы модель в репо."""
    metrics = meta["metrics"]
    if "old_model" not in metrics:
        pytest.skip("первый трейн: old_model в мете нет")
    if GATE_SAMPLES_PATH.exists():
        samples = json.loads(GATE_SAMPLES_PATH.read_text(encoding="utf-8"))
        lower, _upper, _point = model_gate.bootstrap_ape_diff(
            samples["ape_new"], samples["ape_old"]
        )
        assert lower <= model_gate.BOOTSTRAP_TOLERANCE, (
            f"нижняя граница CI разницы APE (новая-старая) {lower:+.4f} "
            f"превышает допуск гейта {model_gate.BOOTSTRAP_TOLERANCE:+.4f}"
        )
    else:
        assert (
            metrics["model"]["mape"]
            <= metrics["old_model"]["mape"] + model_gate.MAPE_TOLERANCE
        )


# --- Контракт metrics_history.jsonl ----------------------------------------


def test_metrics_history_lines_valid_and_ordered():
    """Каждая строка — валидный JSON с полным набором ключей, trained_at
    парсится как ISO-дата и строго возрастает от строки к строке."""
    if not HISTORY_PATH.exists():
        pytest.skip("metrics_history.jsonl отсутствует")
    lines = [
        ln for ln in HISTORY_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    assert lines, "история метрик пуста"
    stamps = []
    for ln in lines:
        entry = json.loads(ln)
        for key in ("trained_at", "mae", "mape", "r2", "n_train", "n_test"):
            assert key in entry, f"в строке истории нет ключа {key}: {ln[:80]}"
        assert entry["mae"] > 0
        assert 0 < entry["mape"] < 1
        assert entry["n_train"] > 0 and entry["n_test"] > 0
        stamps.append(datetime.fromisoformat(entry["trained_at"]))
    for prev, cur in zip(stamps, stamps[1:]):
        assert prev < cur, "trained_at в истории не строго возрастает"


def test_metrics_history_last_entry_matches_meta(meta):
    """Последняя строка истории — про текущую модель: тот же trained_at,
    метрики совпадают с метой с точностью до округлений append_metrics_history."""
    if not HISTORY_PATH.exists():
        pytest.skip("metrics_history.jsonl отсутствует")
    lines = [
        ln for ln in HISTORY_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    last = json.loads(lines[-1])
    metrics = meta["metrics"]
    assert last["trained_at"] == metrics["trained_at"]
    assert last["mae"] == round(metrics["model"]["mae"])
    assert last["mape"] == round(metrics["model"]["mape"], 4)
    assert last["r2"] == round(metrics["model"]["r2"], 4)
    assert last["n_train"] == metrics["n_train"]
    assert last["n_test"] == metrics["n_test"]


# --- Контракт model_gate_samples.json --------------------------------------


def test_gate_samples_contract_and_consistency_with_meta(meta):
    """ape_new/ape_old — парные выборки размера n_test, значения конечны и >= 0,
    а их средние обязаны сходиться с mape в мете: это буквально та же выборка."""
    if not GATE_SAMPLES_PATH.exists():
        pytest.skip("model_gate_samples.json отсутствует")
    metrics = meta["metrics"]
    if "old_model" not in metrics:
        pytest.skip("первый трейн: gate samples не пишутся без старой модели")

    samples = json.loads(GATE_SAMPLES_PATH.read_text(encoding="utf-8"))
    assert set(samples.keys()) == {"ape_new", "ape_old"}
    ape_new = np.asarray(samples["ape_new"], dtype=float)
    ape_old = np.asarray(samples["ape_old"], dtype=float)
    assert len(ape_new) == len(ape_old) == metrics["n_test"]
    assert np.all(np.isfinite(ape_new)) and np.all(ape_new >= 0)
    assert np.all(np.isfinite(ape_old)) and np.all(ape_old >= 0)
    assert float(ape_new.mean()) == pytest.approx(metrics["model"]["mape"], rel=1e-6)
    assert float(ape_old.mean()) == pytest.approx(metrics["old_model"]["mape"], rel=1e-6)


# --- Sanity-предикты на реальной модели ------------------------------------


def test_sanity_predictions_are_plausible_almaty_prices(model, sample_pool):
    """Предикт основной модели на типовых листингах конечен и после expm1
    попадает в правдоподобный диапазон цен Алматы (5 млн – 2 млрд ₸)."""
    log_pred = model.predict(sample_pool)
    assert log_pred.shape == (len(SAMPLE_LISTINGS),)
    assert np.all(np.isfinite(log_pred))
    prices = np.expm1(log_pred)
    assert np.all(prices > 0)
    for listing, price in zip(SAMPLE_LISTINGS, prices):
        assert PRICE_SANE_MIN <= price <= PRICE_SANE_MAX, (listing["rooms"], listing["area"], price)


def test_bigger_area_costs_more_on_identical_listing(model, sample_pool):
    """Мягкая монотонность: одинаковый листинг с площадью 100 м² дороже, чем
    с 40 м². Без monotone constraints строгую монотонность по сетке не
    гарантируем — только грубую разницу на в 2.5 раза большей площади."""
    log_pred = model.predict(sample_pool)
    assert log_pred[AREA_LARGE_IDX] > log_pred[AREA_SMALL_IDX]


def test_quantile_predictions_ordered_and_interval_chain_valid(
    model, quantile_model, meta, sample_pool
):
    """Квантильный предикт: shape (n, 2), сырой q10 <= q90 построчно; после
    боевой цепочки _apply_cqr + expm1 + finalize_interval выполняется
    0 < fair_low <= fair_price <= fair_high."""
    from krisha.interval import finalize_interval
    from krisha.predict import _apply_cqr

    raw = quantile_model.predict(sample_pool)
    assert raw.shape == (len(SAMPLE_LISTINGS), 2)
    assert np.all(np.isfinite(raw))
    assert np.all(raw[:, 0] <= raw[:, 1]), "столбцы квантилей перепутаны: ожидаем 0=q10, 1=q90"

    interval_meta = meta["metrics"]["interval"]
    log_mid = model.predict(sample_pool)
    for i in range(len(SAMPLE_LISTINGS)):
        log_lo, log_hi = _apply_cqr(float(raw[i, 0]), float(raw[i, 1]), interval_meta)
        fair = float(np.expm1(log_mid[i]))
        low, high = finalize_interval(fair, float(np.expm1(log_lo)), float(np.expm1(log_hi)))
        assert 0 < low <= fair <= high, (i, low, fair, high)


# --- Интервал в мете --------------------------------------------------------


def test_interval_meta_sane(meta):
    """Интервальный блок меты: альфы согласованы с loss квантильной модели,
    cqr_scale в допустимых пределах, coverage_test в рамках гейта, ширина
    интервала положительна и не абсурдна."""
    from krisha.train import CQR_SCALE_MAX

    interval = meta["metrics"]["interval"]
    assert interval["alpha_lo"] == pytest.approx(0.1)
    assert interval["alpha_hi"] == pytest.approx(0.9)
    assert interval["target_coverage"] == pytest.approx(0.8)
    # Нижняя граница — ровно допуск гейта (coverage >= target - tolerance);
    # сверху гейт покрытие не ограничивает, < 1.0 — только sanity на вырожденный
    # интервал, чтобы легитимно-консервативный ретрейн тест не ронял.
    assert (
        interval["target_coverage"] - model_gate.COVERAGE_TOLERANCE
        <= interval["coverage_test"]
        < 1.0
    )
    assert 0 <= interval["cqr_scale"] <= CQR_SCALE_MAX
    assert 0 < interval["median_width_pct"] < 1


# --- Пространственные артефакты --------------------------------------------


def test_spatial_ref_contract():
    """spatial_ref.json: lat/lon/ppsm — непустые списки одной длины, координаты
    внутри бибокса Алматы, ₸/м² в допустимых пределах; hex-агрегаты непусты
    и содержат только конечные положительные медианы."""
    if not SPATIAL_REF_PATH.exists():
        pytest.skip("spatial_ref.json отсутствует")
    from krisha.config import ALMATY_BBOX, PPSM_MAX, PPSM_MIN

    ref = json.loads(SPATIAL_REF_PATH.read_text(encoding="utf-8"))
    assert set(ref.keys()) >= {"lat", "lon", "ppsm", "hex7", "hex8"}
    lat = np.asarray(ref["lat"], dtype=float)
    lon = np.asarray(ref["lon"], dtype=float)
    ppsm = np.asarray(ref["ppsm"], dtype=float)
    assert len(lat) == len(lon) == len(ppsm) > 0
    assert np.all((lat >= ALMATY_BBOX["lat_min"]) & (lat <= ALMATY_BBOX["lat_max"]))
    assert np.all((lon >= ALMATY_BBOX["lon_min"]) & (lon <= ALMATY_BBOX["lon_max"]))
    assert np.all((ppsm >= PPSM_MIN) & (ppsm <= PPSM_MAX))

    for hex_key in ("hex7", "hex8"):
        values = np.asarray(list(ref[hex_key].values()), dtype=float)
        assert len(values) > 0
        assert np.all(np.isfinite(values))
        assert np.all((values >= PPSM_MIN) & (values <= PPSM_MAX))


def test_ppsm_maps_in_meta_sane(meta):
    """ppsm_maps в мете: district/microdistrict/global на месте, все медианы
    ₸/м² в допустимых пределах — predict без них деградирует молча."""
    from krisha.config import PPSM_MAX, PPSM_MIN

    maps = meta["ppsm_maps"]
    assert set(maps.keys()) >= {"district", "microdistrict", "global"}
    assert PPSM_MIN <= maps["global"] <= PPSM_MAX
    for level in ("district", "microdistrict"):
        assert maps[level], f"ppsm_maps[{level}] пуст"
        for name, value in maps[level].items():
            assert PPSM_MIN <= value <= PPSM_MAX, (level, name, value)
