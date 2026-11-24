"""
> [!NOTE]
> Модуль gui.sponge_pore_widget: Тактильный микропористый индикатор «Губка» (SpongePoreWidget)
> и кешированный генератор тайла 64x64 для зоны захвата (build_micro_pore_tile).
> Обеспечивает 0 FPS в покое, 15 FPS при наведении и 60 FPS в активных фазах конвейера.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import override

from PySide6.QtCore import QEvent, QPointF, QSize, Qt, QTimer, Slot
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QHideEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QRadialGradient,
    QShowEvent,
)
from PySide6.QtWidgets import QWidget

from gui.theme_manager import DARK_TOKENS, ThemeTokens

# Интервалы таймера (мс) для адаптивного энергосбережения
_INTERVAL_HOVER_15_FPS_MS = 67
_INTERVAL_ACTIVE_60_FPS_MS = 16


class PoreState(StrEnum):
    """Состояния физической анимации микропористой матрицы."""

    IDLE = "IDLE"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"
    MUTED = "MUTED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class _PoreSpec:
    """Нормализованные координаты и базовый относительный радиус одной поры."""

    nx: float
    ny: float
    radius_ratio: float
    diag_order: float


# Органический кластер из 9 микропор (3x3 с естественным смещением и разбросом радиусов)
_PORE_CLUSTER: tuple[_PoreSpec, ...] = (
    _PoreSpec(-0.62, -0.58, 0.105, 0.00),
    _PoreSpec(0.03, -0.66, 0.118, 0.22),
    _PoreSpec(0.64, -0.54, 0.095, 0.44),
    _PoreSpec(-0.66, 0.04, 0.116, 0.25),
    _PoreSpec(0.00, 0.00, 0.148, 0.50),
    _PoreSpec(0.65, 0.06, 0.110, 0.72),
    _PoreSpec(-0.58, 0.63, 0.096, 0.54),
    _PoreSpec(-0.02, 0.65, 0.115, 0.78),
    _PoreSpec(0.60, 0.60, 0.104, 1.00),
)

# Позиции и радиусы точек в тайле 64x64 для тактильной полосы перетаскивания
_TILE_PORES: tuple[tuple[float, float, float], ...] = (
    (9.0, 10.0, 1.45),
    (26.0, 8.0, 1.15),
    (44.0, 12.0, 1.60),
    (58.0, 9.0, 1.10),
    (16.0, 24.0, 1.25),
    (34.0, 26.0, 1.70),
    (52.0, 25.0, 1.30),
    (8.0, 41.0, 1.55),
    (25.0, 43.0, 1.20),
    (42.0, 40.0, 1.45),
    (59.0, 44.0, 1.25),
    (17.0, 56.0, 1.35),
    (35.0, 58.0, 1.15),
    (51.0, 57.0, 1.50),
)

_TileCacheKey = tuple[str, str, int, float, int]
_MICRO_PORE_TILE_CACHE: dict[_TileCacheKey, QPixmap] = {}


def clear_micro_pore_tile_cache() -> None:
    """Очищает кеш сгенерированных тайлов микропористой текстуры."""
    _MICRO_PORE_TILE_CACHE.clear()


def _paint_micro_pore_pixmap(tokens: ThemeTokens, safe_dpr: float, safe_size: int) -> QPixmap:
    """Отрисовывает новый бесшовный QPixmap микропористого узора."""
    phys_size = max(16, int(round(safe_size * safe_dpr)))
    pixmap = QPixmap(phys_size, phys_size)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(safe_dpr)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)

    scale = safe_size / 64.0
    base_color = QColor(tokens.pore_tile_color)
    base_alpha = max(1, min(255, tokens.pore_tile_alpha))

    for idx, (px, py, radius) in enumerate(_TILE_PORES):
        dot_color = QColor(base_color)
        dot_color.setAlpha(min(255, base_alpha + (3 if idx % 3 == 0 else 0)))
        painter.setBrush(dot_color)
        painter.drawEllipse(QPointF(px * scale, py * scale), radius * scale, radius * scale)

    painter.end()
    return pixmap


def build_micro_pore_tile(
    tokens: ThemeTokens,
    dpr: float = 1.0,
    tile_size: int = 64,
) -> QPixmap:
    """
    Генерирует и кеширует бесшовный тайл 64x64 микропористого узора (4% opacity).

    Тайл переиспользуется для тактильной зоны захвата (Drag Strip) и
    пересоздаётся только при смене темы или масштаба DPI.
    """
    safe_dpr = max(1.0, round(float(dpr), 2))
    safe_size = max(16, int(tile_size))
    cache_key: _TileCacheKey = (
        tokens.name,
        tokens.pore_tile_color,
        tokens.pore_tile_alpha,
        safe_dpr,
        safe_size,
    )
    cached = _MICRO_PORE_TILE_CACHE.get(cache_key)
    if cached is not None and not cached.isNull():
        return cached

    pixmap = _paint_micro_pore_pixmap(tokens, safe_dpr, safe_size)
    _MICRO_PORE_TILE_CACHE[cache_key] = pixmap
    return pixmap


def _coerce_pore_state(state: str | PoreState | Enum) -> PoreState:
    """Приводит строковое или enum-состояние к каноническому PoreState."""
    raw = state.value if isinstance(state, Enum) else str(state)
    norm = str(raw).strip().upper()
    try:
        return PoreState(norm)
    except ValueError:
        return PoreState.IDLE


class SpongePoreWidget(QWidget):
    """
    Тактильный визуализатор микропористой матрицы «Губка».

    Режимы энергопотребления и физики:
    - IDLE: 0 FPS в покое, 15 FPS мягкое дыхание только при наведении курсора;
    - LISTENING: 60 FPS «впитывание» к центру в цвете акцента Win11 (#60CDFF);
    - PROCESSING: 60 FPS диагональная фильтрационная волна Sponge Ochre (#E9B44C);
    - SPEAKING: 60 FPS радиальная отдача от центра в нефритовом цвете (#3DD68C);
    - MUTED / ERROR: 0 FPS статичная индикация в коралловом цвете (#FF5C52).
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        tokens: ThemeTokens = DARK_TOKENS,
        size: int = 34,
    ) -> None:
        """Инициализирует микропористый виджет в статичном режиме 0 FPS."""
        super().__init__(parent)
        self._tokens: ThemeTokens = tokens
        self._state: PoreState = PoreState.IDLE
        self._preferred_size: int = max(20, int(size))
        self._hovered: bool = False
        self._collapsed: bool = False
        self._hidden_by_event: bool = False
        self._audio_level: float = 0.0
        self._target_audio_level: float = 0.0
        self._phase: float = 0.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)

        self.setMinimumSize(20, 20)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._sync_timer()

    @override
    def sizeHint(self) -> QSize:
        """Возвращает квадратный предпочтительный размер матрицы пор."""
        return QSize(self._preferred_size, self._preferred_size)

    def set_tokens(self, tokens: ThemeTokens) -> None:
        """Обновляет цветовую палитру виджета при смене темы Dark/Light."""
        if self._tokens != tokens:
            self._tokens = tokens
            self.update()

    def set_state(self, state: str | PoreState | Enum) -> None:
        """Устанавливает состояние конвейера и перенастраивает частоту кадров таймера."""
        next_state = _coerce_pore_state(state)
        if next_state == self._state:
            self._sync_timer()
            return
        self._state = next_state
        self._sync_timer()
        self.update()

    def get_state(self) -> str:
        """Возвращает строковое имя текущего состояния."""
        return self._state.value

    def set_collapsed(self, collapsed: bool) -> None:
        """Переводит виджет в свёрнутый режим (принудительные 0 FPS)."""
        self._collapsed = bool(collapsed)
        self._sync_timer()
        self.update()

    @Slot(float)
    def set_audio_level(self, amplitude: float) -> None:
        """Обновляет уровень входящего голоса (0.0..1.0) для физики «впитывания»."""
        clamped = max(0.0, min(1.0, float(amplitude)))
        self._target_audio_level = clamped
        self._audio_level = 0.45 * clamped + 0.55 * self._audio_level
        self.update()

    def is_animating(self) -> bool:
        """Возвращает True, если таймер анимации сейчас активен."""
        return self._timer.isActive()

    def current_fps(self) -> int:
        """Возвращает целевую частоту кадров таймера (0, 15 или 60 FPS)."""
        if not self._timer.isActive():
            return 0
        interval = self._timer.interval()
        if interval <= _INTERVAL_ACTIVE_60_FPS_MS:
            return 60
        return 15

    def set_phase_for_preview(self, phase: float) -> None:
        """Фиксирует фазу анимации для детерминированной отрисовки превью."""
        self._phase = float(phase)
        self.update()

    def _desired_interval_ms(self) -> int:
        """Вычисляет интервал таймера: 0 (выкл), 67 мс (15 FPS) или 16 мс (60 FPS)."""
        if self._collapsed or self._hidden_by_event:
            return 0
        if self._state in (PoreState.LISTENING, PoreState.PROCESSING, PoreState.SPEAKING):
            return _INTERVAL_ACTIVE_60_FPS_MS
        if self._state == PoreState.IDLE and self._hovered:
            return _INTERVAL_HOVER_15_FPS_MS
        return 0

    def _sync_timer(self) -> None:
        """Запускает, перестраивает или останавливает QTimer согласно текущему режиму."""
        interval_ms = self._desired_interval_ms()
        if interval_ms <= 0:
            if self._timer.isActive():
                self._timer.stop()
            return
        if self._timer.interval() != interval_ms or not self._timer.isActive():
            self._timer.start(interval_ms)

    def _on_tick(self) -> None:
        """Выполняет шаг физики пор и плавное затухание уровня громкости."""
        step = 0.11 if self._timer.interval() <= _INTERVAL_ACTIVE_60_FPS_MS else 0.14
        self._phase = (self._phase + step) % (2.0 * math.pi)
        self._audio_level = max(0.0, self._audio_level * 0.92)
        self.update()

    @override
    def enterEvent(self, event: QEnterEvent) -> None:
        """Пробуждает плавное 15 FPS дыхание в состоянии IDLE при наведении."""
        self._hovered = True
        self._sync_timer()
        super().enterEvent(event)

    @override
    def leaveEvent(self, event: QEvent) -> None:
        """Возвращает состояние IDLE в нулевое энергопотребление (0 FPS)."""
        self._hovered = False
        self._sync_timer()
        self.update()
        super().leaveEvent(event)

    @override
    def hideEvent(self, event: QHideEvent) -> None:
        """Останавливает таймер при скрытии виджета для экономии CPU."""
        self._hidden_by_event = True
        self._sync_timer()
        super().hideEvent(event)

    @override
    def showEvent(self, event: QShowEvent) -> None:
        """Восстанавливает работу таймера при отображении виджета."""
        self._hidden_by_event = False
        self._sync_timer()
        super().showEvent(event)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        """Отрисовывает микропористую матрицу с антиалиасингом и физикой состояния."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        side = float(min(self.width(), self.height()))
        cx = float(self.width()) / 2.0
        cy = float(self.height()) / 2.0
        extent = max(6.0, (side / 2.0) - 3.5)

        self._draw_backdrop_halo(painter, cx, cy, extent)
        self._draw_pores(painter, cx, cy, extent)
        self._draw_state_overlay(painter, cx, cy, extent)
        painter.end()

    def _state_color(self) -> QColor:
        """Возвращает основной цветовой акцент для текущего состояния."""
        color_map: dict[PoreState, str] = {
            PoreState.IDLE: self._tokens.pore_idle,
            PoreState.LISTENING: self._tokens.accent,
            PoreState.PROCESSING: self._tokens.sponge_ochre,
            PoreState.SPEAKING: self._tokens.success,
            PoreState.MUTED: self._tokens.danger,
            PoreState.ERROR: self._tokens.danger,
        }
        return QColor(color_map[self._state])

    def _draw_backdrop_halo(
        self,
        painter: QPainter,
        cx: float,
        cy: float,
        extent: float,
    ) -> None:
        """Рисует мягкое радиальное свечение под активными состояниями."""
        if self._state in (PoreState.IDLE, PoreState.MUTED):
            return

        halo_color = self._state_color()
        intensity = 0.24
        if self._state == PoreState.LISTENING:
            intensity = 0.18 + 0.22 * self._audio_level
        elif self._state == PoreState.SPEAKING:
            intensity = 0.18 + 0.14 * ((math.sin(self._phase * 1.6) + 1.0) / 2.0)

        gradient = QRadialGradient(QPointF(cx, cy), extent * 1.15)
        inner = QColor(halo_color)
        inner.setAlpha(int(255 * intensity))
        outer = QColor(halo_color)
        outer.setAlpha(0)
        gradient.setColorAt(0.0, inner)
        gradient.setColorAt(1.0, outer)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(QPointF(cx, cy), extent * 1.12, extent * 1.12)

    def _pore_transform(self, spec: _PoreSpec) -> tuple[float, float, float, float]:
        """Вычисляет смещённые (nx, ny), масштаб радиуса и яркость (0..1) одной поры."""
        if self._state == PoreState.IDLE:
            breath = 0.08 * math.sin(self._phase + spec.diag_order * 2.0) if self._hovered else 0.0
            return (spec.nx, spec.ny, 1.0 + breath, 0.72 if self._hovered else 0.62)

        if self._state == PoreState.LISTENING:
            pull = 0.22 * self._audio_level + 0.06 * ((math.sin(self._phase * 2.0) + 1.0) / 2.0)
            scale = 0.85 + 0.35 * self._audio_level
            if spec.nx == 0.0 and spec.ny == 0.0:
                scale = 1.05 + 0.30 * self._audio_level
            return (spec.nx * (1.0 - pull), spec.ny * (1.0 - pull), scale, 0.92)

        if self._state == PoreState.PROCESSING:
            wave = (math.sin(self._phase * 1.8 - spec.diag_order * math.pi * 1.6) + 1.0) / 2.0
            return (spec.nx, spec.ny, 0.76 + 0.44 * wave, 0.48 + 0.52 * wave)

        if self._state == PoreState.SPEAKING:
            dist = math.hypot(spec.nx, spec.ny)
            pulse = (math.sin(self._phase * 2.0 - dist * 3.0) + 1.0) / 2.0
            push = 1.0 + 0.09 * pulse
            return (spec.nx * push, spec.ny * push, 0.82 + 0.36 * pulse, 0.58 + 0.42 * pulse)

        if self._state == PoreState.MUTED:
            return (spec.nx, spec.ny, 0.88, 0.55)

        return (spec.nx, spec.ny, 1.02, 0.95)

    def _draw_pores(
        self,
        painter: QPainter,
        cx: float,
        cy: float,
        extent: float,
    ) -> None:
        """Отрисовывает все 9 микропор матрицы с учётом текущей физики состояния."""
        base_color = self._state_color()
        painter.setPen(Qt.PenStyle.NoPen)

        for spec in _PORE_CLUSTER:
            nx, ny, radius_scale, opacity = self._pore_transform(spec)
            px = cx + nx * extent
            py = cy + ny * extent
            radius = max(1.1, spec.radius_ratio * extent * 1.65 * radius_scale)

            pore_color = QColor(base_color)
            pore_color.setAlpha(int(255 * max(0.15, min(1.0, opacity))))
            painter.setBrush(pore_color)
            painter.drawEllipse(QPointF(px, py), radius, radius)

    def _draw_state_overlay(
        self,
        painter: QPainter,
        cx: float,
        cy: float,
        extent: float,
    ) -> None:
        """Рисует вспомогательный штрих для MUTED или тонкий ободок тревоги для ERROR."""
        if self._state == PoreState.MUTED:
            pen = QPen(self._state_color(), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            delta = extent * 0.82
            painter.drawLine(QPointF(cx - delta, cy - delta), QPointF(cx + delta, cy + delta))
            return

        if self._state == PoreState.ERROR:
            ring_color = self._state_color()
            ring_color.setAlpha(155)
            painter.setPen(QPen(ring_color, 1.3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cx, cy), extent * 0.98, extent * 0.98)
