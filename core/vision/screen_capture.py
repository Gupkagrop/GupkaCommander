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

logger = logging.getLogger(__name__)


def set_dpi_awareness() -> bool:
    """Установка DPI-осведомленности процесса в Windows для точного маппинга координат."""
    if os.name != "nt":
        return False
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        res = int(ctypes.windll.shcore.SetProcessDpiAwareness(2))
        return res == 0
    except (AttributeError, OSError):
        try:
            return bool(ctypes.windll.user32.SetProcessDPIAware())
        except (AttributeError, OSError):
            return False


def norm_to_screen(
    x_norm: int,
    y_norm: int,
    screen_width: int,
    screen_height: int,
    norm_max: int = 1000,
) -> tuple[int, int]:
    """Преобразование нормализованных координат [0, norm_max] в экранные координаты.

    Args:
        x_norm: Нормализованная координата X.
        y_norm: Нормализованная координата Y.
        screen_width: Физическая ширина экрана в пикселях.
        screen_height: Физическая высота экрана в пикселях.
        norm_max: Максимальный диапазон нормализации (по умолчанию 1000 для Qwen2.5-VL).

    Returns:
        Кортеж физических координат (x, y) на экране.
    """
    if norm_max <= 0:
        raise ValueError("Параметр norm_max должен быть положительным числом")
    if screen_width <= 0 or screen_height <= 0:
        raise ValueError("Параметры screen_width и screen_height должны быть положительными")

    clamped_x = max(0, min(norm_max, x_norm))
    clamped_y = max(0, min(norm_max, y_norm))
    x_screen = int(round(clamped_x * screen_width / norm_max))
    y_screen = int(round(clamped_y * screen_height / norm_max))
    return (max(0, min(screen_width, x_screen)), max(0, min(screen_height, y_screen)))


def screen_to_norm(
    x_screen: int,
    y_screen: int,
    screen_width: int,
    screen_height: int,
    norm_max: int = 1000,
) -> tuple[int, int]:
    """Преобразование физических экранных координат в нормализованный диапазон [0, norm_max].

    Args:
        x_screen: Физическая координата X на экране.
        y_screen: Физическая координата Y на экране.
        screen_width: Физическая ширина экрана в пикселях.
        screen_height: Физическая высота экрана в пикселях.
        norm_max: Максимальный диапазон нормализации.

    Returns:
        Кортеж нормализованных координат (x_norm, y_norm).
    """
    if norm_max <= 0:
        raise ValueError("Параметр norm_max должен быть положительным числом")
    if screen_width <= 0 or screen_height <= 0:
        raise ValueError("Параметры screen_width и screen_height должны быть положительными")

    clamped_x = max(0, min(screen_width, x_screen))
    clamped_y = max(0, min(screen_height, y_screen))
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
    _png_bytes: bytes | None = None
    _b64: str | None = None

    @property
    def size(self) -> tuple[int, int]:
        """Разрешение экрана в формате (width, height)."""
        return (self.width, self.height)

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
    """Модуль захвата рабочего стола с расчетом хешей и поддержкой DPI."""

    set_dpi_awareness = staticmethod(set_dpi_awareness)

    def __init__(
        self,
        mss_factory: Any | None = None,
        auto_dpi: bool = True,
    ) -> None:
        """Инициализация модуля захвата экрана.

        Args:
            mss_factory: Фабрика создания экземпляра MSS (для тестирования).
            auto_dpi: Флаг автоматической настройки DPI-осведомленности в Windows.
        """
        if auto_dpi:
            set_dpi_awareness()
        self._mss_factory: Any = mss_factory or getattr(mss, "MSS", mss.mss)

    def get_screen_size(self, monitor_index: int = 1) -> tuple[int, int]:
        """Получение разрешения экрана указанного монитора."""
        try:
            with self._mss_factory() as sct:
                monitors = sct.monitors
                idx = monitor_index if monitor_index < len(monitors) else 1
                mon = monitors[idx]
                return (int(mon["width"]), int(mon["height"]))
        except Exception as err:
            logger.warning("Не удалось получить размер монитора: %s. Возврат 1920x1080.", err)
            return (1920, 1080)

    def capture_frame(
        self,
        monitor_index: int = 1,
        include_base64: bool = False,
    ) -> ScreenFrame:
        """Захват кадра экрана с расчетом разностного хеша dhash."""
        width = 1920
        height = 1080
        rgb_data: bytes = b""

        try:
            with self._mss_factory() as sct:
                monitors = sct.monitors
                idx = monitor_index if monitor_index < len(monitors) else 1
                mon = monitors[idx]
                width = int(mon["width"])
                height = int(mon["height"])
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
        frame = ScreenFrame(width=width, height=height, rgb=rgb_data, dhash=dhash_val)
        if include_base64:
            _ = frame.b64
        return frame

    def capture(self, monitor_index: int = 1, include_base64: bool = False) -> ScreenFrame:
        """Алиас метода capture_frame."""
        return self.capture_frame(monitor_index=monitor_index, include_base64=include_base64)

    def capture_base64(self, monitor_index: int = 1) -> str:
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
    ) -> tuple[int, int]:
        """Статический метод преобразования нормализованных координат в экранные."""
        return norm_to_screen(x_norm, y_norm, screen_width, screen_height, norm_max)

    @staticmethod
    def screen_to_norm(
        x_screen: int,
        y_screen: int,
        screen_width: int,
        screen_height: int,
        norm_max: int = 1000,
    ) -> tuple[int, int]:
        """Статический метод преобразования экранных координат в нормализованные."""
        return screen_to_norm(x_screen, y_screen, screen_width, screen_height, norm_max)

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
