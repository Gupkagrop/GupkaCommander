"""
> [!NOTE]
> Модуль gui.spotlight_bar: Главная плавающая поисковая строка Spotlight в стиле macOS / Raycast.
> Реализует безрамочное полупрозрачное окно поверх всех окон с автоцентрированием,
> встроенным визуализатором звуковых волн (WaveformWidget) и выдвижным ящиком истории (ChatDrawer).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, override

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QCursor, QIcon, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.chat_drawer import ChatDrawer
from gui.styles import FLUENT_DARK_THEME, PALETTE, get_status_badge_qss
from gui.waveform_widget import WaveformWidget

logger = logging.getLogger(__name__)

try:
    from core.voice.voice_pipeline import PipelineState
except ImportError:

    class PipelineState:  # type: ignore[no-redef]
        IDLE = "IDLE"
        LISTENING = "LISTENING"
        PROCESSING = "PROCESSING"
        SPEAKING = "SPEAKING"


class SpotlightBar(QWidget):
    """
    Главное плавающее окно ввода команд SpotlightBar.

    Особенности:
    - Безрамочное (FramelessWindowHint, WindowStaysOnTopHint, Tool).
    - Полупрозрачный фон с закругленными углами и рамкой Windows 11 Fluent Dark.
    - Горизонтальная поисковая строка: иконка поиска, QLineEdit, WaveformWidget, бейдж статуса.
    - Выдвижная панель истории запросов и действий (ChatDrawer).
    - Автоматическое центрирование по активному монитору с позиционированием в верхней трети.
    - Обработка клавиш: Escape скрывает окно, Enter отправляет введенную команду.
    - Контролируемое скрытие при потере фокуса (hide_on_focus_loss).
    """

    command_submitted = Signal(str)
    visibility_changed = Signal(bool)

    DEFAULT_WIDTH: int = 680
    DEFAULT_ICON_PATH: Path = (
        Path(__file__).resolve().parent.parent / "assets" / "gupka_commander.ico"
    )

    def __init__(
        self,
        parent: QWidget | None = None,
        hide_on_focus_loss: bool = False,
        icon_path: Path | str | None = None,
    ) -> None:
        """
        Инициализация панели SpotlightBar.

        Args:
            parent: Родительский виджет.
            hide_on_focus_loss: Автоматически скрывать окно при потере активности.
            icon_path: Путь к файлу системной иконки окна.
        """
        super().__init__(parent)
        self._hide_on_focus_loss = hide_on_focus_loss
        self._icon_path = Path(icon_path) if icon_path is not None else self.DEFAULT_ICON_PATH

        self._init_window_flags()
        self._init_ui()
        self._apply_styles()

    def _init_window_flags(self) -> None:
        """Настройка флагов окна, прозрачности фона и системной иконки."""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(self.DEFAULT_WIDTH)
        if self._icon_path.exists():
            self.setWindowIcon(QIcon(str(self._icon_path)))

    def _init_ui(self) -> None:
        """Построение элементов интерфейса окна."""
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(0)

        # Контейнер с закругленными углами и стилем QFrame#spotlightBar
        self._container = QFrame(self)
        self._container.setObjectName("spotlightBar")
        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(14, 10, 14, 10)
        container_layout.setSpacing(8)

        # 1. Горизонтальная строка ввода и статуса
        self._bar_layout = QHBoxLayout()
        self._bar_layout.setContentsMargins(0, 0, 0, 0)
        self._bar_layout.setSpacing(10)

        # Иконка поиска
        self._search_icon = QLabel("🔍", self._container)
        self._search_icon.setStyleSheet(
            f"font-size: 16px; color: {PALETTE.secondary}; background: transparent;"
        )

        # Поле ввода текста
        self._search_input = QLineEdit(self._container)
        self._search_input.setObjectName("searchInput")
        self._search_input.setPlaceholderText("Введите команду или скажите вслух...")
        self._search_input.returnPressed.connect(self._on_enter_pressed)

        # Визуализатор звуковых волн
        self._waveform = WaveformWidget(self._container)

        # Бейдж статуса
        self._status_badge = QLabel("IDLE", self._container)
        self._status_badge.setObjectName("statusBadge")
        self._status_badge.setStyleSheet(get_status_badge_qss("IDLE"))

        # Кнопка раскрытия истории диалога
        self._toggle_drawer_btn = QPushButton("💬", self._container)
        self._toggle_drawer_btn.setToolTip("Показать/скрыть историю диалога")
        self._toggle_drawer_btn.setFixedSize(30, 30)
        hover_style = (
            "QPushButton { background-color: transparent; border: none; font-size: 14px; }\n"
            "QPushButton:hover { "
            f"background-color: {PALETTE.surface_hover}; border-radius: 6px; "
            "}"
        )
        self._toggle_drawer_btn.setStyleSheet(hover_style)
        self._toggle_drawer_btn.clicked.connect(self._on_toggle_drawer_clicked)

        self._bar_layout.addWidget(self._search_icon)
        self._bar_layout.addWidget(self._search_input, stretch=1)
        self._bar_layout.addWidget(self._waveform)
        self._bar_layout.addWidget(self._status_badge)
        self._bar_layout.addWidget(self._toggle_drawer_btn)

        container_layout.addLayout(self._bar_layout)

        # 2. Встроенный ящик истории (ChatDrawer)
        self._chat_drawer = ChatDrawer(self._container)
        # Связываем изменение высоты анимации с изменением размера окна
        self._chat_drawer._animation.valueChanged.connect(self._on_drawer_animation_step)
        self._chat_drawer._animation.finished.connect(self.adjustSize)
        container_layout.addWidget(self._chat_drawer)

        root_layout.addWidget(self._container)

    def _apply_styles(self) -> None:
        """Применение глобальной таблицы стилей Fluent Dark."""
        self.setStyleSheet(FLUENT_DARK_THEME)

    def _on_drawer_animation_step(self, _: Any) -> None:
        """Корректирует размер окна на каждом кадре анимации ChatDrawer."""
        self.adjustSize()

    def _on_toggle_drawer_clicked(self) -> None:
        """Переключает видимость панели истории."""
        self._chat_drawer.toggle()

    def _on_enter_pressed(self) -> None:
        """Обработка нажатия Enter в строке поиска."""
        text = self._search_input.text().strip()
        if not text:
            return

        logger.debug("Команда отправлена из SpotlightBar: %s", text)
        self.command_submitted.emit(text)

    # --- Публичный интерфейс управления окном ---

    @property
    def search_input(self) -> QLineEdit:
        """Возвращает поле ввода QLineEdit."""
        return self._search_input

    @property
    def waveform(self) -> WaveformWidget:
        """Возвращает виджет визуализации звуковых волн WaveformWidget."""
        return self._waveform

    @property
    def chat_drawer(self) -> ChatDrawer:
        """Возвращает выдвижную панель истории ChatDrawer."""
        return self._chat_drawer

    @property
    def icon_path(self) -> Path:
        """Возвращает путь к системной иконке окна."""
        return self._icon_path

    def set_hide_on_focus_loss(self, enabled: bool) -> None:
        """Включает или выключает автоскрытие при потере фокуса."""
        self._hide_on_focus_loss = enabled

    def get_hide_on_focus_loss(self) -> bool:
        """Возвращает флаг автоскрытия при потере фокуса."""
        return self._hide_on_focus_loss

    def set_input_text(self, text: str) -> None:
        """
        Устанавливает текст в строке поиска.

        Args:
            text: Новое строковое значение.
        """
        self._search_input.setText(text)

    def get_input_text(self) -> str:
        """Возвращает текущий текст строки поиска."""
        return self._search_input.text()

    def clear_input(self) -> None:
        """Очищает строку поиска."""
        self._search_input.clear()

    def set_pipeline_state(self, state: str | PipelineState) -> None:
        """
        Обновляет отображаемое состояние конвейера и визуализатор волн.

        Args:
            state: Состояние конвейера (IDLE, LISTENING, PROCESSING, SPEAKING).
        """
        state_str = state.value if hasattr(state, "value") else str(state)
        normalized = state_str.strip().upper()

        self._waveform.set_state(normalized)
        self._status_badge.setText(normalized)
        self._status_badge.setStyleSheet(get_status_badge_qss(normalized))

    def set_audio_level(self, amplitude: float) -> None:
        """
        Передает текущий уровень громкости аудиосигнала в WaveformWidget.

        Args:
            amplitude: Число от 0.0 до 1.0.
        """
        self._waveform.set_level(amplitude)

    def add_chat_message(self, role: str, text: str, status: str = "done") -> None:
        """
        Добавляет сообщение в панель истории.

        Args:
            role: Роль отправителя ('user', 'assistant', 'action').
            text: Текст сообщения.
            status: Статус карточки ('done', 'running', 'error').
        """
        self._chat_drawer.add_message(role=role, text=text, status=status)
        if not self._chat_drawer.is_expanded():
            self._chat_drawer.toggle(expand=True)

    def toggle_chat_drawer(self, expand: bool | None = None) -> None:
        """
        Раскрывает или сворачивает панель истории.

        Args:
            expand: True/False для явной установки, None для переключения.
        """
        self._chat_drawer.toggle(expand=expand)

    def center_on_screen(self) -> None:
        """Центрирует окно на экране с курсором мыши в верхней трети рабочей области."""
        screen = QApplication.primaryScreen()
        cursor_pos = QCursor.pos()

        for s in QApplication.screens():
            if s.geometry().contains(cursor_pos):
                screen = s
                break

        if screen is not None:
            avail = screen.availableGeometry()
            x = avail.x() + (avail.width() - self.width()) // 2
            # Верхняя треть экрана (характерно для macOS Spotlight и PowerToys Run)
            y = avail.y() + (avail.height() - self.height()) // 4
            self.move(x, max(avail.y() + 40, y))

    def show_bar(self) -> None:
        """Отображает панель Spotlight, поднимает ее поверх всех окон и передает фокус вводу."""
        self.center_on_screen()
        self.show()
        self.raise_()
        self.activateWindow()
        self._search_input.setFocus()
        self.visibility_changed.emit(True)

    def hide_bar(self) -> None:
        """Скрывает окно панели Spotlight."""
        self.hide()
        self.visibility_changed.emit(False)

    def toggle_visibility(self) -> None:
        """Переключает видимость окна между скрытым и активным состоянием."""
        if self.isVisible():
            self.hide_bar()
        else:
            self.show_bar()

    # --- Обработчики системных событий Qt ---

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        """
        Перехват нажатий клавиш окна.

        Клавиша Escape скрывает панель.
        """
        if event.key() == Qt.Key.Key_Escape:
            self.hide_bar()
            event.accept()
            return

        super().keyPressEvent(event)

    @override
    def changeEvent(self, event: QEvent) -> None:
        """
        Отслеживание изменения состояния окна.

        При активации флага _hide_on_focus_loss скрывает окно при потере фокуса.
        """
        if (
            event.type() == QEvent.Type.ActivationChange
            and not self.isActiveWindow()
            and self._hide_on_focus_loss
        ):
            self.hide_bar()

        super().changeEvent(event)
