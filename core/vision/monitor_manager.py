"""Модуль обнаружения мониторов, управления DPI и преобразования глобальных координат.

Предоставляет:
- MonitorInfo: метаданные физического монитора (индекс, смещение, размер, DPI, флаг primary).
- MonitorManager: перечисление 1/2/3+ мониторов, определение активного экрана и маппинг
  нормализованных координат [0, 1000] <-> глобальный виртуальный рабочий стол (включая
  отрицательные координаты left/top).
"""

from __future__ import annotations

import ctypes
import dataclasses
import logging
import os
from collections.abc import Callable
from typing import Any

import mss

logger = logging.getLogger(__name__)

_DEFAULT_DPI = 96.0
_MINIMIZED_COORD_THRESHOLD = -30000


@dataclasses.dataclass(frozen=True)
class MonitorInfo:
    """Описание геометрии и масштаба отдельного монитора в виртуальном пространстве."""

    index: int
    left: int
    top: int
    width: int
    height: int
    dpi_scale: float = 1.0
    is_primary: bool = False
    name: str = ""

    @property
    def right(self) -> int:
        """Правая граница монитора в глобальных координатах."""
        return self.left + self.width

    @property
    def bottom(self) -> int:
        """Нижняя граница монитора в глобальных координатах."""
        return self.top + self.height

    @property
    def rect(self) -> tuple[int, int, int, int]:
        """Геометрия монитора в формате (left, top, width, height)."""
        return (self.left, self.top, self.width, self.height)

    def contains(self, x: int, y: int) -> bool:
        """Проверяет попадание глобальной точки (x, y) внутрь прямоугольника монитора."""
        return self.left <= x < self.right and self.top <= y < self.bottom


def _query_win32_monitor_dpi(left: int, top: int, width: int, height: int) -> float:
    """Запрашивает коэффициент DPI-масштабирования монитора через Win32 API."""
    if os.name != "nt" or width <= 0 or height <= 0:
        return 1.0
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        shcore = ctypes.windll.shcore

        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HMONITOR

        shcore.GetDpiForMonitor.argtypes = [
            wintypes.HMONITOR,
            ctypes.c_int,
            ctypes.POINTER(wintypes.UINT),
            ctypes.POINTER(wintypes.UINT),
        ]
        shcore.GetDpiForMonitor.restype = ctypes.c_long

        center = wintypes.POINT(left + width // 2, top + height // 2)
        hmonitor = user32.MonitorFromPoint(center, 2)  # MONITOR_DEFAULTTONEAREST = 2
        if not hmonitor:
            return 1.0

        dpi_x = wintypes.UINT(96)
        dpi_y = wintypes.UINT(96)
        res = int(shcore.GetDpiForMonitor(hmonitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)))
        if res == 0 and dpi_x.value > 0:
            return round(float(dpi_x.value) / _DEFAULT_DPI, 3)
    except (AttributeError, OSError, ValueError):
        pass
    return 1.0


def _query_win32_cursor_pos() -> tuple[int, int] | None:
    """Возвращает текущие глобальные координаты курсора мыши через Win32 GetCursorPos."""
    if os.name != "nt":
        return None
    try:
        from ctypes import wintypes

        point = wintypes.POINT()
        user32 = ctypes.windll.user32
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL
        if user32.GetCursorPos(ctypes.byref(point)):
            return (int(point.x), int(point.y))
    except (AttributeError, OSError, ValueError):
        pass
    return None


def _query_win32_foreground_rect() -> tuple[int, int, int, int] | None:
    """Возвращает (left, top, right, bottom) активного foreground-окна через Win32."""
    if os.name != "nt":
        return None
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    except (AttributeError, OSError, ValueError):
        pass
    return None


def _distance_sq_to_monitor(x: int, y: int, monitor: MonitorInfo) -> int:
    """Квадрат евклидова расстояния от точки до ближайшей границы монитора."""
    clamped_x = max(monitor.left, min(monitor.right - 1, x))
    clamped_y = max(monitor.top, min(monitor.bottom - 1, y))
    dx = x - clamped_x
    dy = y - clamped_y
    return dx * dx + dy * dy


class MonitorManager:
    """Менеджер мультимониторной геометрии и координатных преобразований."""

    def __init__(
        self,
        mss_factory: Any | None = None,
        dpi_provider: Callable[[int, int, int, int], float] | None = None,
        cursor_pos_fn: Callable[[], tuple[int, int] | None] | None = None,
        foreground_rect_fn: Callable[[], tuple[int, int, int, int] | None] | None = None,
        monitors: list[MonitorInfo] | None = None,
    ) -> None:
        """Инициализация менеджера мониторов.

        Args:
            mss_factory: Фабрика MSS-контекста (для модульных тестов).
            dpi_provider: Функция получения DPI scale по (left, top, width, height).
            cursor_pos_fn: Функция получения глобальной позиции курсора (x, y).
            foreground_rect_fn: Функция получения rect активного окна (l, t, r, b).
            monitors: Предзаданный список мониторов (отключает опрос MSS при передаче).
        """
        self._mss_factory: Any = mss_factory or getattr(mss, "MSS", mss.mss)
        self._dpi_provider = dpi_provider or _query_win32_monitor_dpi
        self._cursor_pos_fn = cursor_pos_fn or _query_win32_cursor_pos
        self._foreground_rect_fn = foreground_rect_fn or _query_win32_foreground_rect
        self._static_monitors: list[MonitorInfo] | None = list(monitors) if monitors else None

    def get_dpi_scale_for_rect(self, left: int, top: int, width: int, height: int) -> float:
        """Возвращает коэффициент DPI-масштабирования для указанной прямоугольной области."""
        dpi_scale = float(self._dpi_provider(left, top, width, height))
        return dpi_scale if dpi_scale > 0 else 1.0

    def _build_monitor_info(self, index: int, raw: dict[str, Any]) -> MonitorInfo:
        """Создает MonitorInfo из словаря геометрии MSS с учетом DPI и признака primary."""
        left = int(raw.get("left", 0))
        top = int(raw.get("top", 0))
        width = max(1, int(raw.get("width", 1920)))
        height = max(1, int(raw.get("height", 1080)))
        dpi_scale = self.get_dpi_scale_for_rect(left, top, width, height)
        is_primary = left == 0 and top == 0
        name = str(raw.get("name", f"Monitor {index}"))
        return MonitorInfo(
            index=index,
            left=left,
            top=top,
            width=width,
            height=height,
            dpi_scale=dpi_scale,
            is_primary=is_primary,
            name=name,
        )

    def get_monitors(self) -> list[MonitorInfo]:
        """Возвращает список всех подключенных мониторов (индексы 1..N)."""
        if self._static_monitors is not None:
            return list(self._static_monitors)

        try:
            with self._mss_factory() as sct:
                raw_monitors = list(sct.monitors)
        except Exception as err:
            logger.warning("Ошибка перечисления мониторов через MSS: %s", err)
            return [
                MonitorInfo(
                    index=1,
                    left=0,
                    top=0,
                    width=1920,
                    height=1080,
                    dpi_scale=1.0,
                    is_primary=True,
                    name="Monitor 1",
                )
            ]

        entries = raw_monitors[1:] if len(raw_monitors) > 1 else raw_monitors
        if not entries:
            return [
                MonitorInfo(
                    index=1,
                    left=0,
                    top=0,
                    width=1920,
                    height=1080,
                    dpi_scale=1.0,
                    is_primary=True,
                    name="Monitor 1",
                )
            ]

        result = [
            self._build_monitor_info(idx, dict(raw)) for idx, raw in enumerate(entries, start=1)
        ]
        if not any(mon.is_primary for mon in result):
            first = result[0]
            result[0] = dataclasses.replace(first, is_primary=True)
        return result

    def get_primary_monitor(self) -> MonitorInfo:
        """Возвращает основной (primary) монитор."""
        monitors = self.get_monitors()
        for mon in monitors:
            if mon.is_primary:
                return mon
        return monitors[0]

    def get_monitor(self, index: int) -> MonitorInfo:
        """Возвращает монитор по 1-based индексу, либо primary при выходе за границы."""
        monitors = self.get_monitors()
        for mon in monitors:
            if mon.index == index:
                return mon
        return self.get_primary_monitor()

    def get_monitor_for_point(self, x: int, y: int) -> MonitorInfo:
        """Определяет монитор, содержащий глобальную точку (x, y), или ближайший к ней."""
        monitors = self.get_monitors()
        for mon in monitors:
            if mon.contains(x, y):
                return mon
        return min(monitors, key=lambda mon: _distance_sq_to_monitor(x, y, mon))

    def _foreground_center(self) -> tuple[int, int] | None:
        """Вычисляет центр активного foreground-окна, исключая свернутые и пустые окна."""
        rect = self._foreground_rect_fn()
        if rect is None:
            return None
        left, top, right, bottom = rect
        if right <= left or bottom <= top:
            return None
        if left <= _MINIMIZED_COORD_THRESHOLD and top <= _MINIMIZED_COORD_THRESHOLD:
            return None
        return ((left + right) // 2, (top + bottom) // 2)

    def get_active_monitor(self, prefer: str = "foreground") -> MonitorInfo:
        """Определяет активный монитор по foreground-окну или курсору мыши.

        Args:
            prefer: Приоритетный источник ('foreground' или 'cursor').
        """
        fg_center = self._foreground_center()
        cursor_pos = self._cursor_pos_fn()

        if prefer == "cursor":
            if cursor_pos is not None:
                return self.get_monitor_for_point(cursor_pos[0], cursor_pos[1])
            if fg_center is not None:
                return self.get_monitor_for_point(fg_center[0], fg_center[1])
            return self.get_primary_monitor()

        if fg_center is not None:
            return self.get_monitor_for_point(fg_center[0], fg_center[1])
        if cursor_pos is not None:
            return self.get_monitor_for_point(cursor_pos[0], cursor_pos[1])
        return self.get_primary_monitor()

    def norm_to_global(
        self,
        x_norm: int,
        y_norm: int,
        monitor: MonitorInfo | int | None = None,
        norm_max: int = 1000,
    ) -> tuple[int, int]:
        """Переводит нормализованные [0, norm_max] координаты монитора в глобальные (x, y)."""
        if norm_max <= 0:
            raise ValueError("Параметр norm_max должен быть положительным числом")

        target = self._resolve_target_monitor(monitor)
        if target.width <= 0 or target.height <= 0:
            raise ValueError("Размеры монитора должны быть положительными")

        clamped_x = max(0, min(norm_max, x_norm))
        clamped_y = max(0, min(norm_max, y_norm))
        local_x = int(round(clamped_x * target.width / norm_max))
        local_y = int(round(clamped_y * target.height / norm_max))
        local_x = max(0, min(target.width, local_x))
        local_y = max(0, min(target.height, local_y))
        return (target.left + local_x, target.top + local_y)

    def global_to_norm(
        self,
        x: int,
        y: int,
        monitor: MonitorInfo | int | None = None,
        norm_max: int = 1000,
    ) -> tuple[int, int]:
        """Переводит глобальные экранные координаты (x, y) в нормализованные [0, norm_max]."""
        if norm_max <= 0:
            raise ValueError("Параметр norm_max должен быть положительным числом")

        target = (
            self.get_monitor_for_point(x, y)
            if monitor is None
            else self._resolve_target_monitor(monitor)
        )
        if target.width <= 0 or target.height <= 0:
            raise ValueError("Размеры монитора должны быть положительными")

        local_x = max(0, min(target.width, x - target.left))
        local_y = max(0, min(target.height, y - target.top))
        x_norm = int(round(local_x * norm_max / target.width))
        y_norm = int(round(local_y * norm_max / target.height))
        return (max(0, min(norm_max, x_norm)), max(0, min(norm_max, y_norm)))

    def _resolve_target_monitor(self, monitor: MonitorInfo | int | None) -> MonitorInfo:
        """Разрешает аргумент монитора в экземпляр MonitorInfo."""
        if isinstance(monitor, MonitorInfo):
            return monitor
        if isinstance(monitor, int):
            return self.get_monitor(monitor)
        return self.get_active_monitor()
