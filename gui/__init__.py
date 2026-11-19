"""
> [!NOTE]
> Пакет gui: Пользовательский интерфейс GupkaCommander на базе PySide6 (Qt 6 Fluent Dark).
> Экспортирует визуализатор звуковых волн (WaveformWidget), выдвижной ящик истории (ChatDrawer),
> плавающее окно ввода (SpotlightBar), системный трей (TrayManager),
> глобальные горячие клавиши (HotkeyManager) и дизайн-систему (styles).
"""

from gui.chat_drawer import ChatDrawer, MessageCard
from gui.hotkey_manager import HotkeyManager
from gui.spotlight_bar import SpotlightBar
from gui.styles import (
    FLUENT_DARK_THEME,
    PALETTE,
    FluentPalette,
    get_fluent_stylesheet,
    get_status_badge_qss,
)
from gui.tray_icon import TrayManager
from gui.waveform_widget import WaveformWidget

__all__ = [
    "FLUENT_DARK_THEME",
    "PALETTE",
    "ChatDrawer",
    "FluentPalette",
    "HotkeyManager",
    "MessageCard",
    "SpotlightBar",
    "TrayManager",
    "WaveformWidget",
    "get_fluent_stylesheet",
    "get_status_badge_qss",
]
