"""Модуль высокоскоростного захвата экрана и работы с визуальными координатами.

Предоставляет:
- ScreenCapture: захват рабочего стола через MSS с поддержкой DPI awareness.
- ScreenFrame: контейнер кадра с вычислением dhash и кодированием в Base64.
- norm_to_screen / screen_to_norm: преобразование нормализованных [0, 1000] и экранных координат.
- compute_dhash / hamming_distance: алгоритмы разностного хеширования для детекции изменений UI.
"""

from __future__ import annotations

import base64
import ctypes
import dataclasses
import logging
import os
from typing import Any

import mss
import mss.tools
import numpy as np

from core.vision.monitor_manager import MonitorInfo, MonitorManager

logger = logging.getLogger(__name__)


def set_dpi_awareness() -> bool:
    """Установка DPI-осведомленности процесса в Windows (Per-Monitor v2 с откатом)."""
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        ctx_fn = user32.SetProcessDpiAwarenessContext
        ctx_fn.argtypes = [ctypes.c_void_p]
        ctx_fn.restype = ctypes.c_bool
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        if bool(ctx_fn(ctypes.c_void_p(-4))):
            return True
    except (AttributeError, OSError, ValueError):
        pass
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        res = int(ctypes.windll.shcore.SetProcessDpiAwareness(2))
        if res == 0:
            return True
    except (AttributeError, OSError, ValueError):
        pass
    try:
        return bool(ctypes.windll.user32.SetProcessDPIAware())
    except (AttributeError, OSError, ValueError):
        return False


def norm_to_screen(
    x_norm: int,
    y_norm: int,
    screen_width: int,
    screen_height: int,
    norm_max: int = 1000,
    left: int = 0,
    top: int = 0,
) -> tuple[int, int]:
    """Преобразование нормализованных координат [0, norm_max] в глобальные экранные координаты.

    Args:
        x_norm: Нормализованная координата X.
        y_norm: Нормализованная координата Y.
        screen_width: Физическая ширина целевого монитора в пикселях.
        screen_height: Физическая высота целевого монитора в пикселях.
        norm_max: Максимальный диапазон нормализации (по умолчанию 1000 для VLM).
        left: Глобальное смещение левой границы монитора.
        top: Глобальное смещение верхней границы монитора.

    Returns:
        Кортеж глобальных физических координат (x, y) на виртуальном рабочем столе.
    """
    if norm_max <= 0:
        raise ValueError("Параметр norm_max должен быть положительным числом")
    if screen_width <= 0 or screen_height <= 0:
        raise ValueError("Параметры screen_width и screen_height должны быть положительными")

    clamped_x = max(0, min(norm_max, x_norm))
    clamped_y = max(0, min(norm_max, y_norm))
    local_x = max(0, min(screen_width, int(round(clamped_x * screen_width / norm_max))))
    local_y = max(0, min(screen_height, int(round(clamped_y * screen_height / norm_max))))
    return (left + local_x, top + local_y)


def screen_to_norm(
    x_screen: int,
    y_screen: int,
    screen_width: int,
    screen_height: int,
    norm_max: int = 1000,
    left: int = 0,
    top: int = 0,
) -> tuple[int, int]:
    """Преобразование глобальных экранных координат в нормализованный диапазон [0, norm_max].

    Args:
        x_screen: Физическая координата X на виртуальном экране.
        y_screen: Физическая координата Y на виртуальном экране.
        screen_width: Физическая ширина монитора в пикселях.
        screen_height: Физическая высота монитора в пикселях.
        norm_max: Максимальный диапазон нормализации.
        left: Глобальное смещение левой границы монитора.
        top: Глобальное смещение верхней границы монитора.

    Returns:
        Кортеж нормализованных координат (x_norm, y_norm).
    """
    if norm_max <= 0:
        raise ValueError("Параметр norm_max должен быть положительным числом")
    if screen_width <= 0 or screen_height <= 0:
        raise ValueError("Параметры screen_width и screen_height должны быть положительными")

    clamped_x = max(0, min(screen_width, x_screen - left))
    clamped_y = max(0, min(screen_height, y_screen - top))
    x_norm = int(round(clamped_x * norm_max / screen_width))
    y_norm = int(round(clamped_y * norm_max / screen_height))
    return (max(0, min(norm_max, x_norm)), max(0, min(norm_max, y_norm)))


def compute_dhash(
    rgb_bytes: bytes,
    width: int,
    height: int,
    hash_size: int = 8,
) -> str:
    """Вычисление разностного хеша (dhash) кадра экрана для быстрого сравнения.

    Args:
        rgb_bytes: Байты изображения в формате RGB (3 байта на пиксель).
        width: Ширина исходного изображения.
        height: Высота исходного изображения.
        hash_size: Размерность сетки сравнения (по умолчанию 8 дает 64-битный хеш).

    Returns:
        Шестнадцатеричная строка хеша фиксированной длины.
    """
    hex_len = (hash_size * hash_size) // 4
    if width <= 0 or height <= 0 or not rgb_bytes:
        return "0" * hex_len

    arr = np.frombuffer(rgb_bytes, dtype=np.uint8).reshape((height, width, 3))
    # Перевод в оттенки серого по стандартной формуле яркости (ITU-R 601-2)
    gray = (
        arr[:, :, 0].astype(np.uint32) * 299
        + arr[:, :, 1].astype(np.uint32) * 587
        + arr[:, :, 2].astype(np.uint32) * 114
    ) // 1000

    row_idx = np.linspace(0, height - 1, hash_size, dtype=int)
    col_idx = np.linspace(0, width - 1, hash_size + 1, dtype=int)
    sampled = gray[np.ix_(row_idx, col_idx)]

    # Сравнение смежных пикселей по горизонтали
    diff = sampled[:, 1:] > sampled[:, :-1]
    bits = diff.flatten()

    val = 0
    for bit in bits:
        val = (val << 1) | int(bit)

    return f"{val:0{hex_len}x}"


def hamming_distance(hash1: str, hash2: str) -> int:
    """Вычисление расстояния Хэмминга между двумя шестнадцатеричными хешами."""
    if not hash1 or not hash2:
        return 64
    try:
        val1 = int(hash1, 16)
        val2 = int(hash2, 16)
        return bin(val1 ^ val2).count("1")
    except ValueError:
        return 64


@dataclasses.dataclass
class ScreenFrame:
    """Контейнер захваченного кадра экрана с метаданными и кодированием."""

    width: int
    height: int
    rgb: bytes
    dhash: str = ""
    monitor_index: int = 1
    left: int = 0
    top: int = 0
    dpi_scale: float = 1.0
    _png_bytes: bytes | None = None
    _b64: str | None = None

    @property
    def size(self) -> tuple[int, int]:
        """Разрешение экрана в формате (width, height)."""
        return (self.width, self.height)

    @property
    def rect(self) -> tuple[int, int, int, int]:
        """Глобальная геометрия кадра в формате (left, top, width, height)."""
        return (self.left, self.top, self.width, self.height)

    @property
    def png_bytes(self) -> bytes:
        """Сжатые PNG-байты кадра."""
        if self._png_bytes is None:
            raw_png = mss.tools.to_png(self.rgb, self.size)
            self._png_bytes = raw_png if raw_png is not None else b""
        return self._png_bytes

    @property
    def b64(self) -> str:
        """Строка изображения в кодировке Base64 для мультимодальных моделей."""
        if self._b64 is None:
            self._b64 = base64.b64encode(self.png_bytes).decode("utf-8")
        return self._b64

    @property
    def base64_image(self) -> str:
        """Алиас для совместимости со спецификациями VLM-промптов."""
        return self.b64


class ScreenCapture:
    """Модуль захвата рабочего стола с расчетом хешей и поддержкой Per-Monitor DPI v2."""

    set_dpi_awareness = staticmethod(set_dpi_awareness)

    def __init__(
        self,
        mss_factory: Any | None = None,
        auto_dpi: bool = True,
        monitor_manager: MonitorManager | None = None,
    ) -> None:
        """Инициализация модуля захвата экрана.

        Args:
            mss_factory: Фабрика создания экземпляра MSS (для тестирования).
            auto_dpi: Флаг автоматической настройки DPI-осведомленности в Windows.
            monitor_manager: Внешний менеджер мультимониторной геометрии.
        """
        if auto_dpi:
            set_dpi_awareness()
        self._mss_factory: Any = mss_factory or getattr(mss, "MSS", mss.mss)
        self._monitor_manager: MonitorManager = monitor_manager or MonitorManager(
            mss_factory=self._mss_factory
        )

    @property
    def monitor_manager(self) -> MonitorManager:
        """Доступ к менеджеру мониторов."""
        return self._monitor_manager

    def _resolve_target_index(self, monitor_index: int | None) -> int:
        """Разрешает индекс монитора (None означает активный монитор)."""
        if monitor_index is not None:
            return monitor_index
        return self._monitor_manager.get_active_monitor().index

    @staticmethod
    def _select_mss_monitor(monitors: list[Any], target_index: int) -> tuple[int, dict[str, Any]]:
        """Выбирает словарь геометрии MSS с безопасным fallback на основной монитор."""
        if 0 <= target_index < len(monitors):
            return (target_index, dict(monitors[target_index]))
        fallback_idx = 1 if len(monitors) > 1 else 0
        return (fallback_idx, dict(monitors[fallback_idx]))

    def get_monitor_info(self, monitor_index: int | None = 1) -> MonitorInfo:
        """Возвращает метаданные указанного или активного (None) монитора."""
        if monitor_index is None:
            return self._monitor_manager.get_active_monitor()
        return self._monitor_manager.get_monitor(monitor_index)

    def get_screen_size(self, monitor_index: int | None = 1) -> tuple[int, int]:
        """Получение разрешения экрана указанного или активного монитора."""
        try:
            target_idx = self._resolve_target_index(monitor_index)
            with self._mss_factory() as sct:
                _, mon = self._select_mss_monitor(list(sct.monitors), target_idx)
                return (int(mon["width"]), int(mon["height"]))
        except Exception as err:
            logger.warning("Не удалось получить размер монитора: %s. Возврат 1920x1080.", err)
            return (1920, 1080)

    def capture_frame(
        self,
        monitor_index: int | None = 1,
        include_base64: bool = False,
    ) -> ScreenFrame:
        """Захват кадра указанного (или активного при None) монитора с расчетом dhash."""
        width = 1920
        height = 1080
        left = 0
        top = 0
        dpi_scale = 1.0
        resolved_idx = self._resolve_target_index(monitor_index)
        rgb_data: bytes = b""

        try:
            with self._mss_factory() as sct:
                resolved_idx, mon = self._select_mss_monitor(list(sct.monitors), resolved_idx)
                left = int(mon.get("left", 0))
                top = int(mon.get("top", 0))
                width = int(mon["width"])
                height = int(mon["height"])
                dpi_scale = self._monitor_manager.get_dpi_scale_for_rect(left, top, width, height)
                sct_img = sct.grab(mon)
                rgb_data = bytes(sct_img.rgb)
                raw_size = getattr(sct_img, "size", (width, height))
                width = int(raw_size[0] if isinstance(raw_size, tuple) else raw_size.width)
                height = int(raw_size[1] if isinstance(raw_size, tuple) else raw_size.height)
        except Exception as err:
            logger.warning(
                "Ошибка захвата экрана через MSS: %s. Формирование резервного кадра %dx%d.",
                err,
                width,
                height,
            )
            rgb_data = b"\x00" * (width * height * 3)

        dhash_val = compute_dhash(rgb_data, width, height)
        frame = ScreenFrame(
            width=width,
            height=height,
            rgb=rgb_data,
            dhash=dhash_val,
            monitor_index=resolved_idx,
            left=left,
            top=top,
            dpi_scale=dpi_scale,
        )
        if include_base64:
            _ = frame.b64
        return frame

    def capture_active_monitor(self, include_base64: bool = False) -> ScreenFrame:
        """Захват кадра активного монитора (по foreground-окну или курсору)."""
        return self.capture_frame(monitor_index=None, include_base64=include_base64)

    def capture(
        self,
        monitor_index: int | None = 1,
        include_base64: bool = False,
    ) -> ScreenFrame:
        """Алиас метода capture_frame."""
        return self.capture_frame(monitor_index=monitor_index, include_base64=include_base64)

    def capture_base64(self, monitor_index: int | None = 1) -> str:
        """Захват экрана с немедленным кодированием в строку Base64."""
        frame = self.capture_frame(monitor_index=monitor_index, include_base64=True)
        return frame.b64

    @staticmethod
    def norm_to_screen(
        x_norm: int,
        y_norm: int,
        screen_width: int,
        screen_height: int,
        norm_max: int = 1000,
        left: int = 0,
        top: int = 0,
    ) -> tuple[int, int]:
        """Статический метод преобразования нормализованных координат в глобальные экранные."""
        return norm_to_screen(
            x_norm,
            y_norm,
            screen_width,
            screen_height,
            norm_max=norm_max,
            left=left,
            top=top,
        )

    @staticmethod
    def screen_to_norm(
        x_screen: int,
        y_screen: int,
        screen_width: int,
        screen_height: int,
        norm_max: int = 1000,
        left: int = 0,
        top: int = 0,
    ) -> tuple[int, int]:
        """Статический метод преобразования глобальных экранных координат в нормализованные."""
        return screen_to_norm(
            x_screen,
            y_screen,
            screen_width,
            screen_height,
            norm_max=norm_max,
            left=left,
            top=top,
        )

    @staticmethod
    def calculate_dhash(rgb_bytes: bytes, width: int, height: int, hash_size: int = 8) -> str:
        """Статический метод расчета dhash по RGB-байтам."""
        return compute_dhash(rgb_bytes, width, height, hash_size)

    @staticmethod
    def encode_base64(rgb_bytes: bytes, size: tuple[int, int]) -> str:
        """Кодирование RGB-байтов в Base64 PNG строку."""
        png_data = mss.tools.to_png(rgb_bytes, size)
        if not png_data:
            return ""
        return base64.b64encode(png_data).decode("utf-8")
