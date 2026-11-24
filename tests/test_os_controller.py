"""Тесты для модуля управления операционной системой Windows 11 (OSController).

Проверяются компоненты WebLauncher, ShellExecutor, WindowManager и фасад OSController
с использованием mock-изоляции для win32, subprocess и браузера.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import win32con  # type: ignore[import-untyped]

from core.os_controller import (
    DangerousCommandError,
    OSController,
    ShellExecutor,
    WebLauncher,
    WindowManager,
)


class TestWebLauncher:
    """Набор тестов для компонента WebLauncher."""

    def test_load_config_default_when_missing(self, tmp_path: Path) -> None:
        """Проверка загрузки настроек по умолчанию при отсутствии конфигурационного файла."""
        non_existent_path = tmp_path / "missing.json"
        launcher = WebLauncher(config_path=non_existent_path)

        assert "ютуб" in launcher.web_aliases
        assert "google" in launcher.search_engines

    def test_load_config_custom_file(self, tmp_path: Path) -> None:
        """Проверка корректной загрузки настроек из кастомного JSON файла."""
        custom_config = tmp_path / "custom_commands.json"
        custom_config.write_text(
            '{"web_aliases": {"тест": "https://test.local"}, "search_engines": {"custom": "https://s.local?q={query}"}}',
            encoding="utf-8",
        )
        launcher = WebLauncher(config_path=custom_config)

        assert launcher.resolve_alias("тест") == "https://test.local"
        assert launcher.search_engines.get("custom") == "https://s.local?q={query}"

    def test_open_url_with_startfile(self) -> None:
        """Проверка успешного открытия URL через os.startfile."""
        launcher = WebLauncher()
        with patch("os.startfile", create=True) as mock_startfile:
            result = launcher.open_url("https://example.com")
            assert result is True
            mock_startfile.assert_called_once_with("https://example.com")

    def test_open_url_adds_scheme_if_missing(self) -> None:
        """Проверка автоматического добавления https:// к URL без схемы."""
        launcher = WebLauncher()
        with patch("os.startfile", create=True) as mock_startfile:
            result = launcher.open_url("example.com")
            assert result is True
            mock_startfile.assert_called_once_with("https://example.com")

    def test_open_url_empty_returns_false(self) -> None:
        """Проверка возврата False при пустом URL."""
        launcher = WebLauncher()
        assert launcher.open_url("   ") is False

    def test_open_url_fallback_to_webbrowser(self) -> None:
        """Проверка отката на webbrowser.open при ошибке os.startfile."""
        launcher = WebLauncher()
        with (
            patch("os.startfile", side_effect=OSError("Startfile failed"), create=True),
            patch("webbrowser.open", return_value=True) as mock_webbrowser,
        ):
            result = launcher.open_url("https://example.com")
            assert result is True
            mock_webbrowser.assert_called_once_with("https://example.com")

    def test_resolve_alias_found_and_not_found(self) -> None:
        """Проверка поиска зарегистрированных веб-алиасов и обработки неизвестных."""
        launcher = WebLauncher()
        assert launcher.resolve_alias("ютуб") == "https://www.youtube.com"
        assert launcher.resolve_alias("  YOUTUBE  ") == "https://www.youtube.com"
        assert launcher.resolve_alias("unknown_site_alias") is None

    def test_open_alias_success_and_failure(self) -> None:
        """Проверка вызова open_alias для существующего и несуществующего алиаса."""
        launcher = WebLauncher()
        with patch.object(launcher, "open_url", return_value=True) as mock_open:
            assert launcher.open_alias("гугл") is True
            mock_open.assert_called_once_with("https://www.google.com")

        assert launcher.open_alias("non_existent_site") is False

    def test_search_google(self) -> None:
        """Проверка форматирования поискового запроса Google с URL-кодированием."""
        launcher = WebLauncher()
        with patch.object(launcher, "open_url", return_value=True) as mock_open:
            result = launcher.search("привет мир", engine="google")
            assert result is True
            mock_open.assert_called_once()
            called_url = mock_open.call_args[0][0]
            assert "google.com/search?q=" in called_url
            assert "%D0%BF%D1%80%D0%B8%D0%B2%D0%B5%D1%82" in called_url

    def test_search_empty_query(self) -> None:
        """Проверка отмены поиска при пустом запросе."""
        launcher = WebLauncher()
        assert launcher.search("   ") is False


class TestShellExecutor:
    """Набор тестов для компонента ShellExecutor."""

    def test_run_command_success(self) -> None:
        """Проверка успешного выполнения безопасной команды."""
        executor = ShellExecutor()
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "Hello World\n"
        fake_result.stderr = ""

        with patch("subprocess.run", return_value=fake_result) as mock_run:
            code, stdout, stderr = executor.run_command("Write-Output 'Hello World'")
            assert code == 0
            assert "Hello World" in stdout
            assert stderr == ""
            mock_run.assert_called_once()

    def test_run_command_sets_utf8_encoding_and_checks_raw_command(self) -> None:
        """PowerShell запускается с UTF-8 префиксом, а проверка идет до обертки."""
        executor = ShellExecutor()
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "Привет, мир!\n"
        fake_result.stderr = ""

        with patch("subprocess.run", return_value=fake_result) as mock_run:
            code, stdout, _ = executor.run_command("Write-Output 'Привет'")
            assert code == 0
            assert stdout == "Привет, мир!\n"
            args, kwargs = mock_run.call_args
            ps_cmd = args[0][-1]
            assert "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;" in ps_cmd
            assert "$OutputEncoding = [System.Text.Encoding]::UTF8;" in ps_cmd
            assert ps_cmd.endswith("Write-Output 'Привет'")
            assert kwargs["encoding"] == "utf-8"
            assert kwargs["errors"] == "replace"

        with patch.object(
            executor, "is_safe_command", wraps=executor.is_safe_command
        ) as spy_safety:
            with pytest.raises(DangerousCommandError):
                executor.run_command("diskpart")
            spy_safety.assert_called_once_with("diskpart")

    def test_run_command_timeout(self) -> None:
        """Проверка обработки превышения времени ожидания команды."""
        executor = ShellExecutor()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 5)):
            code, stdout, stderr = executor.run_command("Start-Sleep -Seconds 10", timeout=5)
            assert code == -1
            assert stdout == ""
            assert "Превышено время ожидания" in stderr

    def test_run_command_general_exception(self) -> None:
        """Проверка перехвата непредвиденного исключения при запуске команды."""
        executor = ShellExecutor()
        with patch("subprocess.run", side_effect=RuntimeError("Subprocess failed")):
            code, stdout, stderr = executor.run_command("Get-Date")
            assert code == -1
            assert "Subprocess failed" in stderr

    @pytest.mark.parametrize(
        "dangerous_cmd",
        [
            "Format-Volume -DriveLetter D",
            "format c:",
            "del /f /s /q C:\\",
            "rmdir /s /q C:\\Windows",
            "rm -rf /",
            "rm -rf C:\\",
            "Remove-Item -Recurse -Force C:\\test",
            "stop-computer",
            "restart-computer",
            "diskpart",
        ],
    )
    def test_safety_check_blocks_dangerous_patterns(self, dangerous_cmd: str) -> None:
        """Проверка блокировки опасных системных команд политикой безопасности."""
        executor = ShellExecutor()
        with pytest.raises(DangerousCommandError):
            executor.run_command(dangerous_cmd)

    def test_open_terminal_launches_windows_terminal(self) -> None:
        """Проверка запуска Windows Terminal при наличии wt.exe в системе."""
        executor = ShellExecutor()
        with (
            patch("shutil.which", return_value=r"C:\Windows\System32\wt.exe"),
            patch("subprocess.Popen") as mock_popen,
        ):
            executor.open_terminal(interactive=True)
            mock_popen.assert_called_once_with([r"C:\Windows\System32\wt.exe"])

    def test_open_terminal_fallback_powershell(self) -> None:
        """Проверка отката на powershell.exe при отсутствии wt.exe."""
        executor = ShellExecutor()
        with (
            patch("shutil.which", return_value=None),
            patch("subprocess.Popen") as mock_popen,
        ):
            executor.open_terminal(interactive=True)
            mock_popen.assert_called_once_with(["powershell.exe", "-NoExit"])

            mock_popen.reset_mock()
            executor.open_terminal(interactive=False)
            mock_popen.assert_called_once_with(["powershell.exe"])


class TestWindowManager:
    """Набор тестов для компонента WindowManager."""

    def test_list_windows(self) -> None:
        """Проверка получения списка видимых окон с непустыми заголовками."""
        manager = WindowManager()

        def fake_enum_windows(callback: Any, extra: list[tuple[int, str]]) -> None:
            callback(101, extra)
            callback(102, extra)
            callback(103, extra)

        def fake_is_visible(hwnd: int) -> bool:
            return hwnd in (101, 102)

        def fake_get_text(hwnd: int) -> str:
            titles = {101: "Блокнот", 102: "  ", 103: "Калькулятор"}
            return titles.get(hwnd, "")

        with (
            patch("win32gui.EnumWindows", side_effect=fake_enum_windows),
            patch("win32gui.IsWindowVisible", side_effect=fake_is_visible),
            patch("win32gui.GetWindowText", side_effect=fake_get_text),
        ):
            windows = manager.list_windows()
            assert windows == [(101, "Блокнот")]

    def test_focus_window_success(self) -> None:
        """Проверка фокусировки на найденном окне с восстановлением из свернутого состояния."""
        manager = WindowManager()
        with (
            patch.object(manager, "list_windows", return_value=[(1001, "Telegram Desktop")]),
            patch("win32gui.IsIconic", return_value=True) as mock_iconic,
            patch("win32gui.ShowWindow") as mock_show,
            patch("win32gui.SetForegroundWindow") as mock_set_foreground,
        ):
            result = manager.focus_window("telegram")
            assert result is True
            mock_iconic.assert_called_once_with(1001)
            mock_show.assert_called_once_with(1001, win32con.SW_RESTORE)
            mock_set_foreground.assert_called_once_with(1001)

    def test_focus_window_fallback_on_setforeground_error(self) -> None:
        """Проверка отката на ShowWindow при ошибке SetForegroundWindow."""
        manager = WindowManager()
        with (
            patch.object(manager, "list_windows", return_value=[(1002, "Google Chrome")]),
            patch("win32gui.IsIconic", return_value=False),
            patch("win32gui.SetForegroundWindow", side_effect=RuntimeError("Focus error")),
            patch("win32gui.ShowWindow") as mock_show,
        ):
            result = manager.focus_window("chrome")
            assert result is True
            mock_show.assert_called_once_with(1002, win32con.SW_SHOW)

    def test_focus_window_not_found(self) -> None:
        """Проверка возврата False, если окно не найдено."""
        manager = WindowManager()
        with patch.object(manager, "list_windows", return_value=[]):
            assert manager.focus_window("non_existent_window") is False

    def test_minimize_window(self) -> None:
        """Проверка сворачивания целевого окна."""
        manager = WindowManager()
        with (
            patch.object(manager, "list_windows", return_value=[(2001, "Visual Studio Code")]),
            patch("win32gui.ShowWindow") as mock_show,
        ):
            assert manager.minimize_window("code") is True
            mock_show.assert_called_once_with(2001, win32con.SW_MINIMIZE)

        with patch.object(manager, "list_windows", return_value=[]):
            assert manager.minimize_window("code") is False

    def test_maximize_window(self) -> None:
        """Проверка разворачивания окна на весь экран."""
        manager = WindowManager()
        with (
            patch.object(manager, "list_windows", return_value=[(3001, "Notepad")]),
            patch("win32gui.ShowWindow") as mock_show,
        ):
            assert manager.maximize_window("notepad") is True
            mock_show.assert_called_once_with(3001, win32con.SW_MAXIMIZE)

        with patch.object(manager, "list_windows", return_value=[]):
            assert manager.maximize_window("notepad") is False

    def test_close_window(self) -> None:
        """Проверка отправки сообщения WM_CLOSE окну."""
        manager = WindowManager()
        with (
            patch.object(manager, "list_windows", return_value=[(4001, "Calculator")]),
            patch("win32gui.PostMessage") as mock_post,
        ):
            assert manager.close_window("calculator") is True
            mock_post.assert_called_once_with(4001, win32con.WM_CLOSE, 0, 0)

        with patch.object(manager, "list_windows", return_value=[]):
            assert manager.close_window("calculator") is False

    def test_volume_controls(self) -> None:
        """Проверка вызова клавиатурных событий управления громкостью."""
        manager = WindowManager()
        with patch("win32api.keybd_event") as mock_keybd:
            manager.volume_up()
            assert mock_keybd.call_count == 2
            mock_keybd.assert_any_call(win32con.VK_VOLUME_UP, 0, 0, 0)
            mock_keybd.assert_any_call(win32con.VK_VOLUME_UP, 0, win32con.KEYEVENTF_KEYUP, 0)

        with patch("win32api.keybd_event") as mock_keybd:
            manager.volume_down()
            assert mock_keybd.call_count == 2
            mock_keybd.assert_any_call(win32con.VK_VOLUME_DOWN, 0, 0, 0)
            mock_keybd.assert_any_call(win32con.VK_VOLUME_DOWN, 0, win32con.KEYEVENTF_KEYUP, 0)

        with patch("win32api.keybd_event") as mock_keybd:
            manager.volume_mute()
            assert mock_keybd.call_count == 2
            mock_keybd.assert_any_call(win32con.VK_VOLUME_MUTE, 0, 0, 0)
            mock_keybd.assert_any_call(win32con.VK_VOLUME_MUTE, 0, win32con.KEYEVENTF_KEYUP, 0)


class TestOSControllerFacade:
    """Набор тестов для единого фасада OSController."""

    def test_facade_delegates_to_subcomponents(self) -> None:
        """Проверка корректной делегации всех методов фасада компонентам."""
        controller = OSController()

        with patch.object(controller.web, "open_url", return_value=True) as mock_web_open:
            assert controller.open_url("https://example.com") is True
            mock_web_open.assert_called_once_with("https://example.com")

        with patch.object(controller.web, "search", return_value=True) as mock_web_search:
            assert controller.search("запрос", engine="google") is True
            mock_web_search.assert_called_once_with(query="запрос", engine="google")

        with patch.object(
            controller.web, "resolve_alias", return_value="https://ya.ru"
        ) as mock_alias:
            assert controller.resolve_alias("яндекс") == "https://ya.ru"
            mock_alias.assert_called_once_with("яндекс")

        with patch.object(controller.web, "open_alias", return_value=True) as mock_open_alias:
            assert controller.open_alias("яндекс") is True
            mock_open_alias.assert_called_once_with("яндекс")

        with patch.object(
            controller.shell, "run_command", return_value=(0, "ok", "")
        ) as mock_run_cmd:
            assert controller.run_command("dir", timeout=10) == (0, "ok", "")
            mock_run_cmd.assert_called_once_with(command="dir", timeout=10)

        with patch.object(controller.shell, "open_terminal") as mock_terminal:
            controller.open_terminal(interactive=True)
            mock_terminal.assert_called_once_with(interactive=True)

        with patch.object(
            controller.window, "list_windows", return_value=[(1, "Win")]
        ) as mock_list:
            assert controller.list_windows() == [(1, "Win")]
            mock_list.assert_called_once()

        with patch.object(controller.window, "focus_window", return_value=True) as mock_focus:
            assert controller.focus_window("Win") is True
            mock_focus.assert_called_once_with("Win")

        with patch.object(controller.window, "minimize_window", return_value=True) as mock_min:
            assert controller.minimize_window("Win") is True
            mock_min.assert_called_once_with("Win")

        with patch.object(controller.window, "maximize_window", return_value=True) as mock_max:
            assert controller.maximize_window("Win") is True
            mock_max.assert_called_once_with("Win")

        with patch.object(controller.window, "close_window", return_value=True) as mock_close:
            assert controller.close_window("Win") is True
            mock_close.assert_called_once_with("Win")

        with patch.object(controller.window, "volume_up") as mock_vol_up:
            controller.volume_up()
            mock_vol_up.assert_called_once()

        with patch.object(controller.window, "volume_down") as mock_vol_down:
            controller.volume_down()
            mock_vol_down.assert_called_once()

        with patch.object(controller.window, "volume_mute") as mock_vol_mute:
            controller.volume_mute()
            mock_vol_mute.assert_called_once()
