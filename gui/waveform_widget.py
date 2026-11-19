"""
> [!NOTE]
> Модуль gui.waveform_widget: Кастомный визуализатор звуковых волн и состояний конвейера.
> Использует QPainter для плавного рендеринга анимаций (IDLE, LISTENING, PROCESSING, SPEAKING)
> в стиле Windows 11 Fluent Dark.
"""

import math
from typing import Any, override

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Slot
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from gui.styles import PALETTE

try:
    from core.voice.voice_pipeline import PipelineState
except ImportError:
    # Запасной вариант перечисления для независимого запуска
    class PipelineState:  # type: ignore[no-redef]
        IDLE = "IDLE"
        LISTENING = "LISTENING"
        PROCESSING = "PROCESSING"
        SPEAKING = "SPEAKING"


class WaveformWidget(QWidget):
    """
    Виджет визуализации голосового состояния и амплитуды входящего аудиосигнала.

    Отображает:
    - IDLE: спокойная пульсирующая точка.
    - LISTENING: динамические звуковые полосы (Waveform), реагирующие на амплитуду.
    - PROCESSING: кольцевой индикатор загрузки/обработки.
    - SPEAKING: ритмичные волны синтезированной речи.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Инициализация виджета визуализации."""
        super().__init__(parent)

        self._state: str = "IDLE"
        self._level: float = 0.0
        self._target_level: float = 0.0
        self._phase: float = 0.0

        # Таймер анимации (~33 FPS)
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._on_animation_tick)

        self.setMinimumSize(48, 28)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.start_animation()

    @override
    def sizeHint(self) -> QSize:
        """Возвращает предпочтительный размер виджета."""
        return QSize(64, 32)

    def set_state(self, state: str | PipelineState) -> None:
        """
        Устанавливает текущее состояние конвейера.

        Args:
            state: Имя состояния или экземпляр PipelineState.
        """
        raw_val = state.value if hasattr(state, "value") else str(state)
        normalized = raw_val.strip().upper()

        if normalized not in ("IDLE", "LISTENING", "PROCESSING", "SPEAKING"):
            normalized = "IDLE"

        if self._state != normalized:
            self._state = normalized
            self.update()

    def get_state(self) -> str:
        """Возвращает текущее состояние в виде строки."""
        return self._state

    def set_level(self, amplitude: float) -> None:
        """
        Устанавливает уровень громкости звукового сигнала.

        Args:
            amplitude: Число от 0.0 до 1.0.
        """
        clamped = max(0.0, min(1.0, float(amplitude)))
        self._target_level = clamped
        # Быстрое сглаживание для исключения резких скачков
        self._level = 0.4 * self._target_level + 0.6 * self._level
        self.update()

    @Slot(float)
    def set_audio_level(self, amplitude: float) -> None:
        """
        Устанавливает уровень громкости звукового сигнала (Qt-слот для VoiceBridge).

        Args:
            amplitude: Число от 0.0 до 1.0.
        """
        self.set_level(amplitude)

    def start_animation(self) -> None:
        """Запускает таймер анимации виджета."""
        if not self._timer.isActive():
            self._timer.start()

    def stop_animation(self) -> None:
        """Останавливает таймер анимации."""
        if self._timer.isActive():
            self._timer.stop()

    def _on_animation_tick(self) -> None:
        """Шаг таймера обновления фазы анимации."""
        self._phase += 0.1
        if self._phase >= 2.0 * math.pi:
            self._phase -= 2.0 * math.pi

        # Экспоненциальный спад уровня при отсутствии нового звука
        self._level = max(0.0, self._level * 0.92)
        self.update()

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        """
        Отрисовка состояния виджета в зависимости от _state.

        Args:
            event: Событие отрисовки Qt.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        width = float(self.width())
        height = float(self.height())
        cx = width / 2.0
        cy = height / 2.0

        if self._state == "IDLE":
            self._draw_idle(painter, cx, cy)
        elif self._state == "LISTENING":
            self._draw_listening(painter, cx, cy, height)
        elif self._state == "PROCESSING":
            self._draw_processing(painter, cx, cy, min(width, height))
        elif self._state == "SPEAKING":
            self._draw_speaking(painter, cx, cy, height)
        else:
            self._draw_idle(painter, cx, cy)

        painter.end()

    def _draw_idle(self, painter: QPainter, cx: float, cy: float) -> None:
        """Отрисовка спокойного состояния ожидания."""
        pulse = (math.sin(self._phase) + 1.0) / 2.0
        dot_radius = 3.0 + 1.5 * pulse
        halo_radius = dot_radius + 4.0

        # Внешний ореол
        halo_color = QColor(PALETTE.secondary)
        halo_color.setAlpha(int(30 + 40 * pulse))
        painter.setBrush(halo_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), halo_radius, halo_radius)

        # Центральная точка
        center_color = QColor(PALETTE.secondary)
        center_color.setAlpha(int(180 + 75 * pulse))
        painter.setBrush(center_color)
        painter.drawEllipse(QPointF(cx, cy), dot_radius, dot_radius)

    def _draw_listening(self, painter: QPainter, cx: float, cy: float, total_height: float) -> None:
        """Отрисовка полос эквалайзера при прослушивании."""
        accent_color = QColor(PALETTE.accent)
        bar_count = 5
        bar_width = 3.0
        bar_gap = 3.5
        total_bars_width = bar_count * bar_width + (bar_count - 1) * bar_gap
        start_x = cx - (total_bars_width / 2.0)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent_color)

        for i in range(bar_count):
            offset = (i - 2) * 0.75
            sine_wave = (math.sin(self._phase * 2.2 + offset) + 1.0) / 2.0
            factor = 0.2 + 0.8 * self._level * (0.35 + 0.65 * sine_wave)
            bar_h = max(4.0, (total_height - 8.0) * factor)

            x = start_x + i * (bar_width + bar_gap)
            y = cy - (bar_h / 2.0)
            painter.drawRoundedRect(QRectF(x, y, bar_width, bar_h), 1.5, 1.5)

    def _draw_processing(self, painter: QPainter, cx: float, cy: float, side_len: float) -> None:
        """Отрисовка вращающегося индикатора обработки команды."""
        diameter = max(12.0, side_len - 10.0)
        radius = diameter / 2.0
        rect = QRectF(cx - radius, cy - radius, diameter, diameter)

        # Фоновая окружность
        bg_pen = QPen(QColor(PALETTE.border_subtle), 2.0)
        painter.setPen(bg_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect)

        # Активная вращающаяся дуга
        start_angle = int((self._phase * 180.0 / math.pi * 1.5) % 360.0 * 16.0)
        span_angle = int(100.0 * 16.0)

        arc_pen = QPen(QColor(PALETTE.warning), 2.5)
        arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc_pen)
        painter.drawArc(rect, start_angle, span_angle)

    def _draw_speaking(self, painter: QPainter, cx: float, cy: float, total_height: float) -> None:
        """Отрисовка ритмичной волны синтеза речи."""
        success_color = QColor(PALETTE.success)
        bar_count = 5
        bar_width = 3.0
        bar_gap = 3.5
        total_bars_width = bar_count * bar_width + (bar_count - 1) * bar_gap
        start_x = cx - (total_bars_width / 2.0)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(success_color)

        for i in range(bar_count):
            cadence = math.sin(self._phase * 1.8 + i * 0.9) ** 2
            bar_h = 5.0 + (total_height - 10.0) * (0.35 + 0.65 * cadence)

            x = start_x + i * (bar_width + bar_gap)
            y = cy - (bar_h / 2.0)
            painter.drawRoundedRect(QRectF(x, y, bar_width, bar_h), 1.5, 1.5)

    @override
    def hideEvent(self, event: Any) -> None:
        """Остановка таймера анимации при скрытии виджета для экономии ресурсов."""
        self.stop_animation()
        super().hideEvent(event)

    @override
    def showEvent(self, event: Any) -> None:
        """Возобновление таймера анимации при отображении виджета."""
        super().showEvent(event)
        self.start_animation()

    @override
    def closeEvent(self, event: Any) -> None:
        """Остановка таймера при закрытии виджета."""
        self.stop_animation()
        super().closeEvent(event)
