"""
> [!NOTE]
> Модуль gui.theme_manager: Система дизайн-токенов Windows 11 Fluent 2 (Dark и Light),
> фирменная палитра Sponge Ochre, автосинхронизация темы ОС (Qt + реестр Windows),
> нативный DWM Acrylic/Mica через dwmapi и генератор QSS для Docked Blade.
"""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication

from gui.dock_manager import DockPosition, compute_corner_radii

# Константы Windows 11 Desktop Window Manager (dwmapi)
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY = 19
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_SYSTEMBACKDROP_TYPE = 38

DWMWCP_DONOTROUND = 1
DWMWCP_ROUND = 2

DWMSBT_NONE = 1
DWMSBT_MAINWINDOW = 2
DWMSBT_TRANSIENTWINDOW = 3
DWMSBT_TABBEDWINDOW = 4


class ThemeMode(StrEnum):
    """Режим выбора темы оформления интерфейса."""

    SYSTEM = "system"
    DARK = "dark"
    LIGHT = "light"


class BackdropType(StrEnum):
    """Тип системного размытия окна Windows 11 DWM."""

    NONE = "none"
    MICA = "mica"
    ACRYLIC = "acrylic"
    TABBED = "tabbed"


@dataclass(frozen=True)
class ThemeTokens:
    """Полный набор дизайн-токенов Windows 11 Fluent 2 + Sponge Ochre."""

    name: str
    is_dark: bool
    background: str
    surface: str
    surface_glass: str
    surface_elevated: str
    surface_card: str
    surface_chip: str
    surface_chip_hover: str
    border: str
    border_specular: str
    border_subtle: str
    divider: str
    text: str
    text_secondary: str
    text_muted: str
    sponge_ochre: str
    sponge_ochre_soft: str
    sponge_ochre_border: str
    accent: str
    accent_soft: str
    accent_border: str
    success: str
    success_soft: str
    success_border: str
    danger: str
    danger_soft: str
    danger_border: str
    pore_idle: str
    pore_tile_color: str
    pore_tile_alpha: int
    shadow_color: str
    scrollbar_handle: str


DARK_TOKENS = ThemeTokens(
    name="dark",
    is_dark=True,
    background="#1B1D22",
    surface="#22252C",
    surface_glass="rgba(27, 29, 34, 0.88)",
    surface_elevated="rgba(38, 41, 49, 0.94)",
    surface_card="rgba(32, 35, 42, 0.92)",
    surface_chip="rgba(255, 255, 255, 0.06)",
    surface_chip_hover="rgba(255, 255, 255, 0.12)",
    border="rgba(255, 255, 255, 0.11)",
    border_specular="rgba(255, 255, 255, 0.18)",
    border_subtle="rgba(255, 255, 255, 0.07)",
    divider="rgba(255, 255, 255, 0.08)",
    text="#F3F4F6",
    text_secondary="#A1A7B3",
    text_muted="#737A88",
    sponge_ochre="#E9B44C",
    sponge_ochre_soft="rgba(233, 180, 76, 0.16)",
    sponge_ochre_border="rgba(233, 180, 76, 0.42)",
    accent="#60CDFF",
    accent_soft="rgba(96, 205, 255, 0.16)",
    accent_border="rgba(96, 205, 255, 0.42)",
    success="#3DD68C",
    success_soft="rgba(61, 214, 140, 0.16)",
    success_border="rgba(61, 214, 140, 0.40)",
    danger="#FF5C52",
    danger_soft="rgba(255, 92, 82, 0.18)",
    danger_border="rgba(255, 92, 82, 0.46)",
    pore_idle="#8E96A4",
    pore_tile_color="#FFFFFF",
    pore_tile_alpha=11,
    shadow_color="rgba(0, 0, 0, 0.42)",
    scrollbar_handle="rgba(255, 255, 255, 0.18)",
)

LIGHT_TOKENS = ThemeTokens(
    name="light",
    is_dark=False,
    background="#F6F5F1",
    surface="#ECEAE3",
    surface_glass="rgba(246, 245, 241, 0.90)",
    surface_elevated="rgba(255, 255, 253, 0.96)",
    surface_card="rgba(255, 255, 252, 0.94)",
    surface_chip="rgba(24, 28, 36, 0.05)",
    surface_chip_hover="rgba(24, 28, 36, 0.11)",
    border="rgba(24, 28, 36, 0.12)",
    border_specular="rgba(255, 255, 255, 0.78)",
    border_subtle="rgba(24, 28, 36, 0.08)",
    divider="rgba(24, 28, 36, 0.09)",
    text="#1B1E24",
    text_secondary="#525866",
    text_muted="#7B8291",
    sponge_ochre="#B87D14",
    sponge_ochre_soft="rgba(184, 125, 20, 0.14)",
    sponge_ochre_border="rgba(184, 125, 20, 0.36)",
    accent="#0067C0",
    accent_soft="rgba(0, 103, 192, 0.12)",
    accent_border="rgba(0, 103, 192, 0.34)",
    success="#107C41",
    success_soft="rgba(16, 124, 65, 0.12)",
    success_border="rgba(16, 124, 65, 0.34)",
    danger="#C42B1C",
    danger_soft="rgba(196, 43, 28, 0.12)",
    danger_border="rgba(196, 43, 28, 0.36)",
    pore_idle="#6C7280",
    pore_tile_color="#1B1E24",
    pore_tile_alpha=11,
    shadow_color="rgba(24, 28, 36, 0.16)",
    scrollbar_handle="rgba(24, 28, 36, 0.22)",
)

_BACKDROP_MAP: dict[BackdropType, int] = {
    BackdropType.NONE: DWMSBT_NONE,
    BackdropType.MICA: DWMSBT_MAINWINDOW,
    BackdropType.ACRYLIC: DWMSBT_TRANSIENTWINDOW,
    BackdropType.TABBED: DWMSBT_TABBEDWINDOW,
}


def detect_windows_dark_mode() -> bool:
    """
    Считывает тему приложений Windows из реестра (AppsUseLightTheme).

    Возвращает True для тёмной темы и False для светлой; при недоступности
    реестра или запуске вне Windows безопасно возвращает True.
    """
    if sys.platform != "win32":
        return True
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _reg_type = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(value) == 0
    except OSError:
        return True


def _set_dwm_int_attribute(hwnd: int, attr: int, value: int) -> bool:
    """Вызывает dwmapi.DwmSetWindowAttribute для целочисленного DWORD-атрибута."""
    try:
        dwmapi = ctypes.windll.dwmapi
        func = dwmapi.DwmSetWindowAttribute
        func.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        func.restype = ctypes.c_long
        c_val = ctypes.c_int(value)
        hr = int(func(ctypes.c_void_p(hwnd), attr, ctypes.byref(c_val), ctypes.sizeof(c_val)))
        return hr == 0
    except (AttributeError, OSError, ValueError):
        return False


def apply_win11_backdrop(
    hwnd: int,
    dark: bool,
    backdrop: BackdropType | str = BackdropType.ACRYLIC,
    *,
    disable_dwm_rounding: bool = True,
) -> bool:
    """
    Применяет нативную тему и размытие Windows 11 (Acrylic / Mica) через DWM.

    В headless/offscreen-режиме, при некорректном hwnd или вне Windows работает
    как безопасный no-op и возвращает False.
    """
    if sys.platform != "win32" or hwnd <= 0:
        return False
    if os.environ.get("QT_QPA_PLATFORM", "").strip().lower() == "offscreen":
        return False

    try:
        backdrop_enum = (
            backdrop if isinstance(backdrop, BackdropType) else BackdropType(backdrop.lower())
        )
    except ValueError:
        backdrop_enum = BackdropType.ACRYLIC

    dark_flag = 1 if dark else 0
    dark_ok = _set_dwm_int_attribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, dark_flag)
    if not dark_ok:
        dark_ok = _set_dwm_int_attribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY, dark_flag)

    corner_pref = DWMWCP_DONOTROUND if disable_dwm_rounding else DWMWCP_ROUND
    _set_dwm_int_attribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, corner_pref)

    backdrop_val = _BACKDROP_MAP[backdrop_enum]
    backdrop_ok = _set_dwm_int_attribute(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, backdrop_val)
    return dark_ok or backdrop_ok


class ThemeManager(QObject):
    """
    Менеджер тем оформления с автосинхронизацией Dark/Light ОС.

    Подписывается на QGuiApplication.styleHints().colorSchemeChanged и использует
    реестр Windows как надёжный резервный источник при Unknown colorScheme.
    """

    theme_changed = Signal(object)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        mode: ThemeMode | str = ThemeMode.SYSTEM,
    ) -> None:
        """Инициализирует менеджер тем и определяет начальную палитру."""
        super().__init__(parent)
        self._mode: ThemeMode = (
            mode if isinstance(mode, ThemeMode) else ThemeMode(mode.strip().lower())
        )
        self._tokens: ThemeTokens = self._resolve_tokens()
        self._connect_system_hints()

    @property
    def mode(self) -> ThemeMode:
        """Возвращает текущий режим выбора темы (system / dark / light)."""
        return self._mode

    @property
    def tokens(self) -> ThemeTokens:
        """Возвращает активный набор дизайн-токенов."""
        return self._tokens

    @property
    def is_dark(self) -> bool:
        """Возвращает True, если активна тёмная тема."""
        return self._tokens.is_dark

    def set_mode(self, mode: ThemeMode | str) -> ThemeTokens:
        """Переключает режим темы и испускает theme_changed при смене палитры."""
        coerced = mode if isinstance(mode, ThemeMode) else ThemeMode(mode.strip().lower())
        self._mode = coerced
        return self._apply_resolved_tokens()

    def refresh_from_system(self) -> ThemeTokens:
        """Принудительно перечитывает системную тему и обновляет токены."""
        return self._apply_resolved_tokens()

    @classmethod
    def resolve_tokens(cls, mode: ThemeMode | str = ThemeMode.SYSTEM) -> ThemeTokens:
        """Возвращает дизайн-токены Dark/Light для заданного режима темы без создания экземпляра."""
        coerced = mode if isinstance(mode, ThemeMode) else ThemeMode(mode.strip().lower())
        if coerced == ThemeMode.DARK:
            return DARK_TOKENS
        if coerced == ThemeMode.LIGHT:
            return LIGHT_TOKENS
        return DARK_TOKENS if cls._detect_system_dark() else LIGHT_TOKENS

    def _resolve_tokens(self) -> ThemeTokens:
        """Вычисляет ThemeTokens для текущего режима."""
        return self.resolve_tokens(self._mode)

    @staticmethod
    def _detect_system_dark() -> bool:
        """Определяет тёмную тему через Qt styleHints с откатом к реестру Windows."""
        hints = QGuiApplication.styleHints()
        if hints is not None:
            scheme = hints.colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return True
            if scheme == Qt.ColorScheme.Light:
                return False
        return detect_windows_dark_mode()

    def _apply_resolved_tokens(self) -> ThemeTokens:
        """Обновляет текущие токены и при изменении отправляет сигнал."""
        resolved = self._resolve_tokens()
        if resolved != self._tokens:
            self._tokens = resolved
            self.theme_changed.emit(resolved)
        return self._tokens

    def _connect_system_hints(self) -> None:
        """Подписывается на изменение системной цветовой схемы Qt 6."""
        hints = QGuiApplication.styleHints()
        if hints is None:
            return
        hints.colorSchemeChanged.connect(self._on_system_scheme_changed)

    def _on_system_scheme_changed(self, _scheme: Qt.ColorScheme) -> None:
        """Реагирует на переключение темы ОС в режиме ThemeMode.SYSTEM."""
        if self._mode == ThemeMode.SYSTEM:
            self._apply_resolved_tokens()


def _blade_corner_qss(dock_position: DockPosition | str | None, corner_radius: int) -> str:
    """Формирует CSS-правила радиусов скругления каждого угла для пристыкованного окна."""
    tl, tr, br, bl = compute_corner_radii(dock_position, corner_radius)
    return (
        f"border-top-left-radius: {tl}px;\n"
        f"        border-top-right-radius: {tr}px;\n"
        f"        border-bottom-right-radius: {br}px;\n"
        f"        border-bottom-left-radius: {bl}px;"
    )


def _blade_base_qss(tokens: ThemeTokens, radii_css: str) -> str:
    """Формирует базовые правила QSS для оболочки Docked Blade и поля ввода."""
    focus_bg = "rgba(255, 255, 255, 0.05)" if tokens.is_dark else "rgba(255, 255, 255, 0.72)"
    hover_bg = "rgba(255, 255, 255, 0.03)" if tokens.is_dark else "rgba(24, 28, 36, 0.03)"
    return f"""
    QWidget {{
        font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Inter', sans-serif;
        color: {tokens.text};
    }}

    QLabel {{
        background-color: transparent;
        border: none;
    }}

    QFrame#bladeFrame {{
        background-color: {tokens.surface_glass};
        border: 1px solid {tokens.border};
        {radii_css}
    }}

    QFrame#spotlightBar {{
        background-color: transparent;
        border: none;
        {radii_css}
    }}

    QLineEdit#searchInput {{
        background-color: transparent;
        border: 1px solid transparent;
        border-radius: 10px;
        color: {tokens.text};
        font-size: 14.5px;
        font-weight: 400;
        padding: 6px 10px;
        selection-background-color: {tokens.accent_soft};
        selection-color: {tokens.text};
    }}

    QLineEdit#searchInput:hover {{
        background-color: {hover_bg};
    }}

    QLineEdit#searchInput:focus {{
        background-color: {focus_bg};
        border: 1px solid {tokens.sponge_ochre_border};
    }}
    """


def _blade_widgets_qss(tokens: ThemeTokens) -> str:
    """Формирует правила QSS для чипов, карточек шагов, мини-бара и скролла."""
    return f"""
    QPushButton#contextChip {{
        background-color: {tokens.surface_chip};
        color: {tokens.text_secondary};
        border: 1px solid {tokens.border_subtle};
        border-radius: 11px;
        min-height: 20px;
        max-height: 22px;
        padding: 0px 9px;
        font-size: 10.5px;
        font-weight: 500;
    }}

    QPushButton#contextChip:hover {{
        background-color: {tokens.surface_chip_hover};
        color: {tokens.text};
        border: 1px solid {tokens.border};
    }}

    QPushButton#contextChip[active="true"] {{
        background-color: {tokens.sponge_ochre_soft};
        color: {tokens.sponge_ochre};
        border: 1px solid {tokens.sponge_ochre_border};
        font-weight: 600;
    }}

    QFrame#stepCard {{
        background-color: {tokens.surface_card};
        border: 1px solid {tokens.border_subtle};
        border-radius: 12px;
    }}

    QFrame#agentMiniBar {{
        background-color: {tokens.surface_elevated};
        border: 1px solid {tokens.border};
        border-radius: 14px;
    }}

    QPushButton#miniAbortBtn {{
        background-color: {tokens.danger_soft};
        color: {tokens.danger};
        border: 1px solid {tokens.danger_border};
        border-radius: 11px;
        padding: 4px 12px;
        font-size: 11px;
        font-weight: 700;
    }}

    QPushButton#miniAbortBtn:hover {{
        background-color: {tokens.danger};
        color: #FFFFFF;
    }}
    """


def _blade_chrome_qss(tokens: ThemeTokens) -> str:
    """Формирует правила QSS для полосы прогресса и вертикального скроллбара."""
    return f"""
    QProgressBar#miniProgressBar {{
        background-color: {tokens.surface_chip};
        border: none;
        border-radius: 2px;
        min-height: 4px;
        max-height: 4px;
    }}

    QProgressBar#miniProgressBar::chunk {{
        background-color: {tokens.sponge_ochre};
        border-radius: 2px;
    }}

    QScrollBar:vertical {{
        background: transparent;
        width: 5px;
        margin: 4px 1px 4px 0px;
    }}

    QScrollBar::handle:vertical {{
        background: {tokens.scrollbar_handle};
        border-radius: 2px;
        min-height: 24px;
    }}

    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical,
    QScrollBar::sub-page:vertical {{
        background: transparent;
        height: 0px;
    }}
    """


def build_blade_stylesheet(
    tokens: ThemeTokens,
    dock_position: DockPosition | str | None = None,
    *,
    corner_radius: int = 18,
) -> str:
    """
    Генерирует полную таблицу стилей QSS для Concept B (Fluent 2 Docked Blade).

    Учитывает активную тему (Dark / Light) и геометрию плоских кромок для
    любой из 8 позиций стыковки.
    """
    radii_css = _blade_corner_qss(dock_position, corner_radius)
    return "\n".join(
        (
            _blade_base_qss(tokens, radii_css),
            _blade_widgets_qss(tokens),
            _blade_chrome_qss(tokens),
        )
    )
