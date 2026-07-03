"""Тесты мониторинга модели: история метрик и отчёт о retrain."""


from krisha import monitoring

META_OLD = {"metrics": {"model": {"mae": 4_000_000, "mape": 0.10, "r2": 0.90},
                        "n_train": 30_000, "n_test": 7_000}}
META_NEW = {"metrics": {"model": {"mae": 4_400_000, "mape": 0.11, "r2": 0.89},
                        "n_train": 31_000, "n_test": 7_200}}


def _metrics(mae):
    return {"model": {"mae": mae, "mape": 0.1, "r2": 0.9},
            "baseline": {}, "n_train": 1, "n_test": 1,
            "trained_at": "2026-07-01T00:00:00+00:00"}


def test_history_append_and_load(tmp_path):
    path = tmp_path / "hist.jsonl"
    monitoring.append_metrics_history(_metrics(4_000_000), path=path)
    monitoring.append_metrics_history(_metrics(4_200_000), path=path)
    hist = monitoring.load_metrics_history(path=path)
    assert [h["mae"] for h in hist] == [4_000_000, 4_200_000]
    assert hist[0]["trained_at"] == "2026-07-01T00:00:00+00:00"
    # last= ограничивает хвост
    assert len(monitoring.load_metrics_history(path=path, last=1)) == 1
    assert monitoring.load_metrics_history(path=tmp_path / "no.jsonl") == []


def test_format_retrain_report_deltas():
    text = monitoring.format_retrain_report(META_OLD, META_NEW, gate_passed=False)
    assert "Гейт не пройден" in text
    assert "4.40 млн ₸" in text and "+10.0%" in text  # дельта MAE
    assert "+1.00 п.п." in text  # дельта MAPE
    ok = monitoring.format_retrain_report(META_OLD, META_NEW, gate_passed=True,
                                          history=[{"mae": 4_000_000}, {"mae": 4_400_000}])
    assert "Модель обновлена" in ok
    assert "Тренд MAE (млн ₸): 4.00 → 4.40" in ok


def test_notify_retrain(monkeypatch):
    sent = {}

    def fake_tg_call(method, **payload):
        sent.update(method=method, **payload)
        return {"ok": True}

    monkeypatch.setattr("krisha.bot.tg_call", fake_tg_call)
    # без чата — не шлём
    monkeypatch.delenv("TG_ADMIN_CHAT_ID", raising=False)
    assert monitoring.notify_retrain(META_OLD, META_NEW, True) is False
    # с чатом — шлём HTML в нужный chat_id
    monkeypatch.setenv("TG_ADMIN_CHAT_ID", "42")
    assert monitoring.notify_retrain(META_OLD, META_NEW, True) is True
    assert sent["chat_id"] == 42 and sent["parse_mode"] == "HTML"
    assert "Модель обновлена" in sent["text"]


def _fill_stats_db(db):
    from krisha.db import get_conn, init_db, upsert_listing

    init_db(db)
    base = {
        "url": "https://krisha.kz/a/show/0",
        "title": None,
        "area": 60.0,
        "source": "test",
    }
    rows = [
        {"id": 1, "price": 40_000_000, "rooms": 1, "district": "Bostandykskiy_r-n"},
        {"id": 2, "price": 50_000_000, "rooms": 2, "district": "Bostandykskiy_r-n"},
        {"id": 3, "price": 60_000_000, "rooms": 4, "district": "Almalinskiy_r-n"},
        {"id": 4, "price": 70_000_000, "rooms": 5, "district": "Almalinskiy_r-n"},
    ]
    for row in rows:
        upsert_listing({**base, **row}, db_path=db)
    with get_conn(db) as conn:
        # id=4 ушёл с рынка на этой неделе
        conn.execute("UPDATE listings SET is_active = 0 WHERE id = 4")
        # id=1 «старый»: появился раньше недели назад
        conn.execute(
            "UPDATE listings SET first_seen = datetime('now', '-30 days') WHERE id = 1"
        )
        conn.commit()


def test_dataset_summary(tmp_path):
    from krisha.monitoring import dataset_summary

    db = tmp_path / "krisha.db"
    _fill_stats_db(db)
    ds = dataset_summary(db)
    assert ds["total"] == 4
    assert ds["active"] == 3
    assert ds["new_7d"] == 3  # все, кроме «старого» id=1
    assert ds["gone_7d"] == 1
    assert ds["by_rooms"] == {"1к": 1, "2к": 1, "4к+": 1}
    assert dict(ds["top_districts"])["Bostandykskiy_r-n"] == 2
    assert ds["median_price"] == 50_000_000
    assert ds["median_ppsm"] == int(50_000_000 / 60.0)


def test_dataset_summary_missing_db(tmp_path):
    from krisha.monitoring import dataset_summary

    assert dataset_summary(tmp_path / "nope.db") is None


def test_report_includes_dataset_block():
    from krisha.monitoring import format_retrain_report

    meta = {"metrics": {"model": {"mae": 4e6, "mape": 0.10, "r2": 0.9}, "n_train": 10, "n_test": 3}}
    ds = {
        "total": 44210,
        "active": 38900,
        "new_7d": 1234,
        "gone_7d": 567,
        "by_rooms": {"1к": 12000, "2к": 15000},
        "top_districts": [("Bostandykskiy_r-n", 9800)],
        "median_price": 42_500_000,
        "median_ppsm": 650_000,
    }
    text = format_retrain_report(meta, meta, True, dataset=ds)
    assert "44 210 квартир" in text
    assert "+1 234 новых" in text
    assert "1к 12 000" in text
    assert "42.5 млн ₸" in text
