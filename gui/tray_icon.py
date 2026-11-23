"""
> [!NOTE]
> Модуль gui.tray_icon: Управление иконкой системного трея (TrayManager) Windows 11.
> Реализует QSystemTrayIcon с динамическим цветовым индикатором состояния конвейера
> (IDLE, LISTENING, PROCESSING, SPEAKING, MUTED) и контекстным меню быстрых действий.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from core.paths import get_icon_path
from gui.styles import PALETTE

logger = logging.getLogger(__name__)

try:
    from core.voice.voice_pipeline import PipelineState
except ImportError:

    class PipelineState:  # type: ignore[no-redef]
        IDLE = "IDLE"
        LISTENING = "LISTENING"
        PROCESSING = "PROCESSING"
        SPEAKING = "SPEAKING"


class TrayManager(QObject):
    """
    Диспетчер системного трея Windows 11.

    Предоставляет:
    - Контекстное меню: Показать/Скрыть, Отключить микрофон, Настройки, Выход.
    - Динамическую отрисовку иконки с цветным статусным индикатором PipelineState.
    - Обработку одинарного/двойного клика по трею для открытия SpotlightBar.
    - Поддержку headless-режима в тестах при отсутствии физического трея.
    """

    show_hide_triggered = Signal()
    mute_toggled = Signal(bool)
    models_triggered = Signal()
    settings_triggered = Signal()
    quit_requested = Signal()

    DEFAULT_ICON_PATH: Path = get_icon_path()

    def __init__(
        self,
        parent: QObject | None = None,
        base_icon_path: Path | str | None = None,
    ) -> None:
        """
        Инициализация диспетчера системного трея.

        Args:
            parent: Родительский QObject.
            base_icon_path: Путь к базовой иконке приложения.
        """
        super().__init__(parent)

        self._state: str = "IDLE"
        self._is_muted: bool = False
        self._base_icon_path = (
            Path(base_icon_path) if base_icon_path is not None else self.DEFAULT_ICON_PATH
        )

        self._tray_icon = QSystemTrayIcon(self)
        self._menu = QMenu()
        self._init_menu()

        self._tray_icon.setContextMenu(self._menu)
        self._tray_icon.activated.connect(self._on_tray_activated)

        self._update_icon()

    def _init_menu(self) -> None:
        """Инициализация контекстного меню системного трея."""
        # 1. Показать / Скрыть панель
        self._show_hide_action = QAction("Показать / Скрыть", self)
        self._show_hide_action.triggered.connect(self._on_show_hide_triggered)
        self._menu.addAction(self._show_hide_action)

        self._menu.addSeparator()

        # 2. Отключить микрофон (Checkable)
        self._mute_action = QAction("Отключить микрофон", self)
        self._mute_action.setCheckable(True)
        self._mute_action.setChecked(False)
        self._mute_action.toggled.connect(self._on_mute_action_toggled)
        self._menu.addAction(self._mute_action)

        # 3. Модели и зависимости
        self._models_action = QAction("Модели и зависимости...", self)
        self._models_action.triggered.connect(self._on_models_triggered)
        self._menu.addAction(self._models_action)

        # 4. Настройки
        self._settings_action = QAction("Настройки...", self)
        self._settings_action.triggered.connect(self._on_settings_triggered)
        self._menu.addAction(self._settings_action)

        self._menu.addSeparator()

        # 4. Выход из приложения
        self._quit_action = QAction("Выход", self)
        self._quit_action.triggered.connect(self._on_quit_triggered)
        self._menu.addAction(self._quit_action)

    def _create_icon(self, color_hex: str) -> QIcon:
        """
        Генерирует иконку трея высокого разрешения с цветным индикатором.

        Args:
            color_hex: Шестнадцатеричный код цвета статуса.

        Returns:
            QIcon: Сформированная иконка Qt.
        """
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if self._base_icon_path.exists():
            base_icon = QIcon(str(self._base_icon_path))
            base_pixmap = base_icon.pixmap(28, 28)
            painter.drawPixmap(2, 2, base_pixmap)

            # Цветной индикатор статуса в правом нижнем углу иконки
            painter.setBrush(QBrush(QColor(color_hex)))
            painter.setPen(QPen(QColor(PALETTE.background), 1.5))
            painter.drawEllipse(20, 20, 10, 10)
        else:
            # Фоновый диск темного цвета (fallback при отсутствии файла иконки)
            painter.setBrush(QBrush(QColor(PALETTE.surface)))
            painter.setPen(QPen(QColor(PALETTE.border), 1.5))
            painter.drawEllipse(2, 2, 28, 28)

            # Внутренний цветной индикатор статуса
            painter.setBrush(QBrush(QColor(color_hex)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(9, 9, 14, 14)

        # Если микрофон заглушен, рисуем диагональную красную черту
        if self._is_muted:
            painter.setPen(QPen(QColor(PALETTE.error), 2.5))
            painter.drawLine(6, 6, 26, 26)

        painter.end()
        return QIcon(pixmap)

    def _update_icon(self) -> None:
        """Обновляет иконку и всплывающую подсказку трея согласно состоянию."""
        if self._is_muted:
            color = PALETTE.error
            status_desc = "Микрофон отключен"
        elif self._state == "LISTENING":
            color = PALETTE.accent
            status_desc = "Слушает..."
        elif self._state == "PROCESSING":
            color = PALETTE.warning
            status_desc = "Обработка запроса..."
        elif self._state == "SPEAKING":
            color = PALETTE.success
            status_desc = "Воспроизведение речи..."
        else:
            color = PALETTE.secondary
            status_desc = "Ожидание команды"

        icon = self._create_icon(color)
        self._tray_icon.setIcon(icon)
        self._tray_icon.setToolTip(f"GupkaCommander — {status_desc}")

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Обработка нажатия на иконку системного трея."""
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_hide_triggered.emit()

    def _on_show_hide_triggered(self) -> None:
        """Обработка выбора пункта меню Показать/Скрыть."""
        self.show_hide_triggered.emit()

    def _on_mute_action_toggled(self, checked: bool) -> None:
        """Обработка переключения пункта меню Отключить микрофон."""
        self._is_muted = checked
        self._update_icon()
        self.mute_toggled.emit(checked)

    def _on_settings_triggered(self) -> None:
        """Обработка выбора пункта меню Настройки."""
        self.settings_triggered.emit()

    def _on_models_triggered(self) -> None:
        """Обработка выбора пункта меню Модели и зависимости."""
        self.models_triggered.emit()

    def _on_quit_triggered(self) -> None:
        """Обработка выбора пункта меню Выход."""
        logger.info("Запрошен выход из приложения через системный трей.")
        self.quit_requested.emit()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    # --- Публичный интерфейс управления треем ---

    @property
    def tray_icon(self) -> QSystemTrayIcon:
        """Возвращает внутренний QSystemTrayIcon."""
        return self._tray_icon

    @property
    def menu(self) -> QMenu:
        """Возвращает контекстное меню трея."""
        return self._menu

    @property
    def show_hide_action(self) -> QAction:
        """Возвращает действие меню Показать/Скрыть."""
        return self._show_hide_action

    @property
    def mute_action(self) -> QAction:
        """Возвращает действие меню Отключить микрофон."""
        return self._mute_action

    @property
    def models_action(self) -> QAction:
        """Возвращает действие меню Модели и зависимости."""
        return self._models_action

    @property
    def settings_action(self) -> QAction:
        """Возвращает действие меню Настройки."""
        return self._settings_action

    @property
    def quit_action(self) -> QAction:
        """Возвращает действие меню Выход."""
        return self._quit_action

    @property
    def base_icon_path(self) -> Path:
        """Возвращает путь к базовой иконке трея."""
        return self._base_icon_path

    def set_state(self, state: str | PipelineState) -> None:
        """
        Устанавливает текущее состояние конвейера для отображения в трее.

        Args:
            state: Состояние конвейера (IDLE, LISTENING, PROCESSING, SPEAKING).
        """
        state_str = state.value if hasattr(state, "value") else str(state)
        self._state = state_str.strip().upper()
        self._update_icon()

    def get_state(self) -> str:
        """Возвращает текущее состояние трея."""
        return self._state

    def set_muted(self, muted: bool) -> None:
        """
        Устанавливает статус отключения микрофона.

        Args:
            muted: True, если микрофон отключен.
        """
        if self._is_muted != muted:
            self._is_muted = muted
            self._mute_action.setChecked(muted)
            self._update_icon()

    def is_muted(self) -> bool:
        """Возвращает флаг отключения микрофона."""
        return self._is_muted

    def show(self) -> None:
        """Отображает иконку в системном трее."""
        self._tray_icon.show()

    def hide(self) -> None:
        """Скрывает иконку из системного трея."""
        self._tray_icon.hide()

    def is_visible(self) -> bool:
        """Возвращает видимость иконки трея."""
        return self._tray_icon.isVisible()

    def show_message(
        self,
        title: str,
        message: str,
        icon: QSystemTrayIcon.MessageIcon = QSystemTrayIcon.MessageIcon.Information,
        msecs: int = 5000,
    ) -> None:
        """
        Отображает системное уведомление Windows через системный трей.

        Args:
            title: Заголовок уведомления.
            message: Текст уведомления.
            icon: Иконка уведомления.
            msecs: Время показа в миллисекундах.
        """
        self._tray_icon.showMessage(title, message, icon, msecs)
