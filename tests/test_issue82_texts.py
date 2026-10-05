"""Acceptance checks for issue #82 text rebranding and bot showcase."""

import re
from pathlib import Path

from krisha import bot

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
JARGON = re.compile(r"(?<![а-яё])(лот(а|у|ом|е|ы|ов|ам|ами|ах)?|алерт[а-яё]*)(?![а-яё])", re.I)


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

    # «О проекте» больше не пересказывает бота: туда ведёт карточка «Куда дальше»
    assert "Telegram-бот умеет три вещи" not in about and 'href="/bot"' in about
    # жаргон («лот», «алерты») — для кода и команд бота, не для текста сайта
    for html in (home, about):
        text = re.sub(r"<[^>]+>", " ", html.split("<main", 1)[1].split("</main>", 1)[0])
        assert not JARGON.search(text), JARGON.search(text)
    for html in (home,):
        assert "Telegram-бот умеет три вещи" in html
        assert "оценить квартиру по ссылке или тексту" in html
        assert "следить за ценой объявления через /track" in html
        assert "присылать уведомления о выгодных объявлениях через /alerts" in html
        assert "скоро добавим" not in html.lower()
        assert "скоро появится" not in html.lower()
        assert "в разработке" not in html.lower()
        assert "FairPrice" not in html
