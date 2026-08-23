"""Acceptance checks for issue #82 text rebranding and bot showcase."""

from pathlib import Path

from krisha import bot

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def _static(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_help_text_is_bagam_and_lists_real_bot_features():
    text = bot.HELP_TEXT

    assert "<b>baǵam</b>" in text
    assert "FairPrice" not in text
    assert "Telegram-бот" in text
    assert "ссылку" in text and "текст объявления" in text
    assert "/track" in text and "изменится или объявление снимут" in text
    assert "/alerts" in text and "фильтрами" in text


def test_site_bot_showcase_lists_same_three_real_features():
    home = _static("index.html")
    about = _static("about.html")

    for html in (home, about):
        assert "Telegram-бот умеет три вещи" in html
        assert "оценить квартиру по ссылке или тексту" in html
        assert "следить за лотом через /track" in html
        assert "присылать алерты через /alerts" in html
        assert "скоро добавим" not in html.lower()
        assert "скоро появится" not in html.lower()
        assert "в разработке" not in html.lower()
        assert "FairPrice" not in html


def test_botfather_copy_is_documented_for_manual_paste():
    doc = (ROOT / "docs" / "botfather.md").read_text(encoding="utf-8")

    assert "BotFather" in doc
    assert "description" in doc
    assert "about" in doc
    assert "baǵam" in doc
    assert "FairPrice" not in doc
