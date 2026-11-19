"""
Тесты для модуля виртуализации путей core/paths.py.

Проверяют корректность разрешения путей ресурсов и пользовательских данных
как в стандартном режиме разработки, так и в режиме заморозки (PyInstaller freeze).
"""

import sys
from pathlib import Path

import pytest

from core import paths


def test_is_frozen_standard_mode() -> None:
    """Проверяет, что в обычном тестовом окружении is_frozen() возвращает False."""
    assert paths.is_frozen() is False


def test_is_frozen_when_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет, что is_frozen() возвращает True при заморозке (sys.frozen и _MEIPASS)."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", "C:\\fake\\meipass", raising=False)
    assert paths.is_frozen() is True


def test_get_bundle_root_dev_mode() -> None:
    """Проверяет, что в режиме разработки bundle root указывает на корень проекта."""
    bundle_root = paths.get_bundle_root()
    assert bundle_root.exists()
    assert (bundle_root / "pyproject.toml").exists()
    assert (bundle_root / "core").exists()


def test_get_bundle_root_frozen_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет, что в режиме заморозки bundle root указывает на sys._MEIPASS."""
    fake_meipass = tmp_path / "meipass_extracted"
    fake_meipass.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_meipass), raising=False)

    assert paths.get_bundle_root() == fake_meipass.resolve()


def test_get_data_root_custom_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет, что при установленной GUPKA_DATA_DIR используется именно она."""
    custom_dir = tmp_path / "custom_data"
    monkeypatch.setenv("GUPKA_DATA_DIR", str(custom_dir))

    assert paths.get_data_root() == custom_dir.resolve()
    assert custom_dir.exists()


def test_get_data_root_appdata_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет, что при GUPKA_USE_APPDATA=1 путь строится на базе LOCALAPPDATA."""
    fake_localappdata = tmp_path / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(fake_localappdata))
    monkeypatch.setenv("GUPKA_USE_APPDATA", "1")
    monkeypatch.delenv("GUPKA_DATA_DIR", raising=False)

    data_root = paths.get_data_root()
    expected = fake_localappdata / "GupkaCommander"
    assert data_root == expected
    assert data_root.exists()


def test_get_asset_path_and_icon() -> None:
    """Проверяет получение пути к ассетам и фирменной иконке."""
    icon_path = paths.get_icon_path()
    assert icon_path.name == "gupka_commander.ico"
    assert icon_path.parent.name == "assets"
    assert icon_path.exists()


def test_get_config_path_copies_default_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверяет автоматическое копирование шаблона commands.json в директорию данных."""
    custom_data = tmp_path / "isolated_data"
    monkeypatch.setenv("GUPKA_DATA_DIR", str(custom_data))

    config_path = paths.get_config_path("commands.json")
    assert config_path.exists()
    assert config_path.parent == custom_data / "config"
    assert config_path.stat().st_size > 0


def test_get_database_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет получение пути к базе данных SQLite."""
    custom_data = tmp_path / "isolated_data"
    monkeypatch.setenv("GUPKA_DATA_DIR", str(custom_data))

    db_path = paths.get_database_path("custom_memory.db")
    assert db_path.name == "custom_memory.db"
    assert db_path.parent == custom_data / "config"
    assert db_path.parent.exists()


def test_get_cache_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет получение пути к файлу кэша."""
    custom_data = tmp_path / "isolated_data"
    monkeypatch.setenv("GUPKA_DATA_DIR", str(custom_data))

    cache_path = paths.get_cache_path("app_cache.json")
    assert cache_path.name == "app_cache.json"
    assert cache_path.parent == custom_data / "config"
    assert cache_path.parent.exists()


def test_get_models_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет получение директории моделей."""
    custom_data = tmp_path / "isolated_data"
    monkeypatch.setenv("GUPKA_DATA_DIR", str(custom_data))

    models_dir = paths.get_models_dir()
    assert models_dir.name == "models"
    assert models_dir.exists()
