"""Пакет компьютерного зрения и визуального заземления (Vision Grounding).

Экспортирует:
- ScreenCapture: быстрый захват рабочего стола через MSS с поддержкой DPI.
- ScreenFrame: контейнер кадра с расчетом dhash и кодированием в Base64.
- VisionAgent: визуальный оператор с плавным движением мыши и проверкой кликов.
- ActionFailedError: исключение при отсутствии реакции интерфейса на действие.
- norm_to_screen, screen_to_norm: функции преобразования координат.
- compute_dhash, hamming_distance: утилиты разностного хеширования.
"""

from core.vision.screen_capture import (
    ScreenCapture,
    ScreenFrame,
    compute_dhash,
    hamming_distance,
    norm_to_screen,
    screen_to_norm,
    set_dpi_awareness,
)
from core.vision.vision_agent import ActionFailedError, VisionAgent

__all__ = [
    "ActionFailedError",
    "ScreenCapture",
    "ScreenFrame",
    "VisionAgent",
    "compute_dhash",
    "hamming_distance",
    "norm_to_screen",
    "screen_to_norm",
    "set_dpi_awareness",
]
