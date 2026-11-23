"""
> [!NOTE]
> Модуль gui.spotlight_bar: Главное плавающее окно GupkaCommander в стиле
> Windows 11 Fluent Dark Glass с векторными иконками QPainter, перетаскиванием
> мышкой, мягкой тенью, живым визуализатором волны и выдвижным ChatDrawer.
"""

from __future__ import annotations

from pathlib import Path
from typing import override

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen
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

from core.paths import get_icon_path
from core.voice.voice_pipeline import PipelineState
from gui.chat_drawer import ChatDrawer, MessageCard
from gui.styles import PALETTE, get_fluent_stylesheet, get_status_badge_qss, get_status_label_ru
from gui.waveform_widget import WaveformWidget


class VectorIconWidget(QWidget):
    """Статичная векторная иконка поиска (лупа), отрисованная сглаживанием QPainter."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("searchIcon")
        self.setFixedSize(26, 26)
        self._color = QColor(PALETTE.accent)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        """Рисует минималистичную векторную лупу в едином стиле Fluent."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        pen = QPen(self._color, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Кольцо лупы
        painter.drawEllipse(QRectF(5.5, 5.5, 11.0, 11.0))
        # Ручка лупы
        painter.drawLine(15, 15, 20, 20)
        painter.end()


class VectorIconButton(QPushButton):
    """Кнопка управления с векторной иконкой QPainter (mic, drawer, settings)."""

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind: str = kind
        self._muted: bool = False
        self._expanded: bool = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(QSize(32, 32))

    def set_muted_state(self, muted: bool) -> None:
        """Обновляет визуальное состояние микрофона (активен / отключен)."""
        if self._muted == muted:
            return
        self._muted = muted
        self.setProperty("muted", "true" if muted else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_expanded_state(self, expanded: bool) -> None:
        """Обновляет направление шеврона раскрытия истории чата."""
        if self._expanded == expanded:
            return
        self._expanded = expanded
        self.update()

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        """Отрисовка фона кнопки по QSS и чёткой векторной иконки поверх него."""
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if self._kind == "mic":
            self._paint_mic(painter)
        elif self._kind == "drawer":
            self._paint_chevron(painter)
        elif self._kind == "settings":
            self._paint_settings(painter)

        painter.end()

    def _paint_mic(self, painter: QPainter) -> None:
        """Рисует векторный микрофон (и красную перечеркивающую линию в mute)."""
        color = QColor(PALETTE.error) if self._muted else QColor(PALETTE.text)
        pen = QPen(color, 1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Капсула микрофона
        painter.drawRoundedRect(QRectF(13.0, 7.5, 6.0, 10.0), 3.0, 3.0)
        # Дуга держателя
        painter.drawArc(QRectF(10.0, 11.5, 12.0, 9.5), 180 * 16, 180 * 16)
        # Стойка и основание
        painter.drawLine(16, 21, 16, 24)
        painter.drawLine(13, 24, 19, 24)

        if self._muted:
            slash_pen = QPen(QColor(PALETTE.error), 2.0)
            slash_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(slash_pen)
            painter.drawLine(9, 8, 23, 23)

    def _paint_chevron(self, painter: QPainter) -> None:
        """Рисует векторный шеврон вниз (свернуто) или вверх (раскрыто)."""
        pen = QPen(QColor(PALETTE.secondary), 1.9)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        if self._expanded:
            painter.drawLine(11, 18, 16, 13)
            painter.drawLine(16, 13, 21, 18)
            return

        painter.drawLine(11, 14, 16, 19)
        painter.drawLine(16, 19, 21, 14)

    def _paint_settings(self, painter: QPainter) -> None:
        """Рисует минималистичную иконку настроек (ползунки управления)."""
        pen = QPen(QColor(PALETTE.secondary), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(QColor(PALETTE.surface))

        # Три горизонтальных направляющих ползунка
        painter.drawLine(9, 11, 23, 11)
        painter.drawLine(9, 16, 23, 16)
        painter.drawLine(9, 21, 23, 21)

        # Круглые регуляторы на разной позиции
        painter.drawEllipse(QRectF(12.0, 9.2, 3.6, 3.6))
        painter.drawEllipse(QRectF(17.5, 14.2, 3.6, 3.6))
        painter.drawEllipse(QRectF(11.0, 19.2, 3.6, 3.6))


class SpotlightBar(QWidget):
    """
    Плавающая панель быстрого запуска и голосового управления GupkaCommander.

    Сочетает полупрозрачный стеклянный контейнер в стиле Windows 11 Spotlight,
    мягкую тень, перетаскивание мышкой, векторные кнопки управления и ChatDrawer.
    """

    DEFAULT_WIDTH: int = 732

    command_submitted = Signal(str)
    visibility_changed = Signal(bool)
    mute_toggled_requested = Signal(bool)
    settings_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        hide_on_focus_loss: bool = False,
        icon_path: Path | str | None = None,
    ) -> None:
        """Инициализация окна SpotlightBar."""
        super().__init__(parent)

        self._pipeline_state: str = "IDLE"
        self._is_muted: bool = False
        self._hide_on_focus_loss: bool = bool(hide_on_focus_loss)
        self._drag_offset: QPoint | None = None
        self.icon_path: Path = Path(icon_path) if icon_path is not None else get_icon_path()

        self._init_window_flags()
        if self.icon_path.exists():
            self.setWindowIcon(QIcon(str(self.icon_path)))
        else:
            self.setWindowIcon(QIcon())
        self._init_ui()
        self._apply_styles()
        self.center_on_screen()

    @property
    def pipeline_state(self) -> str:
        """Возвращает текущее состояние голосового конвейера."""
        return self._pipeline_state

    @property
    def search_input(self) -> QLineEdit:
        """Возвращает виджет строки ввода команды."""
        return self._search_input

    @property
    def waveform(self) -> WaveformWidget:
        """Возвращает виджет визуализатора звуковой волны."""
        return self._waveform

    @property
    def chat_drawer(self) -> ChatDrawer:
        """Возвращает выдвижную панель истории диалога."""
        return self._chat_drawer

    def _init_window_flags(self) -> None:
        """Настройка флагов безрамочного окна поверх всех окон с прозрачным фоном."""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(self.DEFAULT_WIDTH)

    def _init_ui(self) -> None:
        """Построение иерархии виджетов, мягкой тени и панели управления."""
        outer_layout = QVBoxLayout(self)
        # Отступы гарантируют пространство для мягкой тени без обрезки по краям окна
        outer_layout.setContentsMargins(20, 14, 20, 24)
        outer_layout.setSpacing(0)

        self._container = QFrame(self)
        self._container.setObjectName("spotlightBar")

        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # Верхняя строка: иконка поиска, поле ввода, волна, статус и векторные кнопки
        input_row = QWidget(self._container)
        input_layout = QHBoxLayout(input_row)
        input_layout.setContentsMargins(16, 11, 14, 11)
        input_layout.setSpacing(10)

        self._search_icon = VectorIconWidget(input_row)
        input_layout.addWidget(self._search_icon)

        self._search_input = QLineEdit(input_row)
        self._search_input.setObjectName("searchInput")
        self._search_input.setPlaceholderText("Спросите Губку или введите команду...")
        self._search_input.returnPressed.connect(self._on_enter_pressed)
        input_layout.addWidget(self._search_input, stretch=1)

        self._waveform = WaveformWidget(input_row)
        self._waveform.setFixedSize(56, 26)
        input_layout.addWidget(self._waveform)

        self._status_badge = QLabel(get_status_label_ru("IDLE"), input_row)
        self._status_badge.setObjectName("statusBadge")
        self._status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        input_layout.addWidget(self._status_badge, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._mic_btn = VectorIconButton("mic", input_row)
        self._mic_btn.setObjectName("actionIconBtn")
        self._mic_btn.setToolTip("Отключить / включить микрофон")
        self._mic_btn.clicked.connect(self._on_mic_btn_clicked)
        input_layout.addWidget(self._mic_btn)

        self._toggle_drawer_btn = VectorIconButton("drawer", input_row)
        self._toggle_drawer_btn.setObjectName("toggleDrawerBtn")
        self._toggle_drawer_btn.setToolTip("Показать или скрыть историю диалога")
        self._toggle_drawer_btn.clicked.connect(self._on_toggle_drawer_clicked)
        input_layout.addWidget(self._toggle_drawer_btn)

        self._settings_btn = VectorIconButton("settings", input_row)
        self._settings_btn.setObjectName("actionIconBtn")
        self._settings_btn.setToolTip("Открыть настройки")
        self._settings_btn.clicked.connect(self._on_settings_btn_clicked)
        input_layout.addWidget(self._settings_btn)

        container_layout.addWidget(input_row)

        # Тонкий горизонтальный разделитель перед историей диалога
        self._drawer_divider = QFrame(self._container)
        self._drawer_divider.setObjectName("drawerDivider")
        self._drawer_divider.setVisible(False)
        container_layout.addWidget(self._drawer_divider)

        # Выдвижная панель истории чата
        self._chat_drawer = ChatDrawer(parent=self._container)
        self._chat_drawer.expanded_changed.connect(self._on_drawer_expanded_changed)
        self._chat_drawer._animation.valueChanged.connect(self._on_drawer_animation_step)
        self._chat_drawer._animation.finished.connect(self._on_drawer_animation_step)
        container_layout.addWidget(self._chat_drawer)

        outer_layout.addWidget(self._container)
        self.adjustSize()

    def _apply_styles(self) -> None:
        """Применение общей таблицы стилей Fluent Dark Glass."""
        self.setStyleSheet(get_fluent_stylesheet())
        self._refresh_status_badge()

    def _refresh_status_badge(self) -> None:
        """Синхронизирует текст и цвет бейджа состояния с текущими флагами."""
        effective_state = (
            "MUTED" if (self._is_muted and self._pipeline_state == "IDLE") else self._pipeline_state
        )
        self._status_badge.setText(get_status_label_ru(effective_state))
        self._status_badge.setStyleSheet(get_status_badge_qss(effective_state))

    def _sync_drawer_chrome(self) -> None:
        """Синхронизирует видимость разделителя и направление иконки раскрытия."""
        expanded = self.chat_drawer.is_expanded()
        self._drawer_divider.setVisible(expanded)
        self._toggle_drawer_btn.set_expanded_state(expanded)

    def _on_drawer_expanded_changed(self, _expanded: bool) -> None:
        """Обновляет оформление панели при смене состояния раскрытия ChatDrawer."""
        self._sync_drawer_chrome()
        self._on_drawer_animation_step()

    def _on_drawer_animation_step(self, _value: object = None) -> None:
        """Подгоняет высоту плавающего окна во время анимации раскрытия/сворачивания."""
        self.chat_drawer.updateGeometry()
        target_height = self.sizeHint().height()
        if target_height > 0:
            self.resize(self.DEFAULT_WIDTH, target_height)

    def _on_enter_pressed(self) -> None:
        """Отправка непустой команды пользователя при нажатии Enter."""
        text = self.search_input.text().strip()
        if not text:
            return
        self.command_submitted.emit(text)

    def _on_mic_btn_clicked(self) -> None:
        """Переключает состояние микрофона и уведомляет оркестратор."""
        new_muted = not self._is_muted
        self.set_muted(new_muted)
        self.mute_toggled_requested.emit(new_muted)

    def _on_toggle_drawer_clicked(self) -> None:
        """Раскрывает или сворачивает выдвижную историю диалога."""
        self.chat_drawer.toggle()
        self._sync_drawer_chrome()

    def _on_settings_btn_clicked(self) -> None:
        """Отправляет запрос на открытие диалога системных настроек."""
        self.settings_requested.emit()

    # --- Публичный интерфейс ---

    def set_pipeline_state(self, state: str | PipelineState) -> None:
        """
        Обновляет состояние конвейера в визуализаторе волны и статусном бейдже.

        Args:
            state: Новое состояние ('IDLE', 'LISTENING', 'PROCESSING', 'SPEAKING', 'ERROR').
        """
        state_str = state.value if hasattr(state, "value") else str(state)
        norm = state_str.strip().upper()
        self._pipeline_state = norm
        self.waveform.set_state(norm)
        self._refresh_status_badge()

    def set_audio_level(self, level: float) -> None:
        """Передает уровень амплитуды микрофона в виджет волны."""
        self.waveform.set_audio_level(level)

    def set_muted(self, muted: bool) -> None:
        """
        Обновляет визуальное состояние кнопки микрофона без повторного emit сигнала.

        Args:
            muted: True, если микрофон отключен.
        """
        self._is_muted = bool(muted)
        self._mic_btn.set_muted_state(self._is_muted)
        self._refresh_status_badge()

    def add_chat_message(self, role: str, text: str, status: str = "done") -> MessageCard:
        """
        Добавляет карточку в историю чата и автоматически раскрывает панель при необходимости.

        Args:
            role: Роль отправителя ('user', 'assistant', 'action').
            text: Содержимое сообщения.
            status: Статус сообщения ('done', 'running', 'error').

        Returns:
            Созданный объект MessageCard.
        """
        card = self.chat_drawer.add_message(role=role, text=text, status=status)
        if not self.chat_drawer.is_expanded():
            self.chat_drawer.toggle(expand=True)
        self._sync_drawer_chrome()
        return card

    def toggle_chat_drawer(self, expand: bool | None = None) -> bool:
        """Переключает или задает состояние раскрытия выдвижной панели ChatDrawer."""
        result = self.chat_drawer.toggle(expand=expand)
        self._sync_drawer_chrome()
        return result

    def set_input_text(self, text: str) -> None:
        """Устанавливает текст в строку ввода."""
        self.search_input.setText(text)

    def get_input_text(self) -> str:
        """Возвращает текущий текст из строки ввода."""
        return self.search_input.text()

    def clear_input(self) -> None:
        """Очищает строку ввода."""
        self.search_input.clear()

    def set_hide_on_focus_loss(self, enabled: bool) -> None:
        """Включает или отключает автоматическое скрытие при потере фокуса."""
        self._hide_on_focus_loss = bool(enabled)

    def get_hide_on_focus_loss(self) -> bool:
        """Возвращает флаг автоскрытия при потере фокуса."""
        return self._hide_on_focus_loss

    def center_on_screen(self) -> None:
        """Позиционирует окно по центру горизонтали в верхней трети активного экрана."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geom = screen.availableGeometry()
        x = geom.x() + max(0, (geom.width() - self.width()) // 2)
        y = geom.y() + max(24, int(geom.height() * 0.18))
        self.move(x, y)

    def show_bar(self) -> None:
        """Отображает плавающую панель и переводит фокус в строку ввода."""
        self.show()
        self.raise_()
        self.activateWindow()
        self.search_input.setFocus()
        self.visibility_changed.emit(True)

    def hide_bar(self) -> None:
        """Скрывает плавающую панель."""
        self.hide()
        self.visibility_changed.emit(False)

    def toggle_visibility(self) -> None:
        """Переключает видимость окна SpotlightBar."""
        if self.isVisible():
            self.hide_bar()
            return
        self.show_bar()

    # --- События окна и перетаскивание мышкой ---

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        """Рисует мягкую многослойную тень под контейнером без искажения HiDPI-шрифтов."""
        rect = self._container.geometry()
        if not rect.isValid():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)

        # Концентрические слои имитируют рассеянную тень Windows 11 без QGraphicsEffect
        layers = (
            (10, 4, 16),
            (7, 3, 24),
            (4, 2, 36),
            (2, 1, 52),
        )
        for spread, y_shift, alpha in layers:
            shadow_rect = QRectF(
                rect.adjusted(-spread, -spread + y_shift, spread, spread + y_shift)
            )
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(shadow_rect, 18.0 + spread * 0.5, 18.0 + spread * 0.5)

        painter.end()

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Начинает перетаскивание плавающей панели за свободную область."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    @override
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Перемещает панель вслед за курсором при перетаскивании."""
        if self._drag_offset is not None and bool(event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Завершает перетаскивание панели."""
        if event.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Скрывает панель по нажатию клавиши Escape."""
        if event.key() == Qt.Key.Key_Escape:
            self.hide_bar()
            event.accept()
            return
        super().keyPressEvent(event)

    @override
    def changeEvent(self, event: QEvent) -> None:
        """Автоматически скрывает панель при потере фокуса, если опция включена."""
        if event.type() == QEvent.Type.ActivationChange:
            if self._hide_on_focus_loss and not self.isActiveWindow() and self.isVisible():
                self.hide_bar()
        super().changeEvent(event)
