"""Модуль мониторинга безопасности (FailSafe) для аварийного прерывания операций."""

from __future__ import annotations

import collections
import math
import threading
import time
from collections.abc import Callable
from types import TracebackType
from typing import Any

from pynput import keyboard, mouse  # type: ignore[import-untyped]


class FailSafeMonitor:
    """Монитор аварийного прерывания (FailSafe) на основе сигналов клавиатуры и мыши."""

    def __init__(
        self,
        shake_window_seconds: float = 0.1,
        shake_threshold_px: float = 350.0,
        keyboard_factory: Callable[..., Any] | None = None,
        mouse_factory: Callable[..., Any] | None = None,
    ) -> None:
        """Инициализация монитора безопасности.

        Args:
            shake_window_seconds: Временное окно детекции потряхивания мыши (секунды).
            shake_threshold_px: Пороговая суммарная евклидова дистанция в пикселях.
            keyboard_factory: Фабрика создания слушателя клавиатуры.
            mouse_factory: Фабрика создания слушателя мыши.
        """
        self.abort_event: threading.Event = threading.Event()
        self._lock: threading.RLock = threading.RLock()
        self._abort_reason: str | None = None
        self._shake_window_seconds: float = shake_window_seconds
        self._shake_threshold_px: float = shake_threshold_px
        self._mouse_history: collections.deque[tuple[float, float, float]] = collections.deque()
        self._keyboard_factory: Callable[..., Any] = keyboard_factory or keyboard.Listener
        self._mouse_factory: Callable[..., Any] = mouse_factory or mouse.Listener
        self._keyboard_listener: Any | None = None
        self._mouse_listener: Any | None = None
        self._running: bool = False

    @property
    def abort_reason(self) -> str | None:
        """Возвращает текстовую причину последнего прерывания."""
        with self._lock:
            return self._abort_reason

    @property
    def is_running(self) -> bool:
        """Проверяет статус работы фоновых слушателей."""
        with self._lock:
            return self._running

    def is_aborted(self) -> bool:
        """Проверяет, взведен ли флаг аварийного прерывания."""
        return self.abort_event.is_set()

    def is_interrupted(self) -> bool:
        """Проверяет, взведен ли флаг аварийного прерывания (алиас к is_aborted)."""
        return self.is_aborted()

    def reset_abort(self) -> None:
        """Сбрасывает флаг прерывания и историю перемещений мыши."""
        with self._lock:
            self.abort_event.clear()
            self._abort_reason = None
            self._mouse_history.clear()

    def trigger_abort(self, reason: str) -> None:
        """Устанавливает флаг аварийного прерывания с фиксацией причины."""
        with self._lock:
            if not self.abort_event.is_set():
                self._abort_reason = reason
                self.abort_event.set()

    def check_abort(self) -> None:
        """Вызывает InterruptedError, если взведен флаг аварийного прерывания."""
        if not self.is_aborted():
            return
        reason = self.abort_reason or "Unknown reason"
        raise InterruptedError(f"FailSafe abort: {reason}")

    def _on_key_press(self, key: Any) -> None:
        """Обработчик нажатия клавиш клавиатуры."""
        try:
            if key == keyboard.Key.esc:
                self.trigger_abort("Escape key pressed")
        except Exception:
            pass

    def _calculate_total_distance(self) -> float:
        """Вычисляет суммарную евклидову дистанцию между соседними точками траектории."""
        if len(self._mouse_history) < 2:
            return 0.0
        return sum(
            math.hypot(
                self._mouse_history[i][1] - self._mouse_history[i - 1][1],
                self._mouse_history[i][2] - self._mouse_history[i - 1][2],
            )
            for i in range(1, len(self._mouse_history))
        )

    def _on_mouse_move(self, x: int | float, y: int | float) -> None:
        """Обработчик событий перемещения курсора мыши для поиска резких колебаний."""
        now = time.monotonic()
        with self._lock:
            if self.abort_event.is_set():
                return
            self._mouse_history.append((now, float(x), float(y)))
            cutoff = now - self._shake_window_seconds
            while self._mouse_history and self._mouse_history[0][0] < cutoff:
                self._mouse_history.popleft()

            total_distance = self._calculate_total_distance()
            if total_distance > self._shake_threshold_px:
                self.trigger_abort("Mouse shake detected")

    def start(self) -> None:
        """Запускает слушатели клавиатуры и мыши в фоновых daemon-потоках."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._keyboard_listener = self._keyboard_factory(on_press=self._on_key_press)
            self._mouse_listener = self._mouse_factory(on_move=self._on_mouse_move)
            self._keyboard_listener.daemon = True
            self._mouse_listener.daemon = True
            self._keyboard_listener.start()
            self._mouse_listener.start()

    def stop(self) -> None:
        """Останавливает слушатели клавиатуры и мыши."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            kb = self._keyboard_listener
            ms = self._mouse_listener
            self._keyboard_listener = None
            self._mouse_listener = None

        if kb is not None and hasattr(kb, "stop"):
            kb.stop()
        if ms is not None and hasattr(ms, "stop"):
            ms.stop()


# Глобальный экземпляр монитора безопасности
default_monitor: FailSafeMonitor = FailSafeMonitor()
abort_event: threading.Event = default_monitor.abort_event


def get_failsafe_monitor() -> FailSafeMonitor:
    """Возвращает глобальный экземпляр монитора безопасности."""
    return default_monitor


def is_aborted() -> bool:
    """Проверяет статус глобального аварийного флага."""
    return default_monitor.is_aborted()


def is_interrupted() -> bool:
    """Проверяет статус глобального аварийного флага (алиас к is_aborted)."""
    return default_monitor.is_interrupted()


def reset_abort() -> None:
    """Сбрасывает глобальный аварийный флаг."""
    default_monitor.reset_abort()


def trigger_abort(reason: str) -> None:
    """Активирует глобальное аварийное прерывание."""
    default_monitor.trigger_abort(reason)


def check_abort(monitor: FailSafeMonitor | None = None) -> None:
    """Проверяет состояние прерывания и выбрасывает исключение InterruptedError."""
    target = monitor or default_monitor
    target.check_abort()


class FailSafeContext:
    """Контекстный менеджер безопасного выполнения операций."""

    def __init__(self, monitor: FailSafeMonitor | None = None) -> None:
        """Инициализация контекстного менеджера с привязкой к монитору."""
        self._monitor: FailSafeMonitor = monitor or default_monitor

    def __enter__(self) -> FailSafeContext:
        """Вход в безопасный блок с предварительной проверкой прерывания."""
        self._monitor.check_abort()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Выход из безопасного блока с проверкой прерывания при отсутствии внутренних ошибок."""
        if exc_val is None:
            self._monitor.check_abort()

    def check(self) -> None:
        """Проверка прерывания внутри выполняемого блока."""
        self._monitor.check_abort()


def safe_execution(monitor: FailSafeMonitor | None = None) -> FailSafeContext:
    """Создает контекстный менеджер безопасного исполнения задач."""
    return FailSafeContext(monitor=monitor)
