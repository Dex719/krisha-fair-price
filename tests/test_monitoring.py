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
