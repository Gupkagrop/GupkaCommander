"""
> [!NOTE]
> Модуль gui.spotlight_bar: Главное пристыковываемое окно GupkaCommander 2.0
> в стиле Windows 11 Fluent 2 Docked Blade & Micro-Pore Matrix (Concept B).
> Интегрирует ThemeManager (Dark/Light + DWM Acrylic/Mica), мультимониторный
> DockManager (8 зон стыковки, авто-инверсия снизу), SpongePoreWidget (0 FPS в покое),
> ContextChipBar, AgentMiniBar ([Esc · Стоп]) и выдвижной ChatDrawer.
"""

from __future__ import annotations

from pathlib import Path
from typing import override

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QIcon,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QShowEvent,
)
from PySide6.QtWidgets import (
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
from gui.agent_widgets import AgentMiniBar, ContextChipBar, StepCard
from gui.chat_drawer import ChatDrawer, MessageCard
from gui.dock_manager import (
    DisplayMode,
    DockGeometry,
    DockManager,
    DockPosition,
    coerce_display_mode,
    coerce_dock_position,
    compute_corner_radii,
    compute_flat_edges,
    should_invert_input_order,
)
from gui.sponge_pore_widget import PoreState, SpongePoreWidget, build_micro_pore_tile
from gui.styles import get_status_label_ru
from gui.theme_manager import (
    DARK_TOKENS,
    BackdropType,
    ThemeManager,
    ThemeMode,
    ThemeTokens,
    apply_win11_backdrop,
    build_blade_stylesheet,
)
from gui.waveform_widget import WaveformWidget

_SHADOW_MARGIN = 12
_SHADOW_BOTTOM_MARGIN = 14


def _build_asymmetric_rounded_path(
    rect: QRectF,
    radii: tuple[int, int, int, int],
) -> QPainterPath:
    """Строит QPainterPath с индивидуальными радиусами углов (tl, tr, br, bl)."""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    max_r = min(w, h) / 2.0
    tl, tr, br, bl = (min(float(max(0, r)), max_r) for r in radii)

    path = QPainterPath()
    path.moveTo(x + tl, y)
    path.lineTo(x + w - tr, y)
    if tr > 0.0:
        path.arcTo(QRectF(x + w - 2.0 * tr, y, 2.0 * tr, 2.0 * tr), 90.0, -90.0)
    path.lineTo(x + w, y + h - br)
    if br > 0.0:
        path.arcTo(QRectF(x + w - 2.0 * br, y + h - 2.0 * br, 2.0 * br, 2.0 * br), 0.0, -90.0)
    path.lineTo(x + bl, y + h)
    if bl > 0.0:
        path.arcTo(QRectF(x, y + h - 2.0 * bl, 2.0 * bl, 2.0 * bl), 270.0, -90.0)
    path.lineTo(x, y + tl)
    if tl > 0.0:
        path.arcTo(QRectF(x, y, 2.0 * tl, 2.0 * tl), 180.0, -90.0)
    path.closeSubpath()
    return path


def _status_badge_qss_for_tokens(state: str, tokens: ThemeTokens) -> str:
    """Формирует QSS-стиль бейджа статуса для активной темы Fluent 2."""
    norm = state.strip().upper()
    if norm == "LISTENING":
        fg, bg, border = tokens.accent, tokens.accent_soft, tokens.accent_border
    elif norm == "SPEAKING":
        fg, bg, border = tokens.success, tokens.success_soft, tokens.success_border
    elif norm == "PROCESSING":
        fg, bg, border = tokens.sponge_ochre, tokens.sponge_ochre_soft, tokens.sponge_ochre_border
    elif norm in ("ERROR", "MUTED"):
        fg, bg, border = tokens.danger, tokens.danger_soft, tokens.danger_border
    else:
        fg, bg, border = tokens.text_secondary, tokens.surface_chip, tokens.border_subtle

    return (
        "QLabel#statusBadge {"
        f"  color: {fg}; background-color: {bg}; border: 1px solid {border};"
        "  border-radius: 12px; padding: 4px 11px;"
        "  font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Inter', sans-serif;"
        "  font-size: 11px; font-weight: 600;"
        "}"
    )


def _spotlight_controls_qss(tokens: ThemeTokens) -> str:
    """Дополнительные правила QSS для кнопок, разделителя и карточек SpotlightBar."""
    return f"""
    QFrame#drawerDivider {{
        background-color: {tokens.divider};
        border: none;
        min-height: 1px;
        max-height: 1px;
    }}

    QPushButton#toggleDrawerBtn,
    QPushButton#actionIconBtn {{
        background-color: {tokens.surface_chip};
        border: 1px solid transparent;
        border-radius: 10px;
        padding: 0px;
    }}

    QPushButton#toggleDrawerBtn:hover,
    QPushButton#actionIconBtn:hover {{
        background-color: {tokens.surface_chip_hover};
        border: 1px solid {tokens.border};
    }}

    QPushButton#actionIconBtn[muted="true"] {{
        background-color: {tokens.danger_soft};
        border: 1px solid {tokens.danger_border};
    }}

    QPushButton#actionIconBtn[muted="true"]:hover {{
        background-color: {tokens.danger_border};
    }}
    """


class VectorIconWidget(QWidget):
    """Статичная векторная иконка поиска (лупа), отрисованная сглаживанием QPainter."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        tokens: ThemeTokens = DARK_TOKENS,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("searchIcon")
        self.setFixedSize(26, 26)
        self._color = QColor(tokens.accent)

    def set_tokens(self, tokens: ThemeTokens) -> None:
        """Обновляет цвет иконки при переключении темы Dark/Light."""
        self._color = QColor(tokens.accent)
        self.update()

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
        painter.drawEllipse(QRectF(5.5, 5.5, 11.0, 11.0))
        painter.drawLine(15, 15, 20, 20)
        painter.end()


class VectorIconButton(QPushButton):
    """Кнопка управления с векторной иконкой QPainter (mic, drawer, settings)."""

    def __init__(
        self,
        kind: str,
        parent: QWidget | None = None,
        *,
        tokens: ThemeTokens = DARK_TOKENS,
    ) -> None:
        super().__init__(parent)
        self._kind: str = kind
        self._muted: bool = False
        self._expanded: bool = False
        self._tokens: ThemeTokens = tokens
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(QSize(32, 32))

    def set_tokens(self, tokens: ThemeTokens) -> None:
        """Обновляет цветовую палитру иконки при смене темы."""
        self._tokens = tokens
        self.update()

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
        """Рисует векторный микрофон и перечёркивание в состоянии mute."""
        color = QColor(self._tokens.danger if self._muted else self._tokens.text)
        pen = QPen(color, 1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        painter.drawRoundedRect(QRectF(13.0, 7.5, 6.0, 10.0), 3.0, 3.0)
        painter.drawArc(QRectF(10.0, 11.5, 12.0, 9.5), 180 * 16, 180 * 16)
        painter.drawLine(16, 21, 16, 24)
        painter.drawLine(13, 24, 19, 24)

        if self._muted:
            slash_pen = QPen(QColor(self._tokens.danger), 2.0)
            slash_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(slash_pen)
            painter.drawLine(9, 8, 23, 23)

    def _paint_chevron(self, painter: QPainter) -> None:
        """Рисует векторный шеврон вниз (свёрнуто) или вверх (раскрыто)."""
        pen = QPen(QColor(self._tokens.text_secondary), 1.9)
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
        pen = QPen(QColor(self._tokens.text_secondary), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(QColor(self._tokens.surface))

        painter.drawLine(9, 11, 23, 11)
        painter.drawLine(9, 16, 23, 16)
        painter.drawLine(9, 21, 23, 21)

        painter.drawEllipse(QRectF(12.0, 9.2, 3.6, 3.6))
        painter.drawEllipse(QRectF(17.5, 14.2, 3.6, 3.6))
        painter.drawEllipse(QRectF(11.0, 19.2, 3.6, 3.6))


class MicroPoreDragStrip(QWidget):
    """Тактильная полоса захвата с кешированным микропористым узором у края стыковки."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        tokens: ThemeTokens = DARK_TOKENS,
        edge_at_bottom: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("microPoreDragStrip")
        self._tokens: ThemeTokens = tokens
        self._edge_at_bottom: bool = edge_at_bottom
        self.setFixedHeight(14)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip("Перетащите панель для перемещения или стыковки")

    def set_tokens(self, tokens: ThemeTokens) -> None:
        """Обновляет палитру тайла и ручки захвата."""
        self._tokens = tokens
        self.update()

    def set_edge_at_bottom(self, at_bottom: bool) -> None:
        """Переключает положение тонкой акцентной кромки (верх / низ)."""
        self._edge_at_bottom = bool(at_bottom)
        self.update()

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        """Рисует микропористый узор из кеша, акцентную линию края и пилюлю захвата."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        dpr = max(1.0, float(self.devicePixelRatioF()))
        tile = build_micro_pore_tile(self._tokens, dpr=dpr)
        painter.drawTiledPixmap(self.rect(), tile)

        # Тонкая акцентная линия Sponge Ochre вдоль внешнего стыкованного края
        accent = QColor(self._tokens.sponge_ochre)
        accent.setAlpha(155)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        line_y = float(self.height() - 1) if self._edge_at_bottom else 0.0
        painter.drawRect(QRectF(0.0, line_y, float(self.width()), 1.5))

        # Центральная тактильная пилюля захвата
        pill_w = min(40.0, max(20.0, self.width() * 0.14))
        pill_h = 3.0
        pill_x = (self.width() - pill_w) / 2.0
        pill_y = (self.height() - pill_h) / 2.0
        pill_color = QColor(self._tokens.sponge_ochre)
        pill_color.setAlpha(205)
        painter.setBrush(pill_color)
        painter.drawRoundedRect(QRectF(pill_x, pill_y, pill_w, pill_h), 1.5, 1.5)
        painter.end()


class CollapsedEdgePill(QWidget):
    """Компактная свёрнутая кромка-пилюля (уровень плотности COLLAPSED_PILL)."""

    clicked = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        tokens: ThemeTokens = DARK_TOKENS,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("collapsedEdgePill")
        self._tokens: ThemeTokens = tokens
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Нажмите, чтобы развернуть GupkaCommander")

    def set_tokens(self, tokens: ThemeTokens) -> None:
        """Обновляет палитру свёрнутой кромки."""
        self._tokens = tokens
        self.update()

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Разворачивает панель при клике левой кнопкой мыши."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        """Отрисовывает микропористую перфорацию и фирменную точку Sponge Ochre."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        dpr = max(1.0, float(self.devicePixelRatioF()))
        painter.drawTiledPixmap(self.rect(), build_micro_pore_tile(self._tokens, dpr=dpr))

        accent = QColor(self._tokens.sponge_ochre)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        if self.height() > self.width():
            pill = QRectF(
                (self.width() - 4.0) / 2.0, self.height() * 0.25, 4.0, self.height() * 0.5
            )
        else:
            pill = QRectF(
                self.width() * 0.28, (self.height() - 4.0) / 2.0, self.width() * 0.44, 4.0
            )
        painter.drawRoundedRect(pill, 2.0, 2.0)
        painter.end()


class SpotlightBar(QWidget):
    """
    Главное пристыковываемое окно GupkaCommander 2.0 (Fluent 2 Docked Blade).

    Поддерживает 8 зон стыковки, мультимониторность, автоматическую Dark/Light
    тему Windows 11 с DWM Acrylic/Mica, живой SpongePoreWidget, ContextChipBar,
    Live Agent Mini-Bar с экстренным прерыванием [Esc · Стоп] и ChatDrawer.
    """

    DEFAULT_WIDTH: int = 732

    command_submitted = Signal(str)
    visibility_changed = Signal(bool)
    mute_toggled_requested = Signal(bool)
    settings_requested = Signal()
    abort_requested = Signal()
    dock_position_changed = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        hide_on_focus_loss: bool = False,
        icon_path: Path | str | None = None,
        *,
        theme_manager: ThemeManager | None = None,
        dock_manager: DockManager | None = None,
        theme_mode: ThemeMode | str = ThemeMode.SYSTEM,
        backdrop_type: BackdropType | str = BackdropType.ACRYLIC,
        dock_position: DockPosition | str = DockPosition.TOP,
        target_monitor: str | int = "auto",
        snap_on_drag_release: bool = False,
    ) -> None:
        """Инициализация окна SpotlightBar с поддержкой всех параметров Concept B."""
        super().__init__(parent)

        self._pipeline_state: str = "IDLE"
        self._is_muted: bool = False
        self._hide_on_focus_loss: bool = bool(hide_on_focus_loss)
        self._drag_offset: QPoint | None = None
        self._manually_dragged: bool = False
        self._snap_on_drag_release: bool = bool(snap_on_drag_release)
        self._display_mode: DisplayMode = DisplayMode.FULL_DECK
        self._target_monitor: str = str(target_monitor).strip().lower() or "auto"
        self._backdrop_type: BackdropType = self._coerce_backdrop(backdrop_type)
        self.icon_path: Path = Path(icon_path) if icon_path is not None else get_icon_path()

        self._theme_manager: ThemeManager = (
            theme_manager if theme_manager is not None else ThemeManager(self, mode=theme_mode)
        )
        self._dock_manager: DockManager = (
            dock_manager
            if dock_manager is not None
            else DockManager(self, initial_position=coerce_dock_position(dock_position))
        )
        self._dock_manager.set_dock_position(dock_position)

        self._init_window_flags()
        self._init_window_icon()
        self._init_ui()
        self._connect_managers()
        self._apply_styles()
        self.center_on_screen()

    # --- Публичные свойства ---

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
        """Возвращает совместимый виджет состояния волны (0 FPS в скрытом режиме)."""
        return self._waveform

    @property
    def chat_drawer(self) -> ChatDrawer:
        """Возвращает выдвижную панель истории диалога и шагов ReAct."""
        return self._chat_drawer

    @property
    def theme_manager(self) -> ThemeManager:
        """Возвращает активный менеджер тем Fluent 2."""
        return self._theme_manager

    @property
    def theme_tokens(self) -> ThemeTokens:
        """Возвращает текущий набор дизайн-токенов (Dark / Light)."""
        return self._theme_manager.tokens

    @property
    def dock_manager(self) -> DockManager:
        """Возвращает мультимониторный менеджер стыковки."""
        return self._dock_manager

    @property
    def dock_position(self) -> DockPosition:
        """Возвращает текущую позицию стыковки из 8 допустимых зон."""
        return self._dock_manager.dock_position

    @property
    def display_mode(self) -> DisplayMode:
        """Возвращает текущий уровень плотности (FULL_DECK / MINI_AGENT_BAR / COLLAPSED_PILL)."""
        return self._display_mode

    @property
    def sponge_pore(self) -> SpongePoreWidget:
        """Возвращает тактильный микропористый индикатор «Губка»."""
        return self._sponge_pore

    @property
    def context_chip_bar(self) -> ContextChipBar:
        """Возвращает полосу кликабельных контекстных чипов."""
        return self._context_chip_bar

    @property
    def agent_mini_bar(self) -> AgentMiniBar:
        """Возвращает компактную полосу Live Agent Mini-Bar."""
        return self._agent_mini_bar

    @property
    def backdrop_type(self) -> BackdropType:
        """Возвращает текущий тип DWM-размытия (acrylic / mica / none / tabbed)."""
        return self._backdrop_type

    # --- Инициализация окна и макета ---

    @staticmethod
    def _coerce_backdrop(backdrop: BackdropType | str) -> BackdropType:
        """Нормализует тип системного размытия с откатом к ACRYLIC."""
        if isinstance(backdrop, BackdropType):
            return backdrop
        try:
            return BackdropType(backdrop.strip().lower())
        except ValueError:
            return BackdropType.ACRYLIC

    def _init_window_flags(self) -> None:
        """Настройка безрамочного окна поверх всех окон с прозрачным фоном."""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(self.DEFAULT_WIDTH)

    def _init_window_icon(self) -> None:
        """Устанавливает системную иконку окна при наличии файла .ico."""
        if self.icon_path.exists():
            self.setWindowIcon(QIcon(str(self.icon_path)))
        else:
            self.setWindowIcon(QIcon())

    def _init_ui(self) -> None:
        """Построение иерархии виджетов Concept B с сохранением совместимости."""
        tokens = self._theme_manager.tokens

        self._outer_layout = QVBoxLayout(self)
        self._outer_layout.setSpacing(0)
        self._update_outer_margins()

        self._container = QFrame(self)
        self._container.setObjectName("spotlightBar")
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(0, 0, 0, 0)
        self._container_layout.setSpacing(0)

        # Совместимый скрытый WaveformWidget без траты CPU (0 FPS таймер)
        self._waveform = WaveformWidget(self)
        self._waveform.stop_animation()
        self._waveform.hide()

        self._deck_widget = self._build_full_deck(tokens)
        self._container_layout.addWidget(self._deck_widget)

        self._agent_mini_bar = AgentMiniBar(self._container, tokens=tokens)
        self._agent_mini_bar.setFixedHeight(48)
        self._agent_mini_bar.abort_requested.connect(self.abort_requested.emit)
        self._agent_mini_bar.hide()
        self._container_layout.addWidget(self._agent_mini_bar)

        self._collapsed_pill = CollapsedEdgePill(self._container, tokens=tokens)
        self._collapsed_pill.clicked.connect(lambda: self.set_display_mode(DisplayMode.FULL_DECK))
        self._collapsed_pill.hide()
        self._container_layout.addWidget(self._collapsed_pill)

        self._outer_layout.addWidget(self._container)
        self._rebuild_deck_order()
        self.adjustSize()

    def _build_full_deck(self, tokens: ThemeTokens) -> QWidget:
        """Собирает основной Command Deck: полоса захвата, строка ввода, чипы и чат."""
        deck = QWidget(self._container)
        deck.setStyleSheet("background: transparent;")
        self._deck_layout = QVBoxLayout(deck)
        self._deck_layout.setContentsMargins(0, 0, 0, 0)
        self._deck_layout.setSpacing(0)

        self._drag_strip = MicroPoreDragStrip(deck, tokens=tokens)
        self._input_row = self._build_input_row(deck, tokens)
        self._chip_bar_wrap = self._build_context_chip_row(deck, tokens)

        self._drawer_divider = QFrame(deck)
        self._drawer_divider.setObjectName("drawerDivider")
        self._drawer_divider.setVisible(False)

        self._chat_drawer = ChatDrawer(parent=deck, tokens=tokens)
        self._chat_drawer.expanded_changed.connect(self._on_drawer_expanded_changed)
        self._chat_drawer._animation.valueChanged.connect(self._on_drawer_animation_step)
        self._chat_drawer._animation.finished.connect(self._on_drawer_animation_step)
        return deck

    def _build_input_row(self, parent: QWidget, tokens: ThemeTokens) -> QWidget:
        """Строит строку ввода с тактильным SpongePoreWidget, бейджем и кнопками."""
        input_row = QWidget(parent)
        input_row.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(input_row)
        layout.setContentsMargins(14, 8, 12, 8)
        layout.setSpacing(10)

        self._sponge_pore = SpongePoreWidget(input_row, tokens=tokens, size=32)
        self._sponge_pore.setFixedSize(32, 32)
        self._search_icon: QWidget = self._sponge_pore
        layout.addWidget(self._sponge_pore)

        self._search_input = QLineEdit(input_row)
        self._search_input.setObjectName("searchInput")
        self._search_input.setPlaceholderText("Спросите Губку или введите команду...")
        self._search_input.returnPressed.connect(self._on_enter_pressed)
        layout.addWidget(self._search_input, stretch=1)

        self._status_badge = QLabel(get_status_label_ru("IDLE"), input_row)
        self._status_badge.setObjectName("statusBadge")
        self._status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status_badge, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._mic_btn = VectorIconButton("mic", input_row, tokens=tokens)
        self._mic_btn.setObjectName("actionIconBtn")
        self._mic_btn.setToolTip("Отключить / включить микрофон")
        self._mic_btn.clicked.connect(self._on_mic_btn_clicked)
        layout.addWidget(self._mic_btn)

        self._toggle_drawer_btn = VectorIconButton("drawer", input_row, tokens=tokens)
        self._toggle_drawer_btn.setObjectName("toggleDrawerBtn")
        self._toggle_drawer_btn.setToolTip("Показать или скрыть историю диалога")
        self._toggle_drawer_btn.clicked.connect(self._on_toggle_drawer_clicked)
        layout.addWidget(self._toggle_drawer_btn)

        self._settings_btn = VectorIconButton("settings", input_row, tokens=tokens)
        self._settings_btn.setObjectName("actionIconBtn")
        self._settings_btn.setToolTip("Открыть настройки")
        self._settings_btn.clicked.connect(self._on_settings_btn_clicked)
        layout.addWidget(self._settings_btn)
        return input_row

    def _build_context_chip_row(self, parent: QWidget, tokens: ThemeTokens) -> QWidget:
        """Создаёт обёртку для полосы контекстных чипов Command Deck."""
        wrap = QWidget(parent)
        wrap.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(14, 0, 14, 9)
        layout.setSpacing(0)

        self._context_chip_bar = ContextChipBar(wrap, tokens=tokens)
        self._context_chip_bar.set_context(
            monitor_label="Мон. 1 · 100%",
            active_app="Рабочий стол",
            memory_count=0,
            voice_mode="Голос",
            voice_active=True,
        )
        self._context_chip_bar.chip_clicked.connect(self._on_context_chip_clicked)
        layout.addWidget(self._context_chip_bar)
        return wrap

    def _connect_managers(self) -> None:
        """Подключает сигналы ThemeManager и DockManager к обновлению окна."""
        self._theme_manager.theme_changed.connect(self._on_theme_changed)
        self._dock_manager.screens_changed.connect(self._on_screens_changed)

    def _rebuild_deck_order(self) -> None:
        """Выстраивает порядок элементов Command Deck (авто-инверсия при стыковке снизу)."""
        inverted = should_invert_input_order(self.dock_position)
        self._drag_strip.set_edge_at_bottom(inverted)

        widgets: tuple[QWidget, ...] = (
            (
                self._chat_drawer,
                self._drawer_divider,
                self._chip_bar_wrap,
                self._input_row,
                self._drag_strip,
            )
            if inverted
            else (
                self._drag_strip,
                self._input_row,
                self._chip_bar_wrap,
                self._drawer_divider,
                self._chat_drawer,
            )
        )
        for widget in widgets:
            self._deck_layout.removeWidget(widget)
        for widget in widgets:
            self._deck_layout.addWidget(widget)

    def _update_outer_margins(self) -> None:
        """Убирает внешний отступ у пристыкованных граней для плоского стыка 0px."""
        edges = compute_flat_edges(self.dock_position)
        left = 0 if "left" in edges else _SHADOW_MARGIN
        top = 0 if "top" in edges else _SHADOW_MARGIN
        right = 0 if "right" in edges else _SHADOW_MARGIN
        bottom = 0 if "bottom" in edges else _SHADOW_BOTTOM_MARGIN
        self._outer_layout.setContentsMargins(left, top, right, bottom)

    # --- Темы, DWM и стили ---

    def _apply_styles(self) -> None:
        """Применяет актуальные дизайн-токены Fluent 2 (Dark/Light) ко всем виджетам."""
        tokens = self._theme_manager.tokens
        qss = (
            build_blade_stylesheet(tokens, self.dock_position, corner_radius=18)
            + "\n"
            + _spotlight_controls_qss(tokens)
        )
        self.setStyleSheet(qss)

        self._sponge_pore.set_tokens(tokens)
        self._drag_strip.set_tokens(tokens)
        self._collapsed_pill.set_tokens(tokens)
        self._context_chip_bar.set_tokens(tokens)
        self._agent_mini_bar.set_tokens(tokens)
        self._chat_drawer.apply_theme(tokens)
        self._mic_btn.set_tokens(tokens)
        self._toggle_drawer_btn.set_tokens(tokens)
        self._settings_btn.set_tokens(tokens)
        self._refresh_status_badge()
        self._apply_backdrop()
        self.update()

    def _apply_backdrop(self) -> None:
        """Применяет нативный Windows 11 DWM Acrylic/Mica (безопасный no-op в offscreen)."""
        if not self.isVisible():
            return
        try:
            hwnd = int(self.winId())
        except (RuntimeError, ValueError):
            return
        apply_win11_backdrop(
            hwnd,
            dark=self._theme_manager.is_dark,
            backdrop=self._backdrop_type,
            disable_dwm_rounding=True,
        )

    def _on_theme_changed(self, _tokens: ThemeTokens) -> None:
        """Реагирует на авто-смену темы Windows 11 или ручное переключение."""
        self._apply_styles()

    def _on_screens_changed(self) -> None:
        """Пересчитывает геометрию при изменении конфигурации мониторов или DPI."""
        if not self._manually_dragged:
            self.center_on_screen()

    def _refresh_status_badge(self) -> None:
        """Синхронизирует текст и стиль бейджа состояния, а также состояние SpongePore."""
        effective_state = (
            "MUTED" if (self._is_muted and self._pipeline_state == "IDLE") else self._pipeline_state
        )
        self._status_badge.setText(get_status_label_ru(effective_state))
        self._status_badge.setStyleSheet(
            _status_badge_qss_for_tokens(effective_state, self._theme_manager.tokens)
        )
        self._sponge_pore.set_state(effective_state)

    def _sync_drawer_chrome(self) -> None:
        """Синхронизирует видимость разделителя и направление шеврона раскрытия."""
        expanded = self.chat_drawer.is_expanded()
        self._drawer_divider.setVisible(expanded)
        self._toggle_drawer_btn.set_expanded_state(expanded)

    def _on_drawer_expanded_changed(self, _expanded: bool) -> None:
        """Обновляет оформление панели при смене состояния раскрытия ChatDrawer."""
        self._sync_drawer_chrome()
        self._on_drawer_animation_step()

    def _on_drawer_animation_step(self, _value: object = None) -> None:
        """Подгоняет высоту окна во время анимации раскрытия/сворачивания ChatDrawer."""
        if self._display_mode != DisplayMode.FULL_DECK:
            return
        self.chat_drawer.updateGeometry()
        target_height = max(92, self.sizeHint().height())
        if not self._manually_dragged and should_invert_input_order(self.dock_position):
            self._apply_dock_geometry(height_override=target_height)
            return
        self.resize(self.width(), target_height)

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

    def _on_context_chip_clicked(self, chip_id: str) -> None:
        """Обрабатывает клики по контекстным чипам Command Deck."""
        if chip_id == "voice":
            self._on_mic_btn_clicked()
        elif chip_id == "monitor":
            self.center_on_screen()

    # --- Вычисление и применение геометрии докинга ---

    def _resolved_monitor_index(self) -> int | None:
        """Преобразует настройку target_monitor ('auto', '1', '2', '3') в индекс экрана."""
        if self._target_monitor == "auto":
            return None
        try:
            one_based = int(self._target_monitor)
        except ValueError:
            return None
        return max(0, one_based - 1)

    def _desired_dimensions(self, height_override: int | None = None) -> tuple[int, int]:
        """Возвращает целевые (width, height) с учётом режима плотности и позиции."""
        pos = self.dock_position
        margins = self._outer_layout.contentsMargins()
        extra_w = margins.left() + margins.right()
        extra_h = margins.top() + margins.bottom()

        if self._display_mode == DisplayMode.COLLAPSED_PILL:
            if pos in (DockPosition.LEFT, DockPosition.RIGHT):
                return (18 + extra_w, 96 + extra_h)
            if pos in (
                DockPosition.TOP_LEFT,
                DockPosition.TOP_RIGHT,
                DockPosition.BOTTOM_LEFT,
                DockPosition.BOTTOM_RIGHT,
            ):
                return (56 + extra_w, 24 + extra_h)
            return (112 + extra_w, 18 + extra_h)

        if self._display_mode == DisplayMode.MINI_AGENT_BAR:
            w = 380 if pos in (DockPosition.LEFT, DockPosition.RIGHT) else 500
            mini_h = max(48, self._agent_mini_bar.minimumHeight())
            container_margins = self._container_layout.contentsMargins()
            inner_h = container_margins.top() + container_margins.bottom()
            return (w, mini_h + inner_h + extra_h)

        # FULL_DECK: сохраняем DEFAULT_WIDTH=732 для TOP/BOTTOM для полной совместимости
        if pos in (DockPosition.LEFT, DockPosition.RIGHT):
            w = 406
        elif pos in (
            DockPosition.TOP_LEFT,
            DockPosition.TOP_RIGHT,
            DockPosition.BOTTOM_LEFT,
            DockPosition.BOTTOM_RIGHT,
        ):
            w = 456
        else:
            w = self.DEFAULT_WIDTH

        h = height_override if height_override is not None else max(98, self.sizeHint().height())
        return (w, h)

    def _apply_dock_geometry(
        self,
        *,
        cursor_pos: QPoint | None = None,
        height_override: int | None = None,
    ) -> DockGeometry:
        """Вычисляет и применяет геометрию окна через DockManager без клиппинга."""
        target_w, target_h = self._desired_dimensions(height_override=height_override)
        geom = self._dock_manager.compute_geometry(
            position=self.dock_position,
            mode=self._display_mode,
            monitor_index=self._resolved_monitor_index(),
            cursor_pos=cursor_pos,
            content_width=target_w,
            content_height=target_h,
        )
        self.setFixedWidth(geom.rect.width())
        self.resize(geom.rect.width(), geom.rect.height())
        self.move(geom.rect.topLeft())

        dpi_pct = int(round(geom.dpi_scale * 100))
        self._context_chip_bar.update_chip(
            "monitor",
            f"Мон. {geom.monitor_index + 1} · {dpi_pct}%",
        )
        return geom

    # --- Публичный интерфейс ---

    def set_theme_mode(self, mode: ThemeMode | str) -> ThemeTokens:
        """Переключает режим темы оформления ('system' / 'dark' / 'light')."""
        tokens = self._theme_manager.set_mode(mode)
        self._apply_styles()
        return tokens

    def set_backdrop_type(self, backdrop: BackdropType | str) -> None:
        """Изменяет тип системного размытия Windows 11 DWM ('acrylic' / 'mica')."""
        self._backdrop_type = self._coerce_backdrop(backdrop)
        self._apply_backdrop()

    def set_target_monitor(self, target_monitor: str | int) -> DockGeometry:
        """Задаёт целевой монитор ('auto', '1', '2', '3') и перепозиционирует панель."""
        self._target_monitor = str(target_monitor).strip().lower() or "auto"
        self._manually_dragged = False
        return self.center_on_screen()

    def set_snap_on_drag_release(self, enabled: bool) -> None:
        """Включает или отключает автоматическую стыковку к краю после перетаскивания."""
        self._snap_on_drag_release = bool(enabled)

    def set_dock_position(
        self,
        position: DockPosition | str,
        *,
        cursor_pos: QPoint | None = None,
    ) -> DockGeometry:
        """Переводит панель в одну из 8 позиций стыковки и обновляет углы/инверсию."""
        coerced = coerce_dock_position(position)
        changed = coerced != self.dock_position
        self._dock_manager.set_dock_position(coerced)
        self._manually_dragged = False
        self._update_outer_margins()
        self._rebuild_deck_order()
        self._apply_styles()
        geom = self._apply_dock_geometry(cursor_pos=cursor_pos)
        if changed:
            self.dock_position_changed.emit(coerced.value)
        return geom

    def set_display_mode(self, mode: DisplayMode | str) -> DockGeometry:
        """Переключает уровень плотности окна (FULL_DECK / MINI_AGENT_BAR / COLLAPSED_PILL)."""
        coerced = coerce_display_mode(mode)
        self._display_mode = coerced

        self._deck_widget.setVisible(coerced == DisplayMode.FULL_DECK)
        self._agent_mini_bar.setVisible(coerced == DisplayMode.MINI_AGENT_BAR)
        self._collapsed_pill.setVisible(coerced == DisplayMode.COLLAPSED_PILL)
        if coerced == DisplayMode.MINI_AGENT_BAR:
            self._container_layout.setContentsMargins(6, 6, 6, 6)
        else:
            self._container_layout.setContentsMargins(0, 0, 0, 0)
        self._sponge_pore.set_collapsed(coerced != DisplayMode.FULL_DECK)
        if coerced != DisplayMode.MINI_AGENT_BAR:
            self._agent_mini_bar.set_state(PoreState.IDLE)

        self.setMinimumHeight(0)
        self._container_layout.activate()
        self._outer_layout.activate()
        return self._apply_dock_geometry()

    def set_agent_progress(
        self,
        active: bool,
        step_index: int = 0,
        max_steps: int = 0,
        title: str = "",
        engine: str = "UIA",
    ) -> None:
        """Переключает панель между FULL_DECK и Live Agent Mini-Bar при работе ReAct."""
        if active:
            self._agent_mini_bar.update_step(
                step_index=step_index,
                total_steps=max_steps,
                title=title,
                engine=engine,
                state=PoreState.PROCESSING,
            )
            self.set_display_mode(DisplayMode.MINI_AGENT_BAR)
            return

        self._agent_mini_bar.set_state(PoreState.IDLE)
        self.set_display_mode(DisplayMode.FULL_DECK)

    def update_context_chips(
        self,
        *,
        monitor_label: str | None = None,
        active_app: str | None = None,
        memory_count: int | None = None,
        voice_mode: str | None = None,
        voice_active: bool | None = None,
    ) -> None:
        """Точечно обновляет значения контекстных чипов Command Deck."""
        if monitor_label is not None:
            self._context_chip_bar.update_chip("monitor", monitor_label)
        if active_app is not None:
            self._context_chip_bar.update_chip("app", active_app)
        if memory_count is not None:
            self._context_chip_bar.update_chip("memory", f"Память: {max(0, int(memory_count))}")
        if voice_mode is not None or voice_active is not None:
            current_btn = self._context_chip_bar.get_chip_button("voice")
            label = (
                voice_mode
                if voice_mode is not None
                else (current_btn.text() if current_btn is not None else "Голос")
            )
            self._context_chip_bar.update_chip("voice", label, active=voice_active)

    def snap_to_point(self, global_pos: QPoint) -> DockPosition:
        """Определяет ближайшую из 8 зон стыковки для точки экрана и пристыковывает окно."""
        zone = self._dock_manager.detect_snap_zone(global_pos)
        self.set_dock_position(zone, cursor_pos=global_pos)
        return zone

    def set_pipeline_state(self, state: str | PipelineState) -> None:
        """Обновляет состояние конвейера в SpongePore, совместимой волне и бейдже."""
        state_str = state.value if hasattr(state, "value") else str(state)
        norm = state_str.strip().upper()
        self._pipeline_state = norm
        self._waveform.set_state(norm)
        self._refresh_status_badge()

    def set_audio_level(self, level: float) -> None:
        """Передаёт амплитуду микрофона в SpongePoreWidget и совместимый WaveformWidget."""
        self._waveform.set_audio_level(level)
        self._sponge_pore.set_audio_level(level)

    def set_muted(self, muted: bool) -> None:
        """Обновляет состояние микрофона в кнопке, бейдже и чипе голоса без emit."""
        self._is_muted = bool(muted)
        self._mic_btn.set_muted_state(self._is_muted)
        self._context_chip_bar.update_chip(
            "voice",
            "Без звука" if self._is_muted else "Голос",
            active=not self._is_muted,
        )
        self._refresh_status_badge()

    def add_chat_message(self, role: str, text: str, status: str = "done") -> MessageCard:
        """Добавляет карточку сообщения в ChatDrawer и автоматически раскрывает его."""
        card = self.chat_drawer.add_message(role=role, text=text, status=status)
        if not self.chat_drawer.is_expanded():
            self.chat_drawer.toggle(expand=True)
        self._sync_drawer_chrome()
        return card

    def add_step_card(
        self,
        step_index: int,
        title: str,
        engine: str,
        status: str = "running",
        duration_ms: int = 0,
        detail: str = "",
    ) -> StepCard:
        """Добавляет карточку шага ReAct в ChatDrawer и раскрывает его при необходимости."""
        step_card = self.chat_drawer.add_step_card(
            step_index=step_index,
            title=title,
            engine=engine,
            status=status,
            duration_ms=duration_ms,
            detail=detail,
        )
        if not self.chat_drawer.is_expanded():
            self.chat_drawer.toggle(expand=True)
        self._sync_drawer_chrome()
        return step_card

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

    def center_on_screen(self, cursor_pos: QPoint | None = None) -> DockGeometry:
        """Позиционирует панель на активном мониторе под курсором через DockManager."""
        self._manually_dragged = False
        return self._apply_dock_geometry(cursor_pos=cursor_pos)

    def show_bar(self) -> None:
        """Отображает панель, применяет DWM-размытие и переводит фокус в строку ввода."""
        self.show()
        self._apply_backdrop()
        self.raise_()
        self.activateWindow()
        if self._display_mode == DisplayMode.FULL_DECK:
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

    # --- События окна, отрисовка тени и перетаскивание ---

    @override
    def showEvent(self, event: QShowEvent) -> None:
        """Применяет DWM-размытие и актуальную тему ОС при каждом появлении окна."""
        super().showEvent(event)
        self._apply_backdrop()

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        """Рисует мягкую многослойную тень и 1px specular-ободок с учётом плоских кромок."""
        rect = self._container.geometry()
        if not rect.isValid():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        radii = compute_corner_radii(self.dock_position, 18)
        is_dark = self._theme_manager.is_dark
        layers = (
            ((8, 3, 14), (5, 2, 22), (2, 1, 34)) if is_dark else ((8, 3, 7), (5, 2, 11), (2, 1, 17))
        )
        shadow_rgb = (0, 0, 0) if is_dark else (24, 28, 36)

        painter.setPen(Qt.PenStyle.NoPen)
        for spread, y_shift, alpha in layers:
            shadow_rect = QRectF(
                rect.adjusted(-spread, -spread + y_shift, spread, spread + y_shift)
            )
            expanded_radii = tuple(r + int(spread * 0.5) if r > 0 else 0 for r in radii)
            path = _build_asymmetric_rounded_path(
                shadow_rect,
                (expanded_radii[0], expanded_radii[1], expanded_radii[2], expanded_radii[3]),
            )
            painter.setBrush(QColor(shadow_rgb[0], shadow_rgb[1], shadow_rgb[2], alpha))
            painter.drawPath(path)

        # Тонкий 1px Fluent 2 specular-ободок по периметру контейнера
        border_path = _build_asymmetric_rounded_path(
            QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), radii
        )
        specular = QColor(255, 255, 255, 26) if is_dark else QColor(255, 255, 255, 180)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(specular, 1.0))
        painter.drawPath(border_path)
        painter.end()

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Начинает перетаскивание плавающей панели за свободную область или Drag Strip."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    @override
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Перемещает панель вслед за курсором при перетаскивании."""
        if self._drag_offset is not None and bool(event.buttons() & Qt.MouseButton.LeftButton):
            self._manually_dragged = True
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Завершает перетаскивание и при включённом режиме пристыковывает к зоне экрана."""
        if event.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            if self._snap_on_drag_release:
                self.snap_to_point(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        """В режиме агента Esc прерывает работу (abort_requested), иначе скрывает панель."""
        if event.key() == Qt.Key.Key_Escape:
            if self._display_mode == DisplayMode.MINI_AGENT_BAR:
                self.abort_requested.emit()
            else:
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
