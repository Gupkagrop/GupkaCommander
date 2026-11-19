"""
> [!NOTE]
> Модуль тестов tests.test_create_shortcut: Проверка утилиты создания ярлыков
> Windows (.lnk), выбор интерпретатора (pythonw/uv), работа через COM и PowerShell fallback.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.create_shortcut import (
    ShortcutConfig,
    create_shortcut_file,
    get_desktop_dir,
    get_startup_dir,
    resolve_execution_target,
    setup_shortcuts,
)


class TestCreateShortcut:
    """Тестирование функционала создания ярлыков Windows."""

    def test_get_desktop_and_startup_dirs(self) -> None:
        """Проверка корректного определения системных путей Рабочего стола и Автозагрузки."""
        desktop = get_desktop_dir()
        startup = get_startup_dir()

        assert isinstance(desktop, Path)
        assert isinstance(startup, Path)
        assert "Desktop" in str(desktop)
        assert "Startup" in str(startup)

    def test_resolve_execution_target_pythonw(self, tmp_path: Path) -> None:
        """Проверка выбора pythonw.exe при наличии виртуального окружения."""
        venv_scripts = tmp_path / ".venv" / "Scripts"
        venv_scripts.mkdir(parents=True)
        dummy_pythonw = venv_scripts / "pythonw.exe"
        dummy_pythonw.write_text("", encoding="utf-8")

        target, args = resolve_execution_target(tmp_path, mode="pythonw")
        assert target == str(dummy_pythonw)
        assert "main.py" in args

    def test_resolve_execution_target_uv(self, tmp_path: Path) -> None:
        """Проверка выбора uv при явном указании режима uv."""
        with patch("shutil.which", return_value="C:\\Tools\\uv.exe"):
            target, args = resolve_execution_target(tmp_path, mode="uv")
            assert "uv" in target.lower()
            assert "run python" in args

    def test_create_shortcut_com_success(self, tmp_path: Path) -> None:
        """Проверка создания ярлыка через COM объект WScript.Shell."""
        target_lnk = tmp_path / "TestApp.lnk"
        cfg = ShortcutConfig(
            target_path="C:\\Windows\\notepad.exe",
            arguments="",
            working_dir=str(tmp_path),
            icon_location="C:\\Windows\\notepad.exe,0",
            description="Тестовый ярлык",
        )

        mock_shell = MagicMock()
        mock_shortcut = MagicMock()
        mock_shell.CreateShortcut.return_value = mock_shortcut

        with patch("win32com.client.Dispatch", return_value=mock_shell):
            result = create_shortcut_file(target_lnk, cfg, location_label="Test")

            assert result.success is True
            assert result.method == "win32com"
            mock_shell.CreateShortcut.assert_called_once_with(str(target_lnk))
            assert mock_shortcut.TargetPath == cfg.target_path
            mock_shortcut.Save.assert_called_once()

    def test_create_shortcut_fallback_to_powershell(self, tmp_path: Path) -> None:
        """Проверка автоматического отката на PowerShell при ошибке win32com."""
        target_lnk = tmp_path / "TestAppFallback.lnk"
        cfg = ShortcutConfig(
            target_path="C:\\Windows\\notepad.exe",
            arguments="",
            working_dir=str(tmp_path),
            icon_location="",
            description="Fallback тест",
        )

        with (
            patch("win32com.client.Dispatch", side_effect=RuntimeError("COM недоступен")),
            patch("subprocess.run") as mock_subproc,
        ):
            result = create_shortcut_file(target_lnk, cfg, location_label="TestFallback")

            assert result.success is True
            assert result.method == "powershell"
            mock_subproc.assert_called_once()
            cmd_arg = mock_subproc.call_args[0][0]
            assert cmd_arg[0] == "powershell"
            assert "CreateShortcut" in cmd_arg[4]

    def test_setup_shortcuts_batch(self, tmp_path: Path) -> None:
        """Проверка массового запуска создания ярлыков для рабочего стола и автозагрузки."""
        dummy_ico = tmp_path / "icon.ico"
        dummy_ico.write_text("icon", encoding="utf-8")

        with (
            patch("scripts.create_shortcut.get_desktop_dir", return_value=tmp_path / "Desktop"),
            patch("scripts.create_shortcut.get_startup_dir", return_value=tmp_path / "Startup"),
            patch("scripts.create_shortcut.create_shortcut_com"),
        ):
            results = setup_shortcuts(
                desktop=True,
                startup=True,
                target_mode="auto",
                shortcut_name="TestGupka",
                custom_icon=dummy_ico,
            )

            assert len(results) == 2
            assert results[0].location_name == "Desktop"
            assert results[1].location_name == "Startup"
            assert results[0].file_path.name == "TestGupka.lnk"
            assert results[1].file_path.name == "TestGupka.lnk"
