"""
Тесты для валидации спецификаций упаковки GupkaCommander.

Проверяют:
1. Синтаксическую и структурную валидность GupkaCommander.spec.
2. Наличие всех критических hiddenimports, манифеста и параметров сборки (console=False, icon).
3. Структуру и настройки двухрежимного установщика Inno Setup (scripts/installer.iss).
4. Корректность скриптов сборки scripts/build_executable.py, scripts/build_installer.py и build.bat.
"""

import ast
import json
import zipfile
from pathlib import Path

from gui.settings_dialog import _default_settings
from scripts.build_executable import PORTABLE_ZIP_NAME, create_portable_zip, get_dir_size_mb
from scripts.build_installer import find_iscc


def test_spec_file_syntax_and_structure() -> None:
    """Проверяет, что GupkaCommander.spec существует и является валидным кодом Python."""
    project_root = Path(__file__).resolve().parent.parent
    spec_path = project_root / "GupkaCommander.spec"

    assert spec_path.exists(), "GupkaCommander.spec должен присутствовать в корне проекта"

    content = spec_path.read_text(encoding="utf-8")
    # Проверка парсинга AST без ошибок синтаксиса
    parsed = ast.parse(content, filename=str(spec_path))
    assert parsed is not None

    # Проверка наличия ключевых скрытых модулей
    assert "onnxruntime" in content
    assert "ctranslate2" in content
    assert "sounddevice" in content
    assert "uiautomation" in content
    assert "core.paths" in content
    assert "pynput.keyboard._win32" in content
    assert "pynput.mouse._win32" in content
    assert "win32clipboard" in content

    # Проверка параметров сборщика
    assert "console=False" in content
    assert "gupka_commander.ico" in content


def test_installer_iss_structure() -> None:
    """Проверяет корректность секций и конфигурации Inno Setup в scripts/installer.iss."""
    project_root = Path(__file__).resolve().parent.parent
    iss_path = project_root / "scripts" / "installer.iss"

    assert iss_path.exists(), "scripts/installer.iss должен присутствовать"

    content = iss_path.read_text(encoding="utf-8")

    # Проверка обязательных секций Inno Setup
    assert "[Setup]" in content
    assert "[Languages]" in content
    assert "[Tasks]" in content
    assert "[Files]" in content
    assert "[Icons]" in content
    assert "[Run]" in content

    # Проверка двухрежимной установки (User-mode по умолчанию + Dialog для Admin-mode)
    assert "PrivilegesRequired=lowest" in content
    assert "PrivilegesRequiredOverridesAllowed=dialog" in content
    assert "{autopf}" in content

    # Проверка ярлыков и автозапуска
    assert "{autodesktop}" in content
    assert "{userstartup}" in content
    assert "--quiet" in content
    assert "gupka_commander.ico" in content


def test_build_scripts_and_helpers(tmp_path: Path) -> None:
    """Проверяет вспомогательные функции утилит сборки."""
    # Тест вычисления размера директории
    test_file = tmp_path / "sample.bin"
    test_file.write_bytes(b"\x00" * (1024 * 1024))  # 1 MB
    size_mb = get_dir_size_mb(tmp_path)
    assert 0.99 <= size_mb <= 1.01

    # Тест поиска ISCC (возвращает Path или None без исключений)
    iscc_result = find_iscc()
    assert iscc_result is None or isinstance(iscc_result, Path)


def test_build_bat_exists() -> None:
    """Проверяет наличие корневого 1-click скрипта build.bat."""
    project_root = Path(__file__).resolve().parent.parent
    build_bat = project_root / "build.bat"

    assert build_bat.exists()
    content = build_bat.read_text(encoding="utf-8")
    assert "scripts/build_executable.py" in content or "scripts\\build_executable.py" in content
    assert "scripts/build_installer.py" in content or "scripts\\build_installer.py" in content


def test_v2_packaging_versions_settings_and_portable_zip(tmp_path: Path) -> None:
    """Проверяет версию v2.0.0, блок ui в settings.json, context_window 8192 и создание ZIP."""
    project_root = Path(__file__).resolve().parent.parent

    iss_text = (project_root / "scripts" / "installer.iss").read_text(encoding="utf-8")
    assert '#define MyAppVersion "2.0.0"' in iss_text

    installer_py = (project_root / "scripts" / "build_installer.py").read_text(encoding="utf-8")
    assert "GupkaCommander-Setup-v2.0.0.exe" in installer_py

    settings_data = json.loads(
        (project_root / "config" / "settings.json").read_text(encoding="utf-8")
    )
    assert settings_data["ui"] == {
        "dock_position": "top",
        "target_monitor": "auto",
        "theme": "system",
        "backdrop": "acrylic",
    }
    assert _default_settings()["models"]["llm"]["context_window"] == 8192

    fake_dist = tmp_path / "GupkaCommander"
    fake_dist.mkdir()
    (fake_dist / "GupkaCommander.exe").write_bytes(b"MZ")
    (fake_dist / "_internal").mkdir()
    (fake_dist / "_internal" / "data.txt").write_text("ok", encoding="utf-8")

    zip_path = create_portable_zip(fake_dist)
    assert zip_path.name == PORTABLE_ZIP_NAME
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = set(zf.namelist())
        assert "GupkaCommander/GupkaCommander.exe" in names
        assert "GupkaCommander/_internal/data.txt" in names
