"""
> [!NOTE]
> Тесты tests.test_launch_scripts: Проверка корректности скриптов запуска
> корневого каталога проекта (start.bat, setup.bat, start_quiet.vbs, start.ps1).
"""

from __future__ import annotations

from pathlib import Path


def get_project_root() -> Path:
    """Возвращает путь к корневой директории проекта."""
    return Path(__file__).resolve().parent.parent


def test_start_bat_exists_and_valid() -> None:
    """Проверяет существование и синтаксическую структуру start.bat."""
    root = get_project_root()
    start_bat = root / "start.bat"
    assert start_bat.exists(), "start.bat должен присутствовать в корне репозитория"

    content = start_bat.read_text(encoding="utf-8")
    assert "chcp 65001" in content
    assert "main.py" in content
    assert "models\\silero_vad.onnx" in content
    assert "download_models.py" in content
    assert "assets\\gupka_commander.ico" in content
    assert "uv run python" in content or "where uv" in content


def test_setup_bat_exists_and_valid() -> None:
    """Проверяет существование и синтаксическую структуру setup.bat."""
    root = get_project_root()
    setup_bat = root / "setup.bat"
    assert setup_bat.exists(), "setup.bat должен присутствовать в корне репозитория"

    content = setup_bat.read_text(encoding="utf-8")
    assert "uv sync" in content
    assert "generate_icon.py" in content
    assert "download_models.py --voice-only" in content
    assert "create_shortcut.py --desktop" in content


def test_start_quiet_vbs_exists_and_valid() -> None:
    """Проверяет существование и структуру тихой службы start_quiet.vbs."""
    root = get_project_root()
    vbs = root / "start_quiet.vbs"
    assert vbs.exists(), "start_quiet.vbs должен присутствовать в корне репозитория"

    content = vbs.read_text(encoding="utf-8")
    assert "WScript.Shell" in content
    assert "pythonw.exe" in content
    assert "main.py" in content


def test_start_ps1_exists_and_valid() -> None:
    """Проверяет существование и параметры PowerShell скрипта start.ps1."""
    root = get_project_root()
    ps1 = root / "start.ps1"
    assert ps1.exists(), "start.ps1 должен присутствовать в корне репозитория"

    content = ps1.read_text(encoding="utf-8")
    assert "param(" in content
    assert "[switch]$Background" in content
    assert "[switch]$Setup" in content
    assert "main.py" in content
    assert "uv run python" in content
