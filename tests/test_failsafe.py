"""Модульные тесты для подсистемы аварийного прерывания (FailSafe)."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest
from pynput import keyboard  # type: ignore[import-untyped]

from core.failsafe import (
    FailSafeContext,
    FailSafeMonitor,
    abort_event,
    check_abort,
    default_monitor,
    get_failsafe_monitor,
    is_aborted,
    reset_abort,
    safe_execution,
    trigger_abort,
)


class DummyListener:
    """Заглушка для слушателей клавиатуры и мыши."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.daemon: bool = False
        self.started: bool = False
        self.stopped: bool = False

    def start(self) -> None:
        """Запуск заглушки."""
        self.started = True

    def stop(self) -> None:
        """Остановка заглушки."""
        self.stopped = True


def test_monitor_initial_state() -> None:
    """Проверка исходного состояния нового экземпляра монитора."""
    monitor = FailSafeMonitor()
    assert not monitor.is_aborted()
    assert not monitor.abort_event.is_set()
    assert monitor.abort_reason is None
    assert not monitor.is_running
    monitor.check_abort()


def test_trigger_and_reset_abort() -> None:
    """Проверка установки и сброса флага прерывания."""
    monitor = FailSafeMonitor()
    monitor.trigger_abort("Пользовательская причина")

    assert monitor.is_aborted()
    assert monitor.abort_event.is_set()
    assert monitor.abort_reason == "Пользовательская причина"

    with pytest.raises(InterruptedError, match="FailSafe abort: Пользовательская причина"):
        monitor.check_abort()

    # Повторный вызов не перезаписывает первую причину
    monitor.trigger_abort("Вторая причина")
    assert monitor.abort_reason == "Пользовательская причина"

    # Сброс
    monitor.reset_abort()
    assert not monitor.is_aborted()
    assert not monitor.abort_event.is_set()
    assert monitor.abort_reason is None
    monitor.check_abort()


def test_keyboard_listener_esc_trigger() -> None:
    """Проверка реакции на нажатие клавиши Escape."""
    monitor = FailSafeMonitor()

    # Нажатие обычной клавиши (не Esc)
    other_key = keyboard.KeyCode.from_char("a")
    monitor._on_key_press(other_key)
    assert not monitor.is_aborted()

    # Нажатие клавиши Escape
    monitor._on_key_press(keyboard.Key.esc)
    assert monitor.is_aborted()
    assert monitor.abort_reason == "Escape key pressed"


def test_keyboard_listener_exception_resilience() -> None:
    """Проверка устойчивости обработчика клавиш к некорректным объектам."""
    monitor = FailSafeMonitor()

    class BadKey:
        def __eq__(self, other: object) -> bool:
            raise RuntimeError("Ошибка сравнения")

    monitor._on_key_press(BadKey())
    assert not monitor.is_aborted()


def test_mouse_shake_detector_trigger() -> None:
    """Проверка детекции резкого потряхивания мыши (> 350px за 100мс)."""
    monitor = FailSafeMonitor(shake_window_seconds=0.1, shake_threshold_px=350.0)

    # Движение в пределах допустимого (< 350px)
    monitor._on_mouse_move(0, 0)
    monitor._on_mouse_move(100, 0)
    monitor._on_mouse_move(150, 0)
    assert not monitor.is_aborted()

    # Резкое колебание: дельта 250px (суммарно 100 + 50 + 250 = 400px > 350px)
    monitor._on_mouse_move(400, 0)
    assert monitor.is_aborted()
    assert monitor.abort_reason == "Mouse shake detected"


def test_mouse_movement_spread_over_time_does_not_trigger() -> None:
    """Проверка, что медленные перемещения не вызывают ложных срабатываний."""
    monitor = FailSafeMonitor(shake_window_seconds=0.1, shake_threshold_px=350.0)

    with patch("time.monotonic") as mock_time:
        mock_time.return_value = 1000.0
        monitor._on_mouse_move(0, 0)

        mock_time.return_value = 1000.05
        monitor._on_mouse_move(200, 0)

        # Прошло 150мс с первой точки, первая точка должна выпасть из окна
        mock_time.return_value = 1000.20
        monitor._on_mouse_move(350, 0)

        assert not monitor.is_aborted()


def test_start_and_stop_listeners() -> None:
    """Проверка корректного запуска и остановки фоновых слушателей."""
    kb_mock = DummyListener()
    ms_mock = DummyListener()

    monitor = FailSafeMonitor(
        keyboard_factory=lambda **kw: kb_mock,
        mouse_factory=lambda **kw: ms_mock,
    )

    monitor.start()
    assert monitor.is_running
    assert kb_mock.started
    assert ms_mock.started
    assert kb_mock.daemon
    assert ms_mock.daemon

    # Повторный запуск не создает дубликатов
    monitor.start()
    assert monitor.is_running

    monitor.stop()
    assert not monitor.is_running
    assert kb_mock.stopped
    assert ms_mock.stopped

    # Повторная остановка безопасна
    monitor.stop()


def test_safe_execution_context_manager_normal() -> None:
    """Проверка штатного прохождения безопасного контекста."""
    monitor = FailSafeMonitor()
    executed = False

    with safe_execution(monitor=monitor) as ctx:
        ctx.check()
        executed = True

    assert executed


def test_safe_execution_context_manager_pre_aborted() -> None:
    """Проверка прерывания при входе в контекст с уже активным абортом."""
    monitor = FailSafeMonitor()
    monitor.trigger_abort("Предварительное прерывание")

    with pytest.raises(InterruptedError, match="FailSafe abort: Предварительное прерывание"):
        with safe_execution(monitor=monitor):
            pass


def test_safe_execution_context_manager_abort_inside() -> None:
    """Проверка вызова прерывания внутри выполняемого блока."""
    monitor = FailSafeMonitor()

    with pytest.raises(InterruptedError, match="FailSafe abort: Прервано в процессе"):
        with safe_execution(monitor=monitor) as ctx:
            monitor.trigger_abort("Прервано в процессе")
            ctx.check()


def test_safe_execution_exit_checks_abort() -> None:
    """Проверка, что выход из контекста проверяет статус отмены."""
    monitor = FailSafeMonitor()

    with pytest.raises(InterruptedError, match="FailSafe abort: Сработало в фоне"):
        with safe_execution(monitor=monitor):
            monitor.trigger_abort("Сработало в фоне")


def test_safe_execution_preserves_internal_exceptions() -> None:
    """Проверка, что пользовательские исключения внутри блока не подменяются."""
    monitor = FailSafeMonitor()

    with pytest.raises(ValueError, match="Кастомная ошибка"):
        with safe_execution(monitor=monitor):
            raise ValueError("Кастомная ошибка")


def test_failsafe_context_direct_instantiation() -> None:
    """Проверка прямого создания экземпляра FailSafeContext."""
    monitor = FailSafeMonitor()
    ctx = FailSafeContext(monitor=monitor)
    with ctx:
        ctx.check()


def test_global_state_and_functions() -> None:
    """Проверка глобальных функций и синглтона."""
    reset_abort()
    assert not is_aborted()
    assert not abort_event.is_set()
    assert get_failsafe_monitor() is default_monitor

    trigger_abort("Глобальный аборт")
    assert is_aborted()
    assert abort_event.is_set()

    with pytest.raises(InterruptedError, match="FailSafe abort: Глобальный аборт"):
        check_abort()

    with pytest.raises(InterruptedError, match="FailSafe abort: Глобальный аборт"):
        with safe_execution():
            pass

    reset_abort()
    assert not is_aborted()
    assert not abort_event.is_set()
    check_abort()


def test_thread_safety_concurrent_abort_and_reset() -> None:
    """Проверка потокобезопасности при конкурентных вызовах из разных потоков."""
    monitor = FailSafeMonitor()
    iterations = 500
    errors: list[Exception] = []

    def abort_worker() -> None:
        try:
            for i in range(iterations):
                monitor.trigger_abort(f"Abort {i}")
                time.sleep(0.0001)
        except Exception as exc:
            errors.append(exc)

    def reset_worker() -> None:
        try:
            for _ in range(iterations):
                monitor.reset_abort()
                time.sleep(0.0001)
        except Exception as exc:
            errors.append(exc)

    def move_worker() -> None:
        try:
            for i in range(iterations):
                monitor._on_mouse_move(i * 10, i * 10)
                time.sleep(0.0001)
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=abort_worker)
    t2 = threading.Thread(target=reset_worker)
    t3 = threading.Thread(target=move_worker)

    t1.start()
    t2.start()
    t3.start()

    t1.join()
    t2.join()
    t3.join()

    assert not errors
