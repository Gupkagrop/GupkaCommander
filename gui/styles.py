"""
> [!NOTE]
> Модуль gui.styles: Централизованная дизайн-система и стили Windows 11 Fluent Dark.
> Предоставляет палитру цветов, CSS/QSS-тему для фреймворка PySide6 и утилиты стилизации.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FluentPalette:
    """Цветовая палитра Windows 11 Fluent Dark."""

    # Базовые поверхности и фон
    background: str = "#1e1e1e"
    surface: str = "#252525"
    surface_secondary: str = "#2d2d2d"
    surface_hover: str = "#333333"
    surface_pressed: str = "#2a2a2a"

    # Границы и разделители
    border: str = "#333333"
    border_subtle: str = "#3a3a3a"
    border_focus: str = "#60cdff"

    # Текст и типографика
    text: str = "#ffffff"
    secondary: str = "#a0a0a0"
    text_disabled: str = "#666666"

    # Акцентные и семантические цвета
    accent: str = "#60cdff"
    accent_hover: str = "#7ad5ff"
    accent_pressed: str = "#45bceb"
    error: str = "#ff453a"
    success: str = "#30d158"
    warning: str = "#f0ad4e"


# Глобальный синглтон палитры
PALETTE = FluentPalette()


FLUENT_DARK_THEME = f"""
/* =========================================================================
   GupkaCommander — Windows 11 Fluent Dark Theme
   ========================================================================= */

/* Базовые элементы окна */
QWidget {{
    background-color: {PALETTE.background};
    color: {PALETTE.text};
    font-family: "Segoe UI Variable Text", "Segoe UI", -apple-system, sans-serif;
    font-size: 13px;
    selection-background-color: {PALETTE.accent};
    selection-color: #000000;
}}

/* Главная панель Spotlight */
QFrame#spotlightBar {{
    background-color: {PALETTE.surface};
    border: 1px solid {PALETTE.border};
    border-radius: 12px;
}}

/* Поле поискового ввода */
QLineEdit#searchInput {{
    background-color: transparent;
    color: {PALETTE.text};
    border: none;
    font-size: 15px;
    padding: 6px 8px;
    selection-background-color: {PALETTE.accent};
    selection-color: #000000;
}}

QLineEdit#searchInput:focus {{
    border: none;
}}

QLineEdit#searchInput[placeholderText] {{
    color: {PALETTE.secondary};
}}

/* Кнопки */
QPushButton {{
    background-color: {PALETTE.surface_secondary};
    color: {PALETTE.text};
    border: 1px solid {PALETTE.border};
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: 500;
}}

QPushButton:hover {{
    background-color: {PALETTE.surface_hover};
    border-color: {PALETTE.border_subtle};
}}

QPushButton:pressed {{
    background-color: {PALETTE.surface_pressed};
}}

QPushButton:disabled {{
    background-color: {PALETTE.background};
    color: {PALETTE.text_disabled};
    border-color: {PALETTE.border};
}}

QPushButton#accentButton {{
    background-color: {PALETTE.accent};
    color: #000000;
    border: none;
    font-weight: 600;
}}

QPushButton#accentButton:hover {{
    background-color: {PALETTE.accent_hover};
}}

QPushButton#accentButton:pressed {{
    background-color: {PALETTE.accent_pressed};
}}

/* Статусные бейджи */
QLabel#statusBadge {{
    background-color: {PALETTE.surface_secondary};
    color: {PALETTE.secondary};
    border: 1px solid {PALETTE.border};
    border-radius: 10px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}}

/* Полосы прокрутки */
QScrollArea {{
    background-color: transparent;
    border: none;
}}

QScrollBar:vertical {{
    background-color: transparent;
    width: 6px;
    margin: 0px;
}}

QScrollBar::handle:vertical {{
    background-color: {PALETTE.border_subtle};
    border-radius: 3px;
    min-height: 24px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {PALETTE.secondary};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
    background: none;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}

/* Всплывающие подсказки */
QToolTip {{
    background-color: {PALETTE.surface};
    color: {PALETTE.text};
    border: 1px solid {PALETTE.border};
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 12px;
}}

/* Контекстные меню */
QMenu {{
    background-color: {PALETTE.surface};
    color: {PALETTE.text};
    border: 1px solid {PALETTE.border};
    border-radius: 8px;
    padding: 4px;
}}

QMenu::item {{
    padding: 6px 20px 6px 28px;
    border-radius: 4px;
}}

QMenu::item:selected {{
    background-color: {PALETTE.surface_hover};
    color: {PALETTE.text};
}}

QMenu::separator {{
    height: 1px;
    background-color: {PALETTE.border};
    margin: 4px 8px;
}}
"""


def get_fluent_stylesheet() -> str:
    """
    Возвращает единую таблицу стилей QSS для темы Fluent Dark.

    Returns:
        str: Полная таблица стилей Qt Style Sheet.
    """
    return FLUENT_DARK_THEME


def get_status_badge_qss(status: str) -> str:
    """
    Возвращает специфический QSS для бейджа статуса конвейера.

    Args:
        status: Текстовый статус ('IDLE', 'LISTENING', 'PROCESSING', 'SPEAKING', 'ERROR').

    Returns:
        str: Строка стилей QSS для QLabel.
    """
    normalized = status.strip().upper()

    if normalized == "LISTENING":
        color = PALETTE.accent
        bg = "rgba(96, 205, 255, 0.15)"
    elif normalized == "PROCESSING":
        color = PALETTE.warning
        bg = "rgba(240, 173, 78, 0.15)"
    elif normalized == "SPEAKING":
        color = PALETTE.success
        bg = "rgba(48, 209, 88, 0.15)"
    elif normalized in ("ERROR", "FAILED"):
        color = PALETTE.error
        bg = "rgba(255, 69, 58, 0.15)"
    else:
        color = PALETTE.secondary
        bg = PALETTE.surface_secondary

    return (
        f"background-color: {bg}; "
        f"color: {color}; "
        f"border: 1px solid {color}; "
        f"border-radius: 10px; "
        f"padding: 2px 8px; "
        f"font-size: 11px; "
        f"font-weight: 600;"
    )
