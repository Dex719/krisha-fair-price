# Модель

## Что предсказываем

Справедливую цену продажи квартиры в Алматы по признакам объявления. Точечная модель — CatBoost-регрессия на `log1p(price)` (`features.TARGET = "log_price"`), интервал — вторая CatBoost-модель с `MultiQuantile:alpha=0.10,0.90` плюс конформная калибровка (CQR). Вердикт — по интервалу: цена ниже нижней границы → `GOOD_DEAL`, выше верхней → `OVERPRICED`, внутри → `FAIR`. Порог ±10% от точки (`VERDICT_THRESHOLD`) остаётся фолбэком, когда интервальной модели нет.

Прод-числа [`models/model_meta.json`, ретрейн 2026-08-30]: MAPE 7.49% (95% ДИ 7.28–7.72), MdAPE 5.73%, MAE 3.89 млн ₸, R² 0.919; train 42 474 строк, test 4 889; бейзлайн «медиана ₸/м² по району × комнатам» — MAPE 15.8%. Живые значения — `/api/health`, в README их синхронизирует `scripts/sync_readme_metrics.py`.

## Фичи (`features.py`, 47 в модели, 13 категориальных)

| Группа | Фичи | Откуда |
|---|---|---|
| Квартира | `rooms`, `area`, `floor`, `total_floors`, `floor_ratio`, `is_first_floor`, `is_last_floor`, `ceiling`, `photos_count` | детальная страница |
| Дом | `year_built`, `building_age`, `is_new_building`, `building_type` | детальная страница; `category` (vtorichka/novostroiki) |
| Из `raw_params` | `renovation`, `toilet`, `furniture`, `parking`, `balcony` (категории, `RAW_PARAM_CAT_MAP`); `has_security_guard`, `has_intercom`, `has_video_surveillance`, `security_count` | `flat.*`/`live.*` параметры объявления |
| Локация | `lat`, `lon`, `dist_center_km` (до Абая/Достык), `district`, `microdistrict` | координаты; район/микрорайон чинятся по OSM-полигонам (`zones.py`) |
| Цена места | `district_ppsm`, `microdistrict_ppsm` (медианы ₸/м² с train, `ppsm_maps` в мете); `hex7_ppsm`, `hex8_ppsm` (H3 res 7 ≈ 5 км², res 8 ≈ 0.7 км², `spatial.py`, снапшот `spatial_ref.json`) | только train-часть — без утечки теста |
| OSM POI (`geo.py`) | `dist_metro_km`, `dist_school_km`, `dist_kindergarten_km`, `dist_park_km`, `dist_supermarket_km`, `dist_bus_stop_km`, `dist_big_road_km`, `dist_industrial_km`, `walk_score` | KD-дерево по `models/osm_pois.json` |
| ЖК (`complexes.py`) | `housing_class`, `developer`, `completion_year`, `apartments_count` | `models/complexes.json` по нормализованному имени |
| Продавец | `user_type` (owner/agent/company/complex), `complex_name` | детальная страница |

Категориальные признаки идут в CatBoost нативно, пропуски — `"unknown"`; числовые вне санитарных диапазонов (`TOTAL_FLOORS_RANGE`, `YEAR_BUILT_MIN`, `CEILING_RANGE`, `ROOMS_RANGE`) превращаются в NaN, а не клипаются: клип маскирует мусор под легитимное значение.

**Вычисляются, но в модель не идут** (`EXTRA_FEATURES`): `knn_ppsm`/`knn_n` (на train сосед — свой дом, на test нет: leakage-mismatch), `district_mismatch` (нулевой эффект, остался бейджем), `in_golden_square` и `otbasy_panel_excluded` (парный walk-forward AB на 3 сидах, авг 2026: эффект неотличим от шума; hex-фичи уже несут локальную цену). LLM-флаги описания убраны из фичей (issue #157) — проваливали абляцию и стоили SQL-запрос на каждом предикте. Возвращать любую из них — только через `scripts/backtest.py` с измеримым выигрышем.

## Обучающая выборка (`train.py`)

1. `load_dataset` — все лоты с деталями из `data/krisha.db`; фильтры `PRICE/AREA/PPSM_*`, bbox Алматы, снятые старше `STALE_DELISTED_DAYS = 90` дней выбрасываются (цена устарела).
2. `dedup_relistings` — перевыставления схлопываются по fingerprint (район, комнаты, площадь до 0.5 м², этаж/этажность, координаты ~10 м), дубль только при цене в пределах `DEDUP_PRICE_TOLERANCE = 2%`; остаётся самая свежая запись. На ретрейне 30.08 выкинуто 11.4% строк.
3. `time_based_split` (issue #104/#153) — тест = самые свежие по `first_seen`, целыми днями от свежих к старым, пока влезает в `TEST_MAX_FRACTION = 20%`; не меньше `TEST_MIN_FRACTION = 10%` и `TEST_MIN_ROWS = 500`, окно растягивается назад не дальше `MAX_TEST_LOOKBACK_DAYS = 45`. **Bulk-дни** (приток > `BULK_DAY_MULTIPLIER = 3×` медианы дневного и ≥ `BULK_ABS_MIN_ROWS = 1500`) целиком уходят в train: это разовая заливка, а не срез рынка, и она растёт задним числом по мере докачки деталей. Константа `TEST_WINDOW_DAYS = 14` — только для маленькой базы; на проде окно реально 22–25 дней, фактическое пишется в мету.
4. `purge_leaked_train_rows` — строки train с fingerprint, встречающимся в test, удаляются (472 на 30.08).
5. CatBoost RMSE: `learning_rate=0.05`, `depth=8`, до `--iterations 2000` с early stopping (100 раундов) по валидации → финальная модель на `best_iterations` (1997 на 30.08 — то есть упирается в потолок, стоит поднять).
6. Интервал: `MultiQuantile:alpha=0.10,0.90`, до 800 итераций с early stopping, обучается на fit-части, калибруется CQR на отложенной calib-части (35 915 / 6 559 на 30.08). `cqr_scale` и покрытие на тесте — в `metrics.interval`; цель 80%, факт 80.1%, медианная ширина 25% от цены. Финализация границ (своп при пересечении, растяжка под точку) — один код `interval.py` и в train, и в predict, иначе гейт мерил бы не тот интервал, который видит пользователь.
7. SHAP: глобальный отчёт `reports/shap_summary.png`; в предикте — top-факторы по SHAP-значениям с переводом в % и ₸ (`predict._with_money_impact`) и подсказками из живых медиан базы (`factor_hints.py`).

`--compare-old PATH` оценивает предыдущую модель на этом же тесте → `metrics.old_model` и пары APE в `models/model_gate_samples.json` для гейта.

## Честность метрик (`validity.py`, issue #158)

Три разных вопроса, три поля в мете:

- **Репрезентативность теста** — `test_representativeness`: TVD распределений района и комнат в тесте против всей базы; порог `MAX_TEST_TVD = 0.20`. На 30.08 TVD по районам 0.224 → `representative: false` (наследие сбора по районам по алфавиту).
- **Точность самой метрики** — `model_mape_ci`: кластерный бутстрэп по зданиям (кластер = здание, чтобы соседние квартиры не считались независимыми), 95% ДИ, 3 491 кластер.
- **Временна́я валидность** — `time_confounding`/`temporal_validity`: пока состав данных по дням меняется вместе с временем (worst day TVD 0.778), число описывает попадание по текущему стоку, а не экстраполяцию вперёд → `temporal_validity=false`, и это честно показано на `/about` и в `/api/health.model_temporal_validity`.

Плюс `scripts/backtest.py` — walk-forward стенд (6–8 недельных срезов назад по времени) для сравнения вариантов модели/таргета (`targets.py`: `price`, `ppsm`, `index_residual`); `scripts/compare_models.py` — все исторические чекпойнты на одном замороженном тесте (таблица в README).

## Гейт качества (`scripts/model_gate.py`, issue #106)

Запускается в `retrain.yml` после `train.py`. Новая модель публикуется, только если:

- MAPE не хуже старой: при наличии `model_gate_samples.json` — парный бутстрэп разницы средних APE (2000 повторов, нижняя граница 90% ДИ ≤ +0.5 п.п.), иначе плоский допуск `MAPE_TOLERANCE = 0.5 п.п.`;
- MAE не хуже более чем на `MAE_TOLERANCE_REL = 5%`;
- покрытие интервала не упало больше `COVERAGE_TOLERANCE = 0.02`, ширина не выросла больше `WIDTH_TOLERANCE_REL = 10%`;
- **fail-closed**: если `--compare-old` был, но старая модель не оценилась (`old_model_error`, обычно разошёлся набор фичей) — публикация блокируется, а не сравнивается вслепую.

Провал гейта → exit 1 → воркфлоу не коммитит `models/`, в проде остаётся старая модель, отчёт в Telegram с вердиктом гейта уходит в любом случае (`notify_retrain.py`, `if: always()`).

## Артефакты (`models/`, коммитятся ретрейном)

| Файл | Что | Кто читает |
|---|---|---|
| `model.cbm` | точечная модель | `predict.load_model` |
| `model_quantile.cbm` | q10/q90 (`model_lo.cbm`/`model_hi.cbm` — легаси до issue #132, фолбэк в `load_interval_models`) | predict |
| `model_meta.json` | фичи, cat_features, метрики, ДИ, валидность, интервал (`cqr_scale`), `ppsm_maps`, split, dedup, `trained_at` | predict, `/api/health`, README-sync, гейт |
| `model_gate_samples.json` | пары APE новая/старая | `model_gate.py` |
| `spatial_ref.json` | медианы ₸/м² по H3-гексагонам с train | `spatial.py` |
| `stats.json` | снапшот рыночной статистики для деплоя без базы | `stats.py` (фолбэк) |
| `complexes.json`, `osm_pois.json`, `osm_zones.json` | справочники ЖК, POI, полигоны зон (разовые скрипты `fetch_osm_*`, `crawl_complexes`) | features/geo/zones |
| `metrics_history.jsonl` | строка на ретрейн | `monitoring.py`, тренд |

Те же файлы пакуются в `model-latest` (`models.tar.gz`) для окружений без checkout'а.

## Как менять модель

1. Гипотеза → фича в `features.py` (и в `listing_to_frame`, чтобы predict считал её так же).
2. `python scripts/backtest.py` с фичей и без — парный walk-forward, смотреть Δ MAPE по срезам и на сегменте, ради которого фича задумана, не только среднее.
3. Если выигрыш измерим — добавить в `NUM_FEATURES`/`CAT_FEATURES`, `make train`, проверить `tests/test_features.py`, `test_train_smoke`, `test_model_artifacts`.
4. PR. Ретрейн в воскресенье пройдёт гейт сам; README обновится автоматически.

Не делать: менять `ALL_FEATURES` без пересчёта `spatial_ref`/`ppsm_maps` (они в мете и снапшоте, predict читает их оттуда); добавлять фичу с походом во внешний сервис на пользовательском пути без фича-флага.
