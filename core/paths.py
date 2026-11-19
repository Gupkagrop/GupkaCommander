"""
Централизованный модуль виртуализации путей для GupkaCommander.

Обеспечивает корректное разделение неизменяемых ресурсов (Bundle / sys._MEIPASS)
и пользовательских изменяемых данных (Local AppData / SQLite / Cache)
как в режиме разработки, так и в упакованном виде (PyInstaller .exe).
"""

import os
import shutil
import sys
from pathlib import Path


def is_frozen() -> bool:
    """
    Проверяет, запущено ли приложение в упакованном виде (PyInstaller freeze).

    Returns:
        True, если приложение скомпилировано в .exe, иначе False.
    """
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def get_bundle_root() -> Path:
    """
    Возвращает корневую директорию неизменяемых ресурсов приложения.

    В упакованном виде — sys._MEIPASS (временная директория распаковки).
    В режиме разработки — корень проекта.

    Returns:
        Абсолютный путь к неизменяемым ресурсам.
    """
    if is_frozen():
        meipass_path: str = getattr(sys, "_MEIPASS")
        return Path(meipass_path).resolve()

    # Корень проекта на один уровень выше core/
    return Path(__file__).resolve().parent.parent


def get_data_root() -> Path:
    """
    Возвращает директорию для пользовательских изменяемых данных (БД, кэш, логи).

    В режиме production/frozen или при GUPKA_USE_APPDATA=1 используется:
    %LOCALAPPDATA%\\GupkaCommander.
    В режиме разработки по умолчанию используется корень проекта для изоляции.

    Returns:
        Абсолютный путь к рабочей директории данных.
    """
    custom_data_dir = os.environ.get("GUPKA_DATA_DIR")
    if custom_data_dir:
        data_path = Path(custom_data_dir).resolve()
        data_path.mkdir(parents=True, exist_ok=True)
        return data_path

    if is_frozen() or os.environ.get("GUPKA_USE_APPDATA") == "1":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base_dir = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        app_dir = base_dir / "GupkaCommander"
        app_dir.mkdir(parents=True, exist_ok=True)
        return app_dir

    return get_bundle_root()


def get_asset_path(filename: str) -> Path:
    """
    Возвращает путь к статическому медиа-ресурсу (иконка, логотип).

    Args:
        filename: Имя файла внутри директории assets.

    Returns:
        Абсолютный путь к файлу ресурса.
    """
    return get_bundle_root() / "assets" / filename


def get_icon_path() -> Path:
    """
    Возвращает путь к фирменной Windows-иконке приложения.

    Returns:
        Абсолютный путь к assets/gupka_commander.ico.
    """
    return get_asset_path("gupka_commander.ico")


def get_config_path(filename: str = "commands.json") -> Path:
    """
    Возвращает путь к конфигурационному файлу в директории данных.

    Если файл отсутствует в директории данных, но присутствует в шаблонах bundle,
    производится автоматическое начальное копирование.

    Args:
        filename: Имя конфигурационного файла.

    Returns:
        Абсолютный путь к файлу конфигурации.
    """
    data_config_dir = get_data_root() / "config"
    data_config_dir.mkdir(parents=True, exist_ok=True)
    target_path = data_config_dir / filename

    if not target_path.exists():
        bundle_source = get_bundle_root() / "config" / filename
        if bundle_source.exists() and bundle_source.resolve() != target_path.resolve():
            shutil.copy2(bundle_source, target_path)

    return target_path


def get_database_path(db_name: str = "memory.db") -> Path:
    """
    Возвращает путь к SQLite базе данных долговременной памяти.

    Args:
        db_name: Имя файла базы данных.

    Returns:
        Абсолютный путь к файлу базы данных в каталоге config.
    """
    config_dir = get_data_root() / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / db_name


def get_cache_path(filename: str = "app_cache.json") -> Path:
    """
    Возвращает путь к файлу кэша приложений.

    Args:
        filename: Имя файла кэша.

    Returns:
        Абсолютный путь к файлу кэша в каталоге config.
    """
    config_dir = get_data_root() / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / filename


def get_models_dir() -> Path:
    """
    Возвращает директорию хранения локальных моделей (ONNX, GGUF).

    Проверяет наличие моделей сначала в DATA_DIR, затем в BUNDLE_DIR.

    Returns:
        Абсолютный путь к директории моделей.
    """
    data_models = get_data_root() / "models"
    bundle_models = get_bundle_root() / "models"

    if bundle_models.exists() and not data_models.exists():
        return bundle_models

    data_models.mkdir(parents=True, exist_ok=True)
    return data_models
