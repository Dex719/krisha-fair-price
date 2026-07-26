"""Тесты слежки за лотами (/track): хранение и алерты об изменениях."""

import sqlite3

from krisha import tracking
from krisha.db import init_db, upsert_listing

BASE = {
    "url": "https://krisha.kz/a/show/111",
    "title": "2-комнатная квартира, 60 м²",
    "price": 50_000_000,
    "area": 60.0,
    "rooms": 2,
    "source": "test",
}


def _no_github_push(monkeypatch):
    """Состояние сохраняем только локально — без сети."""
    monkeypatch.setattr(
        "krisha.subscriptions._push_to_github", lambda *a, **k: None
    )


def test_add_list_remove(tmp_path, monkeypatch):
    _no_github_push(monkeypatch)
    path = tmp_path / "tracked.json"

    ok, reason = tracking.add_tracked(42, 111, 50_000_000, "Квартира", path=path)
    assert ok and reason is None
    ok, reason = tracking.add_tracked(42, 111, 50_000_000, "Квартира", path=path)
    assert not ok and reason == "already"

    lots = tracking.list_tracked(42, path=path)
    assert set(lots) == {"111"} and lots["111"]["price"] == 50_000_000

    assert tracking.remove_tracked(42, 111, path=path) == 1
    assert tracking.list_tracked(42, path=path) == {}


def test_limit_per_chat(tmp_path, monkeypatch):
    _no_github_push(monkeypatch)
    path = tmp_path / "tracked.json"
    for i in range(tracking.MAX_TRACKED_PER_CHAT):
        ok, _ = tracking.add_tracked(1, 1000 + i, None, None, path=path)
        assert ok
    ok, reason = tracking.add_tracked(1, 9999, None, None, path=path)
    assert not ok and reason == "limit"


def test_untrack_all(tmp_path, monkeypatch):
    _no_github_push(monkeypatch)
    path = tmp_path / "tracked.json"
    tracking.add_tracked(1, 111, None, None, path=path)
    tracking.add_tracked(1, 222, None, None, path=path)
    assert tracking.remove_tracked(1, None, path=path) == 2
    assert tracking.load_tracked(path) == {}


def _make_db(tmp_path, price=50_000_000, is_active=1):
    db = tmp_path / "krisha.db"
    init_db(db)
    upsert_listing({**BASE, "id": 111, "price": price}, db_path=db)
    if not is_active:
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE listings SET is_active = 0, "
                "delisted_at = datetime('now') WHERE id = 111"
            )
    return db


def test_check_updates_price_drop(tmp_path, monkeypatch):
    _no_github_push(monkeypatch)
    path = tmp_path / "tracked.json"
    db = _make_db(tmp_path, price=47_500_000)
    tracking.add_tracked(42, 111, 50_000_000, "Квартира", path=path)

    updates = tracking.check_tracked_updates(db_path=db, path=path)
    assert len(updates) == 1
    chat_id, text = updates[0]
    assert chat_id == 42
    assert "📉" in text and "47.5 млн ₸" in text and "-5.0%" in text

    # цена запомнена — повторный проход без изменений молчит
    assert tracking.check_tracked_updates(db_path=db, path=path) == []


def test_check_updates_delisted(tmp_path, monkeypatch):
    _no_github_push(monkeypatch)
    path = tmp_path / "tracked.json"
    db = _make_db(tmp_path, is_active=0)
    tracking.add_tracked(42, 111, 50_000_000, "Квартира", path=path)

    updates = tracking.check_tracked_updates(db_path=db, path=path)
    assert len(updates) == 1
    assert "🏁" in updates[0][1]
    # снятый лот выброшен из слежки
    assert tracking.list_tracked(42, path=path) == {}


def test_delist_after_long_blind_gap_is_not_alerted(tmp_path, monkeypatch):
    """issue #156: снятие, замеченное после долгого перерыва в наблюдении, —
    факт о нашем сбое, а не о рынке.

    Сценарий первого прохода после окна слепоты 14–26.07.2026: у всех
    активных лотов last_seen отстал на 13 дней, и те, что не нашлись в
    выдаче, разом уезжают в delisted. Часть из них жива. Отправленное
    «🏁 Снято с продажи» не отзовёшь, и вместе с ним лот молча выпадает из
    слежки — пользователь заметит это, только когда перестанет получать
    алерты по живому объявлению.

    Поэтому молчим И оставляем лот в слежке: если он жив, ближайший
    нормальный проход вернёт is_active=1 и слежка продолжится сама.
    """
    _no_github_push(monkeypatch)
    path = tmp_path / "tracked.json"
    db = tmp_path / "krisha.db"
    init_db(db)
    upsert_listing({**BASE, "id": 111}, db_path=db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE listings SET is_active = 0, "
            "last_seen = datetime('now', '-14 days'), "
            "delisted_at = datetime('now') WHERE id = 111"
        )
    tracking.add_tracked(42, 111, 50_000_000, "Квартира", path=path)

    assert tracking.check_tracked_updates(db_path=db, path=path) == []
    assert set(tracking.list_tracked(42, path=path)) == {"111"}, (
        "лот обязан остаться в слежке — снятие не подтверждено наблюдением"
    )


def test_delist_within_normal_lag_still_alerts(tmp_path, monkeypatch):
    """Обратная сторона порога: штатный лаг (рескрейп ходит ежедневно и
    снимает после 3 дней отсутствия, замер по проду — медиана 4.0 дня)
    обязан по-прежнему давать алерт, иначе защита съест нормальную работу."""
    _no_github_push(monkeypatch)
    path = tmp_path / "tracked.json"
    db = tmp_path / "krisha.db"
    init_db(db)
    upsert_listing({**BASE, "id": 111}, db_path=db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE listings SET is_active = 0, "
            "last_seen = datetime('now', '-4 days'), "
            "delisted_at = datetime('now') WHERE id = 111"
        )
    tracking.add_tracked(42, 111, 50_000_000, "Квартира", path=path)

    updates = tracking.check_tracked_updates(db_path=db, path=path)
    assert len(updates) == 1 and "🏁" in updates[0][1]
    assert tracking.list_tracked(42, path=path) == {}


def test_check_updates_no_change(tmp_path, monkeypatch):
    _no_github_push(monkeypatch)
    path = tmp_path / "tracked.json"
    db = _make_db(tmp_path, price=50_000_000)
    tracking.add_tracked(42, 111, 50_000_000, "Квартира", path=path)
    assert tracking.check_tracked_updates(db_path=db, path=path) == []


def test_bot_track_commands(tmp_path, monkeypatch):
    """Команды бота: /track со ссылкой, список, /untrack."""
    from krisha import bot

    _no_github_push(monkeypatch)
    path = tmp_path / "tracked.json"
    monkeypatch.setattr(tracking, "TRACKED_PATH", path)
    monkeypatch.setattr(bot, "_track_listing_meta", lambda lid: (50_000_000, "Квартира"))

    calls = []
    monkeypatch.setattr(
        bot, "tg_call", lambda method, **kw: calls.append((method, kw)) or {"ok": True}
    )

    bot.handle_update(
        {"message": {"chat": {"id": 42}, "text": "/track https://krisha.kz/a/show/111"}}
    )
    sent = [kw for m, kw in calls if m == "sendMessage"]
    assert sent and "Слежу" in sent[-1]["text"]
    assert tracking.list_tracked(42) != {}

    bot.handle_update({"message": {"chat": {"id": 42}, "text": "/track"}})
    assert "Квартира" in calls[-1][1]["text"]

    bot.handle_update(
        {"message": {"chat": {"id": 42}, "text": "/untrack https://krisha.kz/a/show/111"}}
    )
    assert "Убрал" in calls[-1][1]["text"]
    assert tracking.list_tracked(42) == {}


def test_tracked_lot_with_unknown_price_heals_and_then_alerts(tmp_path, monkeypatch):
    """Регрессия: лот, взятый в слежку с неизвестной ценой (деталь ещё не
    докачана), не давал алерта НИКОГДА. _lot_event выходил по `old is None`,
    а сохранённая цена обновлялась только внутри ветки «событие есть» — так
    что old оставался None на каждом следующем проходе."""
    from krisha.db import get_conn, init_db, upsert_listing
    from krisha.tracking import add_tracked, check_tracked_updates, list_tracked

    db = tmp_path / "t.db"
    path = tmp_path / "tracked.json"
    init_db(db)
    upsert_listing(
        {"id": 900, "url": "https://krisha.kz/a/show/900", "title": "Лот",
         "price": 40_000_000, "area": 60.0, "lat": 43.24, "lon": 76.89}, db
    )
    # Цена на момент /track неизвестна — ровно тот случай из бага.
    add_tracked(5, 900, None, "Лот", path=path)

    # Первый проход: события нет, но цена обязана «подлечиться» из базы.
    assert check_tracked_updates(db_path=db, path=path) == []
    assert list_tracked(5, path=path)["900"]["price"] == 40_000_000

    with get_conn(db) as conn:
        conn.execute("UPDATE listings SET price = 36000000 WHERE id = 900")

    messages = check_tracked_updates(db_path=db, path=path)
    assert len(messages) == 1
    chat_id, text = messages[0]
    assert chat_id == 5
    assert "📉" in text and "36.0 млн" in text
