"""Модуль визуального агента (VisionAgent) для управления интерфейсом ОС.

Обеспечивает:
- Плавное и безопасное перемещение курсора мыши с прерыванием через FailSafe.
- Выполнение клика с постпроверкой реакции интерфейса по разностным хешам (dhash).
- Ввод текста и функциональных клавиш в активные элементы.
- Инспекцию текущего экрана с генерацией Base64 для мультимодальных нейросетей.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import Any

import pyautogui  # type: ignore[import-untyped]

from core.failsafe import FailSafeMonitor, get_failsafe_monitor, safe_execution
from core.vision.monitor_manager import MonitorInfo, MonitorManager
from core.vision.screen_capture import ScreenCapture, ScreenFrame

logger = logging.getLogger(__name__)


def set_clipboard_text(text: str) -> bool:
    """Запись строки в системный буфер обмена Windows.

    Последовательно проверяет:
    1. win32clipboard (CF_UNICODETEXT)
    2. pyperclip
    3. ctypes (Windows user32/kernel32 API)
    """
    try:
        import win32clipboard  # type: ignore[import-untyped]
        import win32con  # type: ignore[import-untyped]

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
            return True
        finally:
            win32clipboard.CloseClipboard()
    except Exception as exc:
        logger.debug("Не удалось записать в буфер через win32clipboard: %s", exc)

    try:
        import pyperclip  # type: ignore[import-untyped]

        pyperclip.copy(text)
        return True
    except Exception as exc:
        logger.debug("Не удалось записать в буфер через pyperclip: %s", exc)

    if os.name == "nt":
        try:
            import ctypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            kernel32.GlobalAlloc.restype = ctypes.c_void_p
            kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
            kernel32.GlobalLock.restype = ctypes.c_void_p
            kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
            kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
            kernel32.GlobalFree.argtypes = [ctypes.c_void_p]

            user32.SetClipboardData.restype = ctypes.c_void_p
            user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

            if not user32.OpenClipboard(None):
                return False
            try:
                user32.EmptyClipboard()
                text_bytes = (text + "\0").encode("utf-16le")
                gmem_moveable = 0x0002
                cf_unicodetext = 13
                h_mem = kernel32.GlobalAlloc(gmem_moveable, len(text_bytes))
                if not h_mem:
                    return False
                p_mem = kernel32.GlobalLock(h_mem)
                if not p_mem:
                    kernel32.GlobalFree(h_mem)
                    return False
                ctypes.memmove(p_mem, text_bytes, len(text_bytes))
                kernel32.GlobalUnlock(h_mem)
                res = user32.SetClipboardData(cf_unicodetext, h_mem)
                if not res:
                    kernel32.GlobalFree(h_mem)
                    return False
                return True
            finally:
                user32.CloseClipboard()
        except Exception as exc:
            logger.warning("Ошибка при прямой записи в буфер через ctypes: %s", exc)

    return False


class ActionFailedError(RuntimeError):
    """Исключение при отсутствии реакции пользовательского интерфейса на действие."""


class VisionAgent:
    """Агент визуального взаимодействия с экраном и оконными элементами."""

    def __init__(
        self,
        screen_capture: ScreenCapture | None = None,
        monitor: FailSafeMonitor | None = None,
        pyautogui_module: Any | None = None,
        mouse_move_duration: float = 0.1,
        mouse_move_steps: int = 5,
        max_click_attempts: int = 3,
        post_click_delay_s: float = 0.1,
        clipboard_fn: Callable[[str], bool] | None = None,
    ) -> None:
        """Инициализация визуального агента.

        Args:
            screen_capture: Экземпляр захвата экрана.
            monitor: Монитор безопасности FailSafe (Esc / потряхивание мыши).
            pyautogui_module: Модуль автоматизации GUI (для подмены моками).
            mouse_move_duration: Длительность плавного перемещения курсора в секундах.
            mouse_move_steps: Количество шагов интерполяции траектории мыши.
            max_click_attempts: Лимит попыток клика до фиксации сбоя интерфейса.
            post_click_delay_s: Пауза между кликом и анализом изменения кадра.
            clipboard_fn: Пользовательская функция копирования в буфер обмена.
        """
        self._screen_capture: ScreenCapture = screen_capture or ScreenCapture()
        self._monitor: FailSafeMonitor = monitor or get_failsafe_monitor()
        self._pyautogui: Any = pyautogui_module or pyautogui
        self._mouse_move_duration: float = mouse_move_duration
        self._mouse_move_steps: int = max(1, mouse_move_steps)
        self._max_click_attempts: int = max(1, max_click_attempts)
        self._post_click_delay_s: float = max(0.0, post_click_delay_s)
        self._clipboard_fn: Callable[[str], bool] | None = clipboard_fn

        # Отключаем встроенный FailSafe pyautogui в пользу нашего FailSafeMonitor
        if hasattr(self._pyautogui, "FAILSAFE"):
            self._pyautogui.FAILSAFE = False

    @property
    def screen_capture(self) -> ScreenCapture:
        """Доступ к экземпляру захвата экрана."""
        return self._screen_capture

    def _has_real_monitor_manager(self) -> bool:
        """Проверяет наличие настоящего MonitorManager у объекта захвата экрана."""
        mgr = getattr(self._screen_capture, "monitor_manager", None)
        return isinstance(mgr, MonitorManager)

    def _get_monitor_geometry(
        self, monitor_index: int | None = None
    ) -> tuple[int, int, int, int, int]:
        """Возвращает (monitor_index, left, top, width, height) для целевого монитора."""
        if hasattr(self._screen_capture, "get_monitor_info"):
            info = self._screen_capture.get_monitor_info(monitor_index)
            if isinstance(info, MonitorInfo):
                return (info.index, info.left, info.top, info.width, info.height)

        idx = 1 if monitor_index is None else monitor_index
        size = (
            self._screen_capture.get_screen_size()
            if monitor_index is None
            else self._screen_capture.get_screen_size(monitor_index)
        )
        return (idx, 0, 0, int(size[0]), int(size[1]))

    def _capture_frame_for_monitor(
        self,
        monitor_index: int | None = None,
        include_base64: bool = False,
    ) -> ScreenFrame:
        """Захватывает кадр целевого монитора с сохранением совместимости моков."""
        if monitor_index is not None:
            return self._screen_capture.capture_frame(
                monitor_index=monitor_index, include_base64=include_base64
            )
        if self._has_real_monitor_manager():
            return self._screen_capture.capture_frame(
                monitor_index=None, include_base64=include_base64
            )
        return self._screen_capture.capture_frame(include_base64=include_base64)

    def norm_to_screen(
        self,
        x_norm: int,
        y_norm: int,
        monitor_index: int | None = None,
    ) -> tuple[int, int]:
        """Преобразование нормализованных [0, 1000] координат в глобальные экранные."""
        _, left, top, width, height = self._get_monitor_geometry(monitor_index)
        local_x, local_y = self._screen_capture.norm_to_screen(x_norm, y_norm, width, height)
        return (local_x + left, local_y + top)

    def screen_to_norm(
        self,
        x_screen: int,
        y_screen: int,
        monitor_index: int | None = None,
    ) -> tuple[int, int]:
        """Преобразование глобальных экранных координат в нормализованные [0, 1000]."""
        if monitor_index is None and self._has_real_monitor_manager():
            mgr = self._screen_capture.monitor_manager
            mon = mgr.get_monitor_for_point(x_screen, y_screen)
            return mgr.global_to_norm(x_screen, y_screen, mon)

        _, left, top, width, height = self._get_monitor_geometry(monitor_index)
        return self._screen_capture.screen_to_norm(x_screen - left, y_screen - top, width, height)

    def _copy_to_clipboard(self, text: str) -> bool:
        """Помещение текста в буфер обмена с безопасным откатом."""
        if self._clipboard_fn is not None:
            try:
                return bool(self._clipboard_fn(text))
            except Exception as exc:
                logger.warning("Ошибка внешней функции буфера обмена: %s", exc)
                return False
        return set_clipboard_text(text)

    def move_to(
        self,
        x: int,
        y: int,
        smooth: bool = True,
        duration: float | None = None,
        steps: int | None = None,
        normalized: bool = False,
        monitor_index: int | None = None,
    ) -> None:
        """Плавное перемещение курсора мыши в целевые экранные или нормализованные координаты."""
        with safe_execution(self._monitor) as safe:
            safe.check()
            if normalized:
                target_x, target_y = (
                    self.norm_to_screen(x, y, monitor_index=monitor_index)
                    if monitor_index is not None
                    else self.norm_to_screen(x, y)
                )
            else:
                target_x, target_y = (x, y)
            total_steps = steps if steps is not None else self._mouse_move_steps
            if not smooth or total_steps <= 1:
                self._pyautogui.moveTo(target_x, target_y)
                return

            total_duration = duration if duration is not None else self._mouse_move_duration
            step_delay = (total_duration / total_steps) if total_duration > 0 else 0.0

            try:
                start_x, start_y = self._pyautogui.position()
            except Exception:
                start_x, start_y = (target_x, target_y)

            self._interpolate_mouse_movement(
                start_x, start_y, target_x, target_y, total_steps, step_delay
            )

    def _interpolate_mouse_movement(
        self,
        start_x: int,
        start_y: int,
        target_x: int,
        target_y: int,
        total_steps: int,
        step_delay: float,
    ) -> None:
        """Интерполяция пути курсора по гладкой S-кривой."""
        suppress_ctx = (
            self._monitor.suppress_mouse_shake()
            if hasattr(self._monitor, "suppress_mouse_shake")
            else None
        )
        if suppress_ctx is None:
            self._run_mouse_interpolation_steps(
                start_x, start_y, target_x, target_y, total_steps, step_delay
            )
            return

        with suppress_ctx:
            self._run_mouse_interpolation_steps(
                start_x, start_y, target_x, target_y, total_steps, step_delay
            )

    def _run_mouse_interpolation_steps(
        self,
        start_x: int,
        start_y: int,
        target_x: int,
        target_y: int,
        total_steps: int,
        step_delay: float,
    ) -> None:
        """Пошаговое перемещение курсора с проверкой прерывания на каждом шаге."""
        for step in range(1, total_steps + 1):
            self._monitor.check_abort()
            ratio = step / total_steps
            # S-образное сглаживание Smoothstep
            ease = ratio * ratio * (3.0 - 2.0 * ratio)
            curr_x = int(round(start_x + (target_x - start_x) * ease))
            curr_y = int(round(start_y + (target_y - start_y) * ease))
            self._pyautogui.moveTo(curr_x, curr_y)
            if step_delay > 0:
                time.sleep(step_delay)

    def _execute_click_step(
        self,
        target_x: int,
        target_y: int,
        button: str,
        clicks: int,
        monitor_index: int | None = None,
    ) -> tuple[str, str]:
        """Выполнение одного клика с получением хешей кадра до и после действия."""
        before_frame = self._capture_frame_for_monitor(monitor_index)
        self.move_to(target_x, target_y)
        self._monitor.check_abort()
        self._pyautogui.click(target_x, target_y, button=button, clicks=clicks)
        if self._post_click_delay_s > 0:
            time.sleep(self._post_click_delay_s)
        self._monitor.check_abort()
        after_frame = self._capture_frame_for_monitor(monitor_index)
        return (before_frame.dhash, after_frame.dhash)

    def _resolve_click_target(
        self,
        x_norm: int,
        y_norm: int,
        normalized: bool,
        monitor_index: int | None,
    ) -> tuple[int, int, int | None]:
        """Вычисляет глобальные координаты клика и целевой индекс монитора для dhash."""
        has_mgr = self._has_real_monitor_manager()
        if not normalized:
            target_mon = (
                self._screen_capture.monitor_manager.get_monitor_for_point(x_norm, y_norm).index
                if monitor_index is None and has_mgr
                else monitor_index
            )
            return (x_norm, y_norm, target_mon)

        target_mon = (
            self._screen_capture.monitor_manager.get_active_monitor().index
            if monitor_index is None and has_mgr
            else monitor_index
        )
        if target_mon is not None:
            tx, ty = self.norm_to_screen(x_norm, y_norm, monitor_index=target_mon)
        else:
            tx, ty = self.norm_to_screen(x_norm, y_norm)
        return (tx, ty, target_mon)

    def click_at(
        self,
        x_norm: int,
        y_norm: int,
        button: str = "left",
        clicks: int = 1,
        verify_change: bool = True,
        max_attempts: int | None = None,
        normalized: bool = True,
        monitor_index: int | None = None,
    ) -> bool:
        """Клик по нормализованным или экранным координатам с контролем изменения монитора."""
        target_x, target_y, target_mon = self._resolve_click_target(
            x_norm, y_norm, normalized, monitor_index
        )

        if not verify_change:
            with safe_execution(self._monitor):
                self.move_to(target_x, target_y)
                self._pyautogui.click(target_x, target_y, button=button, clicks=clicks)
                return True

        limit = max_attempts if max_attempts is not None else self._max_click_attempts
        for attempt in range(1, limit + 1):
            with safe_execution(self._monitor):
                before_hash, after_hash = self._execute_click_step(
                    target_x, target_y, button, clicks, monitor_index=target_mon
                )
                if before_hash != after_hash:
                    logger.info(
                        "Клик успешен на попытке %d: %s -> %s", attempt, before_hash, after_hash
                    )
                    return True
                logger.warning(
                    "Попытка %d/%d: хеш экрана не изменился (%s)", attempt, limit, before_hash
                )

        raise ActionFailedError(
            f"Интерфейс не отреагировал на клик по координатам ({x_norm}, {y_norm}) "
            f"после {limit} попыток (хэш кадра не изменился)."
        )

    def type_text(self, text: str, interval: float = 0.0) -> None:
        """Ввод текста с клавиатуры в активный интерфейсный элемент.

        Для ASCII-текста используется прямой ввод через pyautogui.write.
        Для кириллицы и Unicode используется буфер обмена Windows и комбинация Ctrl+V.
        """
        if not text:
            return
        with safe_execution(self._monitor) as safe:
            safe.check()
            is_ascii = all(ord(ch) < 128 for ch in text)
            if is_ascii:
                self._pyautogui.write(text, interval=interval)
                return

            # Для кириллицы/Unicode копируем в буфер и нажимаем Ctrl+V
            copied = self._copy_to_clipboard(text)
            if copied:
                self._pyautogui.hotkey("ctrl", "v")
            else:
                self._pyautogui.write(text, interval=interval)

    def press_key(self, key: str, presses: int = 1) -> None:
        """Нажатие специальной или навигационной клавиши клавиатуры."""
        if not key:
            return
        with safe_execution(self._monitor) as safe:
            safe.check()
            self._pyautogui.press(key, presses=presses)

    def hotkey(self, *keys: str) -> None:
        """Нажатие комбинации горячих клавиш (например, 'ctrl', 'c')."""
        if not keys:
            return
        with safe_execution(self._monitor) as safe:
            safe.check()
            self._pyautogui.hotkey(*keys)

    def inspect_screen(self, monitor_index: int | None = None) -> dict[str, Any]:
        """Снимок и инспекция указанного или активного монитора для когнитивного ядра (VLM).

        Returns:
            Словарь с геометрией монитора, разностным хешем и изображением в Base64.
        """
        with safe_execution(self._monitor):
            frame = self._capture_frame_for_monitor(monitor_index, include_base64=True)
            return {
                "monitor_index": frame.monitor_index,
                "left": frame.left,
                "top": frame.top,
                "width": frame.width,
                "height": frame.height,
                "dpi_scale": frame.dpi_scale,
                "dhash": frame.dhash,
                "image_b64": frame.b64,
                "base64_image": frame.b64,
                "timestamp": time.time(),
            }
