"""Набор модульных тестов для компонента обнаружения и поиска приложений AppFinder."""

import json
import winreg
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.app_finder import (
    AppFinder,
    AppInfo,
    _clean_executable_path,
    _is_valid_shortcut,
    _read_registry_string,
    _scan_single_app_path_subkey,
    _scan_single_uninstall_subkey,
    _scan_start_menu_directory,
)


def test_app_info_dataclass_and_serialization() -> None:
    """Проверяет создание AppInfo и сериализацию/десериализацию через to_dict/from_dict."""
    app = AppInfo(
        name="Telegram Desktop",
        path=r"C:\Users\test\AppData\Roaming\Telegram\Telegram.exe",
        source="start_menu",
        icon_path=r"C:\Users\test\AppData\Roaming\Telegram\Telegram.exe",
    )
    serialized = app.to_dict()
    assert serialized["name"] == "Telegram Desktop"
    assert serialized["source"] == "start_menu"

    restored = AppInfo.from_dict(serialized)
    assert restored.name == app.name
    assert restored.path == app.path
    assert restored.source == app.source
    assert restored.icon_path == app.icon_path


def test_clean_executable_path() -> None:
    """Проверяет корректность очистки путей от кавычек, аргументов и запятых."""
    assert (
        _clean_executable_path(r'"C:\Program Files\App\app.exe" /allusers')
        == r"C:\Program Files\App\app.exe"
    )
    assert (
        _clean_executable_path(r"C:\Program Files\App\app.exe,0") == r"C:\Program Files\App\app.exe"
    )
    assert (
        _clean_executable_path(r'"C:\Program Files\App\app.exe"') == r"C:\Program Files\App\app.exe"
    )
    assert (
        _clean_executable_path(r"C:\Program Files\App\app.exe -silent")
        == r"C:\Program Files\App\app.exe"
    )
    assert _clean_executable_path("") == ""


def test_is_valid_shortcut() -> None:
    """Проверяет фильтрацию ярлыков удаления и служебных файлов."""
    assert _is_valid_shortcut(Path("Google Chrome.lnk"))
    assert _is_valid_shortcut(Path("Telegram.lnk"))
    assert not _is_valid_shortcut(Path("Uninstall App.lnk"))
    assert not _is_valid_shortcut(Path("Удалить программу.lnk"))
    assert not _is_valid_shortcut(Path("Readme.lnk"))


def test_read_registry_string_success() -> None:
    """Проверяет успешное чтение строкового значения из реестра."""
    mock_key = MagicMock()
    with patch("winreg.QueryValueEx", return_value=("Test Value", winreg.REG_SZ)):
        result = _read_registry_string(mock_key, "DisplayName")
        assert result == "Test Value"


def test_read_registry_string_error() -> None:
    """Проверяет обработку отсутствующего значения или ошибки в реестре."""
    mock_key = MagicMock()
    with patch("winreg.QueryValueEx", side_effect=OSError("Key not found")):
        result = _read_registry_string(mock_key, "NonExistent")
        assert result is None


def test_scan_single_uninstall_subkey_valid() -> None:
    """Проверяет извлечение валидного приложения из подраздела Uninstall."""
    mock_root = MagicMock()

    def mock_query(key: MagicMock, val_name: str) -> tuple[str | int, int]:
        if val_name == "DisplayName":
            return "Test App", winreg.REG_SZ
        if val_name == "DisplayIcon":
            return r"C:\TestApp\test.exe,0", winreg.REG_SZ
        raise OSError("Value not found")

    with (
        patch("winreg.OpenKey", return_value=MagicMock()),
        patch("winreg.QueryValueEx", side_effect=mock_query),
    ):
        app = _scan_single_uninstall_subkey(mock_root, "TestAppKey")
        assert app is not None
        assert app.name == "Test App"
        assert app.path == r"C:\TestApp\test.exe"
        assert app.source == "registry_uninstall"


def test_scan_single_uninstall_subkey_skips_system_component() -> None:
    """Проверяет игнорирование системных компонентов Windows."""
    mock_root = MagicMock()

    def mock_query(key: MagicMock, val_name: str) -> tuple[str | int, int]:
        if val_name == "DisplayName":
            return "Windows Driver Update", winreg.REG_SZ
        if val_name == "SystemComponent":
            return 1, winreg.REG_DWORD
        raise OSError("Value not found")

    with (
        patch("winreg.OpenKey", return_value=MagicMock()),
        patch("winreg.QueryValueEx", side_effect=mock_query),
    ):
        app = _scan_single_uninstall_subkey(mock_root, "SystemUpdateKey")
        assert app is None


def test_scan_single_app_path_subkey() -> None:
    """Проверяет парсинг подраздела App Paths."""
    mock_root = MagicMock()

    def mock_query(key: MagicMock, val_name: str) -> tuple[str, int]:
        if val_name == "":
            return r"C:\Program Files\Custom\custom.exe", winreg.REG_SZ
        raise OSError("Not found")

    with (
        patch("winreg.OpenKey", return_value=MagicMock()),
        patch("winreg.QueryValueEx", side_effect=mock_query),
    ):
        app = _scan_single_app_path_subkey(mock_root, "custom.exe")
        assert app is not None
        assert app.name == "custom"
        assert app.path == r"C:\Program Files\Custom\custom.exe"
        assert app.source == "registry_app_paths"


def test_scan_start_menu_directory(tmp_path: Path) -> None:
    """Проверяет рекурсивное обнаружение ярлыков .lnk в папке меню Пуск."""
    menu_dir = tmp_path / "StartMenu"
    sub_dir = menu_dir / "SubFolder"
    sub_dir.mkdir(parents=True)

    good_lnk = menu_dir / "GoodApp.lnk"
    good_lnk.write_text("dummy")

    nested_lnk = sub_dir / "NestedApp.lnk"
    nested_lnk.write_text("dummy")

    uninstall_lnk = menu_dir / "Uninstall GoodApp.lnk"
    uninstall_lnk.write_text("dummy")

    results = _scan_start_menu_directory(menu_dir)
    found_names = {app.name for app in results}

    assert "GoodApp" in found_names
    assert "NestedApp" in found_names
    assert "Uninstall GoodApp" not in found_names


def test_scan_start_menu_directory_missing() -> None:
    """Проверяет корректный возврат пустого списка при отсутствии папки."""
    non_existent = Path(r"C:\NonExistent\Path\To\Directory")
    assert _scan_start_menu_directory(non_existent) == []


def test_cache_save_and_load(tmp_path: Path) -> None:
    """Проверяет сохранение и загрузку кэша приложений."""
    cache_file = tmp_path / "app_cache.json"
    commands_file = tmp_path / "commands.json"

    finder = AppFinder(cache_path=cache_file, commands_path=commands_file)
    test_apps = [
        AppInfo(name="App One", path="C:/app1.exe", source="test"),
        AppInfo(name="App Two", path="C:/app2.exe", source="test"),
    ]
    finder._apps = test_apps
    finder.save_cache()

    assert cache_file.exists()

    new_finder = AppFinder(cache_path=cache_file, commands_path=commands_file)
    loaded = new_finder.load_cache()
    assert len(loaded) == 2
    assert loaded[0].name == "App One"
    assert loaded[1].path == "C:/app2.exe"


def test_load_cache_missing_or_corrupt(tmp_path: Path) -> None:
    """Проверяет поведение load_cache при отсутствии или повреждении файла."""
    cache_file = tmp_path / "corrupt_cache.json"
    commands_file = tmp_path / "commands.json"

    finder = AppFinder(cache_path=cache_file, commands_path=commands_file)
    assert finder.load_cache() == []

    cache_file.write_text("{ not valid json }", encoding="utf-8")
    assert finder.load_cache() == []


def test_load_aliases_and_resolve(tmp_path: Path) -> None:
    """Проверяет загрузку и разрешение алиасов из commands.json."""
    commands_file = tmp_path / "commands.json"
    commands_file.write_text(
        json.dumps(
            {
                "app_aliases": {
                    "телега": "telegram",
                    "хром": "chrome",
                    "блокнот": "notepad",
                }
            }
        ),
        encoding="utf-8",
    )

    finder = AppFinder(cache_path=tmp_path / "cache.json", commands_path=commands_file)
    assert finder.resolve_alias("телега") == "telegram"
    assert finder.resolve_alias("хром") == "chrome"
    assert finder.resolve_alias("неизвестное") == "неизвестное"


def test_scan_caching_behavior(tmp_path: Path) -> None:
    """Проверяет, что повторный вызов scan(force=False) использует кэш без повторного поиска."""
    cache_file = tmp_path / "app_cache.json"
    commands_file = tmp_path / "commands.json"

    finder = AppFinder(cache_path=cache_file, commands_path=commands_file)
    preset_apps = [AppInfo(name="CachedApp", path="C:/cached.exe", source="cache")]
    finder._apps = preset_apps
    finder.save_cache()

    with (
        patch.object(finder, "scan_start_menu") as mock_menu,
        patch.object(finder, "scan_registry") as mock_reg,
    ):
        result = finder.scan(force=False)
        assert len(result) == 1
        assert result[0].name == "CachedApp"
        mock_menu.assert_not_called()
        mock_reg.assert_not_called()


def test_scan_force_refresh(tmp_path: Path) -> None:
    """Проверяет, что scan(force=True) перезапускает сканирование и перезаписывает кэш."""
    cache_file = tmp_path / "app_cache.json"
    commands_file = tmp_path / "commands.json"

    finder = AppFinder(cache_path=cache_file, commands_path=commands_file)
    fresh_apps = [AppInfo(name="FreshApp", path="C:/fresh.exe", source="start_menu")]

    with (
        patch.object(finder, "scan_start_menu", return_value=fresh_apps),
        patch.object(finder, "scan_registry", return_value=[]),
    ):
        result = finder.scan(force=True)
        assert len(result) == 1
        assert result[0].name == "FreshApp"
        assert cache_file.exists()


def test_fuzzy_search_find(tmp_path: Path) -> None:
    """Проверяет нечеткий поиск приложений по имени и алиасу."""
    commands_file = tmp_path / "commands.json"
    commands_file.write_text(
        json.dumps(
            {
                "app_aliases": {
                    "телега": "telegram",
                    "хром": "chrome",
                    "настройки": "ms-settings:",
                }
            }
        ),
        encoding="utf-8",
    )

    finder = AppFinder(cache_path=tmp_path / "cache.json", commands_path=commands_file)
    finder._apps = [
        AppInfo(
            name="Google Chrome",
            path="C:/chrome.exe",
            source="registry_app_paths",
        ),
        AppInfo(
            name="Telegram Desktop",
            path="C:/telegram.exe",
            source="start_menu",
        ),
        AppInfo(
            name="Visual Studio Code",
            path="C:/code.exe",
            source="registry_uninstall",
        ),
    ]

    # Прямой поиск
    chrome_res = finder.find("chrome")
    assert len(chrome_res) > 0
    assert chrome_res[0].name == "Google Chrome"

    # Поиск через алиас
    alias_res = finder.find("хром")
    assert len(alias_res) > 0
    assert alias_res[0].name == "Google Chrome"

    alias_telega = finder.find("телега")
    assert len(alias_telega) > 0
    assert alias_telega[0].name == "Telegram Desktop"

    # Поиск несуществующего приложения
    missing_res = finder.find("unknown_app_xyz_123")
    assert len(missing_res) == 0

    # Поиск системного алиаса без приложения в кэше
    settings_res = finder.find("настройки")
    assert len(settings_res) == 1
    assert settings_res[0].path == "ms-settings:"
    assert settings_res[0].source == "alias"


def test_find_empty_query(tmp_path: Path) -> None:
    """Проверяет обработку пустого запроса."""
    finder = AppFinder(cache_path=tmp_path / "cache.json")
    assert finder.find("") == []
    assert finder.find("   ") == []


def test_live_windows_scan_smoke() -> None:
    """Дымовой тест реального сканирования на текущей машине Windows."""
    finder = AppFinder()
    apps = finder.scan(force=True)
    assert len(apps) > 0
    assert any(
        app.source in ("start_menu", "registry_uninstall", "registry_app_paths") for app in apps
    )
