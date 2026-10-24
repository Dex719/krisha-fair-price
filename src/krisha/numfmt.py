"""Числа в сообщениях бота — как на сайте: запятая в дробях и типографский минус.

Сайт пишет «28,7 млн ₸» и «−4,2%», а бот раньше — «28.7» и «-4.2%»: одна и та
же оценка выглядела по-разному. Здесь — единственное место, где бот, слежка и
выгодные объявления превращают число в текст.
"""

MINUS = "−"


def dec(value: float, digits: int = 1) -> str:
    """43.8 → «43,8»; отрицательное — с «−», а «−0,0» не бывает."""
    s = f"{abs(value):.{digits}f}".replace(".", ",")
    return (MINUS if value < 0 and s.strip("0,") else "") + s


def signed(value: float, digits: int = 1) -> str:
    """Со знаком: «+8,1», «−7,7»; ноль — без знака."""
    s = f"{abs(value):.{digits}f}".replace(".", ",")
    if not s.strip("0,"):
        return s
    return ("+" if value > 0 else MINUS) + s


def pct(value: float, digits: int = 1) -> str:
    """Процент со знаком: «+8,1%», «−14,9%»."""
    return signed(value, digits) + "%"
