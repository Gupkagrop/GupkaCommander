"""Интеграционные тесты для сквозного сценария Fast-Path (Спринт 1).

Проверяют совместную работу:
1. AppFinder: разрешение синонимов и поиск приложений.
2. OSController: управление процессами, браузером и окнами Windows.
3. FailSafe: мгновенное прерывание операций при нажатии Esc или тряске мыши.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pynput import keyboard  # type: ignore[import-untyped]

from core.app_finder import AppFinder, AppInfo
from core.failsafe import FailSafeMonitor, safe_execution
from core.os_controller import DangerousCommandError, OSController


class TestFastPathIntegration:
    """Набор сквозных интеграционных тестов для быстрого пути управления ОС."""

    def test_app_finder_resolve_and_os_controller_launch_success(self, tmp_path: Path) -> None:
        """Проверка успешного поиска приложения и его запуска в безопасном контексте."""
        cache_file = tmp_path / "app_cache.json"
        finder = AppFinder(cache_path=cache_file)
        finder._apps = [
            AppInfo(name="Notepad", path=r"C:\Windows\notepad.exe", source="test"),
            AppInfo(name="Calculator", path=r"C:\Windows\calc.exe", source="test"),
        ]
        controller = OSController()
        monitor = FailSafeMonitor()

        apps = finder.find("notepad")
        assert len(apps) > 0
        target_app = apps[0]

        executed = False
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "Started"
        fake_result.stderr = ""

        with (
            safe_execution(monitor=monitor) as ctx,
            patch("subprocess.run", return_value=fake_result) as mock_run,
        ):
            ctx.check()
            code, stdout, _ = controller.run_command(f'Start-Process "{target_app.path}"')
            ctx.check()
            assert code == 0
            assert stdout == "Started"
            mock_run.assert_called_once()
            executed = True

        assert executed
        assert not monitor.is_aborted()

    def test_app_launch_aborted_by_esc_key(self, tmp_path: Path) -> None:
        """Проверка аварийного прерывания запуска приложения при нажатии клавиши Escape."""
        cache_file = tmp_path / "app_cache.json"
        finder = AppFinder(cache_path=cache_file)
        finder._apps = [
            AppInfo(
                name="Telegram Desktop",
                path=r"C:\Apps\Telegram.exe",
                source="test",
            )
        ]
        controller = OSController()
        monitor = FailSafeMonitor()

        apps = finder.find("телега")
        assert len(apps) > 0

        with patch("subprocess.run") as mock_run:
            with pytest.raises(InterruptedError, match="FailSafe abort: Escape key pressed"):
                with safe_execution(monitor=monitor) as ctx:
                    # Симуляция нажатия клавиши Esc слушателем клавиатуры
                    monitor._on_key_press(keyboard.Key.esc)
                    ctx.check()
                    controller.run_command(f'Start-Process "{apps[0].path}"')

            # Команда в ОС не должна быть запущена после прерывания
            mock_run.assert_not_called()

        assert monitor.is_aborted()
        assert monitor.abort_reason == "Escape key pressed"

    def test_app_launch_aborted_by_mouse_shake(self, tmp_path: Path) -> None:
        """Проверка аварийного прерывания при обнаружении резкого потряхивания мыши."""
        cache_file = tmp_path / "app_cache.json"
        finder = AppFinder(cache_path=cache_file)
        finder._apps = [AppInfo(name="Google Chrome", path=r"C:\Apps\chrome.exe", source="test")]
        controller = OSController()
        monitor = FailSafeMonitor(shake_window_seconds=0.1, shake_threshold_px=350.0)

        apps = finder.find("хром")
        assert len(apps) > 0

        with patch("subprocess.run") as mock_run:
            with pytest.raises(InterruptedError, match="FailSafe abort: Mouse shake detected"):
                with safe_execution(monitor=monitor) as ctx:
                    # Симуляция резких колебаний курсора мыши
                    monitor._on_mouse_move(0, 0)
                    monitor._on_mouse_move(200, 0)
                    monitor._on_mouse_move(450, 0)
                    ctx.check()
                    controller.run_command(f'Start-Process "{apps[0].path}"')

            mock_run.assert_not_called()

        assert monitor.is_aborted()
        assert monitor.abort_reason == "Mouse shake detected"

    def test_web_alias_fast_path_safe_execution(self) -> None:
        """Проверка безопасного открытия веб-алиаса и прерывания до выполнения."""
        controller = OSController()
        monitor = FailSafeMonitor()

        with (
            safe_execution(monitor=monitor) as ctx,
            patch("os.startfile", create=True) as mock_startfile,
        ):
            ctx.check()
            opened = controller.open_alias("ютуб")
            ctx.check()
            assert opened is True
            mock_startfile.assert_called_once_with("https://www.youtube.com")

        # Проверка блокировки открытия при взведенном прерывании
        monitor.trigger_abort("Предварительная отмена")
        with patch("os.startfile", create=True) as mock_startfile:
            with pytest.raises(InterruptedError, match="FailSafe abort: Предварительная отмена"):
                with safe_execution(monitor=monitor) as ctx:
                    ctx.check()
                    controller.open_alias("ютуб")
            mock_startfile.assert_not_called()

    def test_window_operations_under_failsafe(self) -> None:
        """Проверка выполнения и прерывания операций над окнами Windows."""
        controller = OSController()
        monitor = FailSafeMonitor()

        with (
            safe_execution(monitor=monitor) as ctx,
            patch.object(
                controller.window,
                "list_windows",
                return_value=[(42, "Target Window")],
            ),
            patch("win32gui.ShowWindow"),
            patch("win32gui.SetForegroundWindow") as mock_focus,
            patch("win32gui.IsIconic", return_value=False),
        ):
            ctx.check()
            focused = controller.focus_window("target")
            ctx.check()
            assert focused is True
            mock_focus.assert_called_once_with(42)

        # Прерывание между шагами многосоставной операции с окнами
        with (
            patch.object(
                controller.window,
                "list_windows",
                return_value=[(42, "Target Window")],
            ),
            patch("win32gui.PostMessage") as mock_post,
        ):
            with pytest.raises(InterruptedError, match="FailSafe abort: Прервано посередине"):
                with safe_execution(monitor=monitor) as ctx:
                    windows = controller.list_windows()
                    assert len(windows) == 1
                    monitor.trigger_abort("Прервано посередине")
                    ctx.check()
                    controller.close_window("target")

            mock_post.assert_not_called()

    def test_dangerous_command_blocked_inside_safe_execution(self) -> None:
        """Проверка блокировки деструктивных команд оболочки внутри безопасного контекста."""
        controller = OSController()
        monitor = FailSafeMonitor()

        with safe_execution(monitor=monitor) as ctx:
            ctx.check()
            with pytest.raises(DangerousCommandError):
                controller.run_command("Format-Volume -DriveLetter C")
            ctx.check()

        assert not monitor.is_aborted()

    def test_multistep_pipeline_aborted_by_concurrent_thread(self, tmp_path: Path) -> None:
        """Проверка прерывания цепочки действий фоновым параллельным потоком."""
        cache_file = tmp_path / "app_cache.json"
        finder = AppFinder(cache_path=cache_file)
        finder._apps = [AppInfo(name="Editor", path="C:/editor.exe", source="test")]
        controller = OSController()
        monitor = FailSafeMonitor()

        def background_canceler() -> None:
            time.sleep(0.02)
            monitor._on_key_press(keyboard.Key.esc)

        thread = threading.Thread(target=background_canceler)
        thread.start()

        completed_steps = 0
        with patch("subprocess.run") as mock_run:
            with pytest.raises(InterruptedError, match="FailSafe abort: Escape key pressed"):
                with safe_execution(monitor=monitor) as ctx:
                    apps = finder.find("editor")
                    assert len(apps) == 1
                    completed_steps += 1
                    # Имитация паузы между действиями
                    time.sleep(0.05)
                    ctx.check()
                    controller.run_command(f'Start-Process "{apps[0].path}"')
                    completed_steps += 1

            mock_run.assert_not_called()

        thread.join()
        assert completed_steps == 1
        assert monitor.is_aborted()
