"""
> [!NOTE]
> Модуль gui.styles: Палитра цветов, таблица стилей QSS и русскоязычные бейджи
> состояний для интерфейса GupkaCommander в стиле Windows 11 Fluent Dark Glass.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FluentPalette:
    """Цветовая палитра интерфейса Windows 11 Fluent Dark."""

    background: str = "#1E1E1E"
    surface: str = "#252525"
    border: str = "#333333"
    text: str = "#FFFFFF"
    secondary: str = "#A0A0A0"
    accent: str = "#60CDFF"
    error: str = "#FF453A"
    success: str = "#30D158"
    warning: str = "#FFD60A"

    # Расширенные токены стеклянных поверхностей и карточек
    surface_secondary: str = "#2D2D30"
    surface_glass: str = "rgba(24, 24, 28, 242)"
    surface_elevated: str = "rgba(40, 41, 48, 210)"
    border_glass: str = "rgba(255, 255, 255, 0.11)"
    border_subtle: str = "rgba(255, 255, 255, 0.07)"
    divider: str = "rgba(255, 255, 255, 0.08)"
    user_bubble_bg: str = "rgba(96, 205, 255, 0.14)"
    user_bubble_border: str = "rgba(96, 205, 255, 0.28)"
    assistant_card_bg: str = "rgba(38, 39, 46, 0.92)"
    action_card_bg: str = "rgba(52, 44, 24, 0.72)"
    action_card_border: str = "rgba(255, 214, 10, 0.25)"


PALETTE = FluentPalette()

_STATUS_LABELS_RU: dict[str, str] = {
    "IDLE": "Готов",
    "LISTENING": "Слушаю",
    "PROCESSING": "Думаю...",
    "SPEAKING": "Говорю",
    "MUTED": "Без звука",
    "ERROR": "Ошибка",
}


def get_status_label_ru(state: str) -> str:
    """
    Возвращает локализованную краткую подпись состояния конвейера для бейджа.

    Args:
        state: Строковое имя состояния (IDLE, LISTENING, PROCESSING, SPEAKING, MUTED, ERROR).

    Returns:
        Локализованная строка состояния на русском языке.
    """
    norm = state.strip().upper()
    return _STATUS_LABELS_RU.get(norm, "Готов")


def get_status_badge_qss(state: str) -> str:
    """
    Генерирует QSS-стиль пилюли-индикатора состояния для #statusBadge.

    Args:
        state: Состояние голосового конвейера или интерфейса.

    Returns:
        Строка QSS для применения к виджету #statusBadge.
    """
    norm = state.strip().upper()
    if norm == "LISTENING":
        fg = PALETTE.accent
        bg = "rgba(96, 205, 255, 0.16)"
        border = "rgba(96, 205, 255, 0.42)"
    elif norm == "SPEAKING":
        fg = PALETTE.success
        bg = "rgba(48, 209, 88, 0.16)"
        border = "rgba(48, 209, 88, 0.42)"
    elif norm == "PROCESSING":
        fg = PALETTE.warning
        bg = "rgba(255, 214, 10, 0.16)"
        border = "rgba(255, 214, 10, 0.40)"
    elif norm in ("ERROR", "MUTED"):
        fg = PALETTE.error
        bg = "rgba(255, 69, 58, 0.16)"
        border = "rgba(255, 69, 58, 0.42)"
    else:
        fg = PALETTE.secondary
        bg = "rgba(255, 255, 255, 0.06)"
        border = "rgba(255, 255, 255, 0.11)"

    return (
        "QLabel#statusBadge {"
        f"  color: {fg};"
        f"  background-color: {bg};"
        f"  border: 1px solid {border};"
        "  border-radius: 12px;"
        "  padding: 4px 11px;"
        "  font-family: 'Segoe UI', 'Inter', sans-serif;"
        "  font-size: 11px;"
        "  font-weight: 600;"
        "}"
    )


def get_fluent_stylesheet() -> str:
    """
    Формирует основную таблицу стилей QSS для плавающего окна SpotlightBar и ChatDrawer.

    Returns:
        Полный QSS шаблон в стиле Windows 11 Fluent Dark Glass.
    """
    return f"""
    QWidget {{
        font-family: 'Segoe UI', 'Inter', sans-serif;
        color: {PALETTE.text};
    }}

    QFrame#spotlightBar {{
        background-color: {PALETTE.surface_glass};
        border: 1px solid {PALETTE.border_glass};
        border-radius: 18px;
    }}

    QLineEdit#searchInput {{
        background-color: transparent;
        border: none;
        color: {PALETTE.text};
        font-size: 15px;
        font-weight: 400;
        padding: 6px 4px;
        selection-background-color: rgba(96, 205, 255, 0.35);
        selection-color: {PALETTE.text};
    }}

    QFrame#drawerDivider {{
        background-color: {PALETTE.divider};
        border: none;
        min-height: 1px;
        max-height: 1px;
    }}

    QLabel#statusBadge {{
        color: {PALETTE.secondary};
        background-color: rgba(255, 255, 255, 0.06);
        border: 1px solid rgba(255, 255, 255, 0.11);
        border-radius: 12px;
        padding: 4px 11px;
        font-size: 11px;
        font-weight: 600;
    }}

    QPushButton#toggleDrawerBtn,
    QPushButton#actionIconBtn {{
        background-color: rgba(255, 255, 255, 0.04);
        border: 1px solid transparent;
        border-radius: 10px;
        padding: 0px;
    }}

    QPushButton#toggleDrawerBtn:hover,
    QPushButton#actionIconBtn:hover {{
        background-color: rgba(255, 255, 255, 0.10);
        border: 1px solid rgba(255, 255, 255, 0.12);
    }}

    QPushButton#toggleDrawerBtn:pressed,
    QPushButton#actionIconBtn:pressed {{
        background-color: rgba(255, 255, 255, 0.16);
    }}

    QPushButton#actionIconBtn[muted="true"] {{
        background-color: rgba(255, 69, 58, 0.15);
        border: 1px solid rgba(255, 69, 58, 0.38);
    }}

    QPushButton#actionIconBtn[muted="true"]:hover {{
        background-color: rgba(255, 69, 58, 0.24);
    }}

    QWidget#chatDrawer,
    QScrollArea,
    QScrollArea > QWidget > QWidget {{
        background-color: transparent;
        border: none;
    }}

    QScrollBar:vertical {{
        background: transparent;
        width: 5px;
        margin: 6px 2px 6px 0px;
    }}

    QScrollBar::handle:vertical {{
        background: rgba(255, 255, 255, 0.18);
        border-radius: 2px;
        min-height: 28px;
    }}

    QScrollBar::handle:vertical:hover {{
        background: rgba(96, 205, 255, 0.55);
    }}

    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical,
    QScrollBar::sub-page:vertical {{
        background: transparent;
        height: 0px;
    }}

    QFrame#messageCard {{
        background-color: {PALETTE.assistant_card_bg};
        border: 1px solid {PALETTE.border_subtle};
        border-radius: 14px;
    }}

    QFrame#messageCard[role="user"] {{
        background-color: {PALETTE.user_bubble_bg};
        border: 1px solid {PALETTE.user_bubble_border};
        border-radius: 14px;
    }}

    QFrame#messageCard[role="action"] {{
        background-color: {PALETTE.action_card_bg};
        border: 1px solid {PALETTE.action_card_border};
        border-radius: 12px;
    }}

    QLabel#cardRoleLabel {{
        font-size: 11px;
        font-weight: 600;
        color: {PALETTE.secondary};
    }}

    QLabel#cardTextLabel {{
        font-size: 13px;
        font-weight: 400;
        color: {PALETTE.text};
        line-height: 1.4;
    }}

    QLabel#cardStatusBadge {{
        font-size: 10px;
        font-weight: 600;
        border-radius: 8px;
        padding: 2px 8px;
        background-color: rgba(255, 255, 255, 0.08);
        color: {PALETTE.secondary};
    }}
    """


FLUENT_DARK_THEME: str = get_fluent_stylesheet()
