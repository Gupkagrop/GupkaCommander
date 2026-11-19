"""
> [!NOTE]
> Модуль gui.hotkey_manager: Глобальный менеджер горячих клавиш Windows 11.
> Регистрирует комбинацию Alt+Space через Win32 API (RegisterHotKey)
> с надежным автоматическим переключением на pynput в случае недоступности.
"""

from __future__ import annotations

import ctypes
import logging
import platform
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from typing import Any

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

# Константы Win32 API для RegisterHotKey
MOD_ALT: int = 0x0001
MOD_CONTROL: int = 0x0002
MOD_SHIFT: int = 0x0004
MOD_WIN: int = 0x0008
MOD_NOREPEAT: int = 0x4000
VK_SPACE: int = 0x20
WM_HOTKEY: int = 0x0312
WM_QUIT: int = 0x0012
PM_REMOVE: int = 0x0001
HOTKEY_ID: int = 0x0042


class HotkeyManager(QObject):
    """
    Глобальный диспетчер горячих клавиш.

    По умолчанию перехватывает сочетание Alt+Space для открытия панели SpotlightBar.
    Использует низкоуровневый Win32 RegisterHotKey в отдельном потоке сообщений,
    а при невозможности регистрации (например, занято другим процессом или Linux/CI)
    бесшовно переключается на pynput.keyboard.GlobalHotKeys.
    """

    hotkey_pressed = Signal()

    def __init__(
        self,
        callback: Callable[[], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        """
        Инициализация диспетчера горячих клавиш.

        Args:
            callback: Опциональный прямой обратный вызов при срабатывании.
            parent: Родительский QObject для управления временем жизни Qt.
        """
        super().__init__(parent)
        self._callback: Callable[[], None] | None = callback
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._win32_thread: threading.Thread | None = None
        self._win32_thread_id: int = 0
        self._pynput_hotkeys: Any | None = None
        self._active_backend: str = "none"
        self._is_registered: bool = False

    @property
    def is_active(self) -> bool:
        """Проверяет, активен ли слушатель горячих клавиш."""
        with self._lock:
            return self._is_registered

    @property
    def backend_name(self) -> str:
        """Возвращает имя текущего активного бэкенда ('win32', 'pynput', 'none')."""
        with self._lock:
            return self._active_backend

    def start(self) -> bool:
        """
        Запускает регистрацию глобального хоткея Alt+Space.

        Сначала пытается использовать Win32 RegisterHotKey.
        При неудаче автоматически переходит к pynput.

        Returns:
            bool: True, если хоткей успешно зарегистрирован.
        """
        with self._lock:
            if self._is_registered:
                logger.debug("HotkeyManager уже запущен.")
                return True

            self._stop_event.clear()

            # 1. Попытка Win32 RegisterHotKey на платформе Windows
            if platform.system() == "Windows":
                if self._start_win32():
                    self._active_backend = "win32"
                    self._is_registered = True
                    logger.info("Глобальный хоткей Alt+Space зарегистрирован через Win32 API.")
                    return True
                logger.warning(
                    "Не удалось зарегистрировать хоткей через Win32. Переключаемся на pynput."
                )

            # 2. Резервный запуск через pynput
            if self._start_pynput():
                self._active_backend = "pynput"
                self._is_registered = True
                logger.info("Глобальный хоткей Alt+Space зарегистрирован через pynput.")
                return True

            self._active_backend = "none"
            self._is_registered = False
            logger.error("Не удалось зарегистрировать хоткей ни через Win32, ни через pynput.")
            return False

    def stop(self) -> None:
        """Останавливает слушатели и снимает регистрацию горячих клавиш."""
        with self._lock:
            self._stop_event.set()

            # Остановка Win32 потока
            if self._win32_thread is not None and self._win32_thread.is_alive():
                self._stop_win32()
                self._win32_thread.join(timeout=1.0)
                self._win32_thread = None

            # Остановка pynput
            if self._pynput_hotkeys is not None:
                try:
                    self._pynput_hotkeys.stop()
                except Exception as exc:
                    logger.warning("Ошибка при остановке pynput listener: %s", exc)
                self._pynput_hotkeys = None

            self._active_backend = "none"
            self._is_registered = False
            logger.info("Слушатель горячих клавиш остановлен.")

    def _on_hotkey_triggered(self) -> None:
        """Внутренний обработчик срабатывания горячей клавиши."""
        logger.debug("Хоткей Alt+Space нажат.")
        self.hotkey_pressed.emit()
        if self._callback is not None:
            try:
                self._callback()
            except Exception as exc:
                logger.exception("Ошибка при вызове callback хоткея: %s", exc)

    def _start_win32(self) -> bool:
        """Запуск Win32 цикла сообщений в отдельном фоновом потоке."""
        registration_event = threading.Event()
        registration_success = [False]

        def _worker() -> None:
            user32 = ctypes.windll.user32
            thread_id = user32.GetCurrentThreadId()
            self._win32_thread_id = thread_id

            # Регистрируем Alt+Space (MOD_ALT | MOD_NOREPEAT, VK_SPACE)
            success = bool(
                user32.RegisterHotKey(
                    None,
                    HOTKEY_ID,
                    MOD_ALT | MOD_NOREPEAT,
                    VK_SPACE,
                )
            )

            registration_success[0] = success
            registration_event.set()

            if not success:
                return

            msg = wintypes.MSG()
            while not self._stop_event.is_set():
                # Проверяем наличие сообщений в очереди потока
                has_msg = bool(user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE))
                if has_msg:
                    if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                        self._on_hotkey_triggered()
                    elif msg.message == WM_QUIT:
                        break
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                else:
                    time.sleep(0.02)

            user32.UnregisterHotKey(None, HOTKEY_ID)

        self._win32_thread = threading.Thread(
            target=_worker,
            name="Win32HotkeyThread",
            daemon=True,
        )
        self._win32_thread.start()

        # Ожидаем завершения регистрации
        registration_event.wait(timeout=1.0)
        return registration_success[0]

    def _stop_win32(self) -> None:
        """Посылает сигнал завершения потоку сообщений Win32."""
        if platform.system() == "Windows" and self._win32_thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(
                    self._win32_thread_id,
                    WM_QUIT,
                    0,
                    0,
                )
            except Exception as exc:
                logger.debug(
                    "Не удалось отправить WM_QUIT в поток %d: %s", self._win32_thread_id, exc
                )

    def _start_pynput(self) -> bool:
        """Запуск резервного слушателя через pynput.keyboard."""
        try:
            from pynput import keyboard  # type: ignore[import-untyped]

            self._pynput_hotkeys = keyboard.GlobalHotKeys(
                {"<alt>+<space>": self._on_hotkey_triggered}
            )
            self._pynput_hotkeys.start()
            return True
        except Exception as exc:
            logger.warning("Не удалось запустить pynput.keyboard.GlobalHotKeys: %s", exc)
            return False
