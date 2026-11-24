"""
> [!NOTE]
> Модуль gui.dock_manager: Менеджер стыковки Fluent 2 Docked Blade к 8 зонам экрана.
> Поддерживает 1/2/3+ мониторов, смешанный DPI (100–200%), отрицательные координаты
> экранов, определение зон прилипания при перетаскивании и расчёт геометрии без обрезки.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QObject, QPoint, QRect, Signal
from PySide6.QtGui import QCursor, QScreen
from PySide6.QtWidgets import QApplication


class DockPosition(StrEnum):
    """Восемь допустимых позиций стыковки панели к границам рабочего стола."""

    TOP = "top"
    BOTTOM = "bottom"
    LEFT = "left"
    RIGHT = "right"
    TOP_LEFT = "top_left"
    TOP_RIGHT = "top_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"


class DisplayMode(StrEnum):
    """Три уровня плотности интерфейса Concept B."""

    COLLAPSED_PILL = "collapsed_pill"
    MINI_AGENT_BAR = "mini_agent_bar"
    FULL_DECK = "full_deck"


@dataclass(frozen=True)
class ScreenDescriptor:
    """Независимое описание геометрии и масштаба монитора."""

    index: int
    available_geometry: QRect
    dpi_scale: float = 1.0
    name: str = ""


@dataclass(frozen=True)
class DockGeometry:
    """Вычисленная геометрия пристыкованного окна без выхода за пределы монитора."""

    rect: QRect
    corner_radii: tuple[int, int, int, int]
    flat_edges: tuple[str, ...]
    monitor_index: int
    dpi_scale: float
    is_vertical: bool
    invert_input_order: bool
    dock_position: DockPosition = DockPosition.TOP
    display_mode: DisplayMode = DisplayMode.FULL_DECK


_FLAT_EDGES_MAP: dict[DockPosition, tuple[str, ...]] = {
    DockPosition.TOP: ("top",),
    DockPosition.BOTTOM: ("bottom",),
    DockPosition.LEFT: ("left",),
    DockPosition.RIGHT: ("right",),
    DockPosition.TOP_LEFT: ("top", "left"),
    DockPosition.TOP_RIGHT: ("top", "right"),
    DockPosition.BOTTOM_LEFT: ("bottom", "left"),
    DockPosition.BOTTOM_RIGHT: ("bottom", "right"),
}

_CORNER_POSITIONS: frozenset[DockPosition] = frozenset(
    {
        DockPosition.TOP_LEFT,
        DockPosition.TOP_RIGHT,
        DockPosition.BOTTOM_LEFT,
        DockPosition.BOTTOM_RIGHT,
    }
)

_VERTICAL_POSITIONS: frozenset[DockPosition] = frozenset(
    {
        DockPosition.LEFT,
        DockPosition.RIGHT,
    }
)

_INVERTED_INPUT_POSITIONS: frozenset[DockPosition] = frozenset(
    {
        DockPosition.BOTTOM,
        DockPosition.BOTTOM_LEFT,
        DockPosition.BOTTOM_RIGHT,
    }
)


def coerce_dock_position(position: DockPosition | str) -> DockPosition:
    """Нормализует строковое или enum-значение позиции стыковки."""
    if isinstance(position, DockPosition):
        return position
    return DockPosition(position.strip().lower())


def coerce_display_mode(mode: DisplayMode | str) -> DisplayMode:
    """Нормализует строковое или enum-значение режима отображения."""
    if isinstance(mode, DisplayMode):
        return mode
    return DisplayMode(mode.strip().lower())


def compute_corner_radii(
    position: DockPosition | str | None,
    radius: int = 18,
) -> tuple[int, int, int, int]:
    """
    Возвращает радиусы скругления углов (top_left, top_right, bottom_right, bottom_left).

    Внешние углы, прилегающие к краю монитора, всегда равны 0px;
    внутренние углы получают плавное скругление radius (по умолчанию 18px).
    """
    r = max(0, int(radius))
    if position is None:
        return (r, r, r, r)

    pos = coerce_dock_position(position)
    radii_map: dict[DockPosition, tuple[int, int, int, int]] = {
        DockPosition.TOP: (0, 0, r, r),
        DockPosition.BOTTOM: (r, r, 0, 0),
        DockPosition.LEFT: (0, r, r, 0),
        DockPosition.RIGHT: (r, 0, 0, r),
        DockPosition.TOP_LEFT: (0, 0, r, 0),
        DockPosition.TOP_RIGHT: (0, 0, 0, r),
        DockPosition.BOTTOM_LEFT: (0, r, 0, 0),
        DockPosition.BOTTOM_RIGHT: (r, 0, 0, 0),
    }
    return radii_map[pos]


def compute_flat_edges(position: DockPosition | str) -> tuple[str, ...]:
    """Возвращает кортеж плоских граней окна, примыкающих к кромке монитора."""
    return _FLAT_EDGES_MAP[coerce_dock_position(position)]


def is_vertical_position(position: DockPosition | str) -> bool:
    """Проверяет, является ли позиция вертикальной боковой панелью (LEFT / RIGHT)."""
    return coerce_dock_position(position) in _VERTICAL_POSITIONS


def should_invert_input_order(position: DockPosition | str) -> bool:
    """Проверяет, должна ли строка ввода крепиться снизу (BOTTOM / BL / BR)."""
    return coerce_dock_position(position) in _INVERTED_INPUT_POSITIONS


def _default_dimensions(
    position: DockPosition,
    mode: DisplayMode,
    avail: QRect,
    content_width: int | None,
    content_height: int | None,
) -> tuple[int, int]:
    """Подбирает целевые ширину и высоту окна с учётом позиции и режима плотности."""
    if mode == DisplayMode.COLLAPSED_PILL:
        if position in _VERTICAL_POSITIONS:
            return (18, 96)
        if position in _CORNER_POSITIONS:
            return (56, 24)
        return (112, 18)

    if mode == DisplayMode.MINI_AGENT_BAR:
        if position in _VERTICAL_POSITIONS:
            return (content_width or 340, content_height or 48)
        if position in _CORNER_POSITIONS:
            return (content_width or 420, content_height or 44)
        return (content_width or 460, content_height or 44)

    # Режим FULL_DECK
    if position in _VERTICAL_POSITIONS:
        default_h = min(540, max(280, avail.height() - 48))
        return (content_width or 390, content_height or default_h)
    if position in _CORNER_POSITIONS:
        default_h = min(380, max(200, avail.height() - 48))
        return (content_width or 440, content_height or default_h)
    return (content_width or 680, content_height or 76)


def _anchor_rect(position: DockPosition, avail: QRect, width: int, height: int) -> QRect:
    """Размещает прямоугольник (width x height) вплотную к заданным граням avail."""
    clamped_w = max(1, min(width, max(1, avail.width())))
    clamped_h = max(1, min(height, max(1, avail.height())))

    edges = _FLAT_EDGES_MAP[position]
    if "left" in edges:
        x = avail.left()
    elif "right" in edges:
        x = avail.left() + avail.width() - clamped_w
    else:
        x = avail.left() + (avail.width() - clamped_w) // 2

    if "top" in edges:
        y = avail.top()
    elif "bottom" in edges:
        y = avail.top() + avail.height() - clamped_h
    else:
        y = avail.top() + (avail.height() - clamped_h) // 2

    return QRect(x, y, clamped_w, clamped_h)


def _squared_distance_to_rect(point: QPoint, rect: QRect) -> int:
    """Вычисляет квадрат евклидова расстояния от точки до прямоугольника."""
    dx = 0
    if point.x() < rect.left():
        dx = rect.left() - point.x()
    elif point.x() > rect.right():
        dx = point.x() - rect.right()

    dy = 0
    if point.y() < rect.top():
        dy = rect.top() - point.y()
    elif point.y() > rect.bottom():
        dy = point.y() - rect.bottom()

    return dx * dx + dy * dy


class DockManager(QObject):
    """
    Мультимониторный менеджер стыковки для GupkaCommander 2.0.

    Отвечает за:
    - перечисление всех мониторов и их рабочих областей (availableGeometry) с DPI;
    - определение активного монитора по позиции курсора;
    - распознавание 8 зон прилипания (snap zones) при перетаскивании;
    - вычисление геометрии окна без клиппинга с плоскими внешними кромками.
    """

    geometry_updated = Signal(object)
    dock_position_changed = Signal(str)
    screens_changed = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        initial_position: DockPosition = DockPosition.TOP,
        screens_provider: Callable[[], list[ScreenDescriptor]] | None = None,
    ) -> None:
        """Инициализирует менеджер стыковки и подписывается на события экранов Qt."""
        super().__init__(parent)
        self._position: DockPosition = initial_position
        self._screens_provider = screens_provider
        self._virtual_screens: list[ScreenDescriptor] | None = None
        self._last_geometry: DockGeometry | None = None
        self._connected_screens: set[int] = set()
        self._connect_qt_screen_signals()

    @property
    def dock_position(self) -> DockPosition:
        """Возвращает текущую активную позицию стыковки."""
        return self._position

    @property
    def last_geometry(self) -> DockGeometry | None:
        """Возвращает последнюю вычисленную геометрию окна."""
        return self._last_geometry

    def set_dock_position(self, position: DockPosition | str) -> None:
        """Изменяет текущую позицию стыковки и уведомляет подписчиков."""
        new_pos = coerce_dock_position(position)
        if new_pos == self._position:
            return
        self._position = new_pos
        self.dock_position_changed.emit(new_pos.value)

    def set_virtual_screens(self, screens: list[ScreenDescriptor] | None) -> None:
        """
        Задаёт виртуальный набор мониторов (для тестов и симуляций смешанного DPI).

        Передача None возвращает чтение реальных мониторов из QApplication.screens().
        """
        self._virtual_screens = list(screens) if screens is not None else None
        self.screens_changed.emit()

    def get_screens(self) -> list[ScreenDescriptor]:
        """Возвращает актуальный список мониторов и их рабочих областей."""
        if self._virtual_screens is not None:
            return list(self._virtual_screens)
        if self._screens_provider is not None:
            return list(self._screens_provider())

        qt_screens = QApplication.screens()
        if not qt_screens:
            return [ScreenDescriptor(0, QRect(0, 0, 1920, 1040), 1.0, "Default")]

        descriptors: list[ScreenDescriptor] = []
        for idx, screen in enumerate(qt_screens):
            scale = max(1.0, float(screen.devicePixelRatio()))
            descriptors.append(
                ScreenDescriptor(
                    index=idx,
                    available_geometry=QRect(screen.availableGeometry()),
                    dpi_scale=scale,
                    name=screen.name(),
                )
            )
        return descriptors

    def resolve_active_monitor(
        self,
        cursor_pos: QPoint | None = None,
    ) -> ScreenDescriptor:
        """
        Определяет активный монитор по координатам курсора.

        Сначала проверяет точное попадание (включая QApplication.screenAt для
        реальных экранов), затем выбирает ближайший монитор без сбоев на стыках.
        """
        screens = self.get_screens()
        pos = cursor_pos if cursor_pos is not None else QCursor.pos()

        # Для реальных экранов проверяем нативный QApplication.screenAt
        if self._virtual_screens is None and self._screens_provider is None:
            native_screen = QApplication.screenAt(pos)
            if native_screen is not None:
                qt_screens = QApplication.screens()
                if native_screen in qt_screens:
                    idx = qt_screens.index(native_screen)
                    if idx < len(screens):
                        return screens[idx]

        for desc in screens:
            if desc.available_geometry.contains(pos):
                return desc

        return min(
            screens,
            key=lambda desc: _squared_distance_to_rect(pos, desc.available_geometry),
        )

    def detect_snap_zone(
        self,
        point: QPoint,
        screen: ScreenDescriptor | None = None,
        *,
        corner_ratio: float = 0.22,
    ) -> DockPosition:
        """
        Определяет ближайшую из 8 зон стыковки для точки перетаскивания.

        Углы имеют приоритет, если курсор попадает одновременно в угловую полосу
        по X и по Y; иначе выбирается ближайшая из четырёх сторон экрана.
        """
        target = screen if screen is not None else self.resolve_active_monitor(point)
        avail = target.available_geometry
        width = max(1, avail.width())
        height = max(1, avail.height())

        rel_x = max(0.0, min(1.0, (point.x() - avail.left()) / width))
        rel_y = max(0.0, min(1.0, (point.y() - avail.top()) / height))
        band = max(0.05, min(0.35, corner_ratio))

        near_left = rel_x <= band
        near_right = rel_x >= (1.0 - band)
        near_top = rel_y <= band
        near_bottom = rel_y >= (1.0 - band)

        corner = self._resolve_corner_zone(near_left, near_right, near_top, near_bottom)
        if corner is not None:
            return corner

        distances: dict[DockPosition, float] = {
            DockPosition.TOP: rel_y,
            DockPosition.BOTTOM: 1.0 - rel_y,
            DockPosition.LEFT: rel_x,
            DockPosition.RIGHT: 1.0 - rel_x,
        }
        return min(distances, key=lambda key: distances[key])

    def compute_geometry(
        self,
        position: DockPosition | str | None = None,
        mode: DisplayMode | str = DisplayMode.FULL_DECK,
        *,
        monitor_index: int | None = None,
        cursor_pos: QPoint | None = None,
        content_width: int | None = None,
        content_height: int | None = None,
        corner_radius: int = 18,
    ) -> DockGeometry:
        """Вычисляет геометрию окна с плоскими кромками и гарантией без клиппинга."""
        pos = self._position if position is None else coerce_dock_position(position)
        display_mode = coerce_display_mode(mode)
        screen = self._resolve_target_screen(monitor_index, cursor_pos)
        avail = screen.available_geometry

        target_w, target_h = _default_dimensions(
            pos, display_mode, avail, content_width, content_height
        )
        geometry = DockGeometry(
            rect=_anchor_rect(pos, avail, target_w, target_h),
            corner_radii=compute_corner_radii(pos, corner_radius),
            flat_edges=compute_flat_edges(pos),
            monitor_index=screen.index,
            dpi_scale=screen.dpi_scale,
            is_vertical=is_vertical_position(pos),
            invert_input_order=should_invert_input_order(pos),
            dock_position=pos,
            display_mode=display_mode,
        )
        self._last_geometry = geometry
        self.geometry_updated.emit(geometry)
        return geometry

    def _resolve_target_screen(
        self,
        monitor_index: int | None,
        cursor_pos: QPoint | None,
    ) -> ScreenDescriptor:
        """Выбирает экран по явному индексу или по позиции курсора."""
        screens = self.get_screens()
        if monitor_index is not None:
            for desc in screens:
                if desc.index == monitor_index:
                    return desc
            clamped_idx = max(0, min(monitor_index, len(screens) - 1))
            return screens[clamped_idx]
        return self.resolve_active_monitor(cursor_pos)

    @staticmethod
    def _resolve_corner_zone(
        near_left: bool,
        near_right: bool,
        near_top: bool,
        near_bottom: bool,
    ) -> DockPosition | None:
        """Возвращает угловую зону стыковки при одновременном приближении к двум краям."""
        if near_top and near_left:
            return DockPosition.TOP_LEFT
        if near_top and near_right:
            return DockPosition.TOP_RIGHT
        if near_bottom and near_left:
            return DockPosition.BOTTOM_LEFT
        if near_bottom and near_right:
            return DockPosition.BOTTOM_RIGHT
        return None

    def _connect_qt_screen_signals(self) -> None:
        """Подключает сигналы изменения конфигурации мониторов у QApplication."""
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        app.screenAdded.connect(self._on_screen_added)
        app.screenRemoved.connect(self._on_screen_removed)
        for screen in app.screens():
            self._watch_screen(screen)

    def _watch_screen(self, screen: QScreen) -> None:
        """Подписывается на изменение рабочей области отдельного монитора."""
        screen_id = id(screen)
        if screen_id in self._connected_screens:
            return
        self._connected_screens.add(screen_id)
        screen.availableGeometryChanged.connect(self._on_screen_geometry_changed)
        screen.geometryChanged.connect(self._on_screen_geometry_changed)

    def _on_screen_added(self, screen: QScreen) -> None:
        """Обрабатывает подключение нового монитора."""
        self._watch_screen(screen)
        self.screens_changed.emit()

    def _on_screen_removed(self, screen: QScreen) -> None:
        """Обрабатывает отключение монитора."""
        self._connected_screens.discard(id(screen))
        self.screens_changed.emit()

    def _on_screen_geometry_changed(self, _rect: QRect) -> None:
        """Обрабатывает смену разрешения, DPI или положения панели задач."""
        self.screens_changed.emit()
