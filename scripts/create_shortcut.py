"""
> [!NOTE]
> Скрипт scripts.create_shortcut: Утилита создания ярлыков Windows (.lnk)
> для запуска GupkaCommander с Рабочего стола и из автозагрузки Windows.
> Поддерживает запуск через pythonw.exe (без консоли) или 'uv run', использует
> WScript.Shell через win32com.client с автоматическим откатом на PowerShell.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final, NamedTuple

logger = logging.getLogger("create_shortcut")

DEFAULT_SHORTCUT_NAME: Final[str] = "GupkaCommander"
DEFAULT_DESCRIPTION: Final[str] = (
    "GupkaCommander — автономный голосовой ассистент и командный центр Windows 11"
)


class ShortcutConfig(NamedTuple):
    """Параметры создаваемого ярлыка Windows."""

    target_path: str
    arguments: str
    working_dir: str
    icon_location: str
    description: str


class ShortcutResult(NamedTuple):
    """Результат операции создания ярлыка."""

    location_name: str
    file_path: Path
    success: bool
    method: str
    error_message: str | None = None


def resolve_project_root() -> Path:
    """Возвращает абсолютный путь к корню репозитория проекта."""
    return Path(__file__).resolve().parent.parent


def get_desktop_dir() -> Path:
    """
    Определяет путь к папке Рабочего стола текущего пользователя.

    Returns:
        Path: Путь к Рабочему столу.
    """
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        desktop = Path(user_profile) / "Desktop"
        if desktop.exists():
            return desktop

    # Запасной вариант через системный путь OneDrive / Desktop
    onedrive = os.environ.get("OneDrive")
    if onedrive:
        od_desktop = Path(onedrive) / "Desktop"
        if od_desktop.exists():
            return od_desktop

    return Path.home() / "Desktop"


def get_startup_dir() -> Path:
    """
    Определяет путь к папке автозагрузки Windows текущего пользователя.

    Returns:
        Path: Путь к папке Startup.
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup_subpath = Path("AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup")
    return Path.home() / startup_subpath


def resolve_execution_target(
    project_root: Path,
    mode: str = "auto",
) -> tuple[str, str]:
    """
    Определяет исполняемый файл и аргументы командной строки для ярлыка.

    Args:
        project_root: Корень проекта.
        mode: Режим запуска ('auto', 'pythonw', 'uv').

    Returns:
        tuple[str, str]: (Путь к бинарнику, аргументы).
    """
    main_py = project_root / "main.py"
    venv_pythonw = project_root / ".venv" / "Scripts" / "pythonw.exe"

    if mode in ("pythonw", "auto") and venv_pythonw.exists():
        return str(venv_pythonw), f'"{main_py}"'

    sys_pythonw = Path(sys.executable).parent / "pythonw.exe"
    if mode in ("pythonw", "auto") and sys_pythonw.exists():
        return str(sys_pythonw), f'"{main_py}"'

    uv_path = shutil.which("uv")
    if (mode in ("uv", "auto")) and uv_path:
        return uv_path, f'run python "{main_py}"'

    # Окончательный fallback на текущий интерпретатор python
    return sys.executable, f'"{main_py}"'


def create_shortcut_com(dest_lnk: Path, config: ShortcutConfig) -> None:
    """
    Создает ярлык .lnk через COM-объект WScript.Shell библиотеки win32com.

    Args:
        dest_lnk: Целевой путь создаваемого .lnk файла.
        config: Параметры конфигурации ярлыка.
    """
    import win32com.client  # type: ignore[import-untyped]

    dest_lnk.parent.mkdir(parents=True, exist_ok=True)
    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortcut(str(dest_lnk))
    shortcut.TargetPath = config.target_path
    shortcut.Arguments = config.arguments
    shortcut.WorkingDirectory = config.working_dir
    shortcut.IconLocation = config.icon_location
    shortcut.Description = config.description
    shortcut.Save()


def create_shortcut_powershell(dest_lnk: Path, config: ShortcutConfig) -> None:
    """
    Создает ярлык .lnk с помощью команды PowerShell (fallback-механизм).

    Args:
        dest_lnk: Целевой путь создаваемого .lnk файла.
        config: Параметры конфигурации ярлыка.
    """
    dest_lnk.parent.mkdir(parents=True, exist_ok=True)

    # Экранирование одинарных кавычек для синтаксиса PowerShell
    def _escape(val: str) -> str:
        return val.replace("'", "''")

    ps_script = (
        f"$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{_escape(str(dest_lnk))}'); "
        f"$s.TargetPath = '{_escape(config.target_path)}'; "
        f"$s.Arguments = '{_escape(config.arguments)}'; "
        f"$s.WorkingDirectory = '{_escape(config.working_dir)}'; "
        f"$s.IconLocation = '{_escape(config.icon_location)}'; "
        f"$s.Description = '{_escape(config.description)}'; "
        f"$s.Save()"
    )

    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def create_shortcut_file(
    dest_lnk: Path,
    config: ShortcutConfig,
    location_label: str = "custom",
) -> ShortcutResult:
    """
    Создает ярлык, пытаясь сначала использовать win32com, затем PowerShell.

    Args:
        dest_lnk: Целевой путь к файлу .lnk.
        config: Конфигурация ярлыка.
        location_label: Название расположения для логирования.

    Returns:
        ShortcutResult: Объект с детальной информацией о создании ярлыка.
    """
    # 1. Попытка создания через win32com.client
    try:
        create_shortcut_com(dest_lnk, config)
        logger.info("Ярлык успешно создан через COM (win32com): %s", dest_lnk)
        return ShortcutResult(location_label, dest_lnk, True, "win32com")
    except Exception as err:
        logger.warning("Ошибка создания ярлыка через COM: %s. Пробуем PowerShell...", err)

    # 2. Резервный механизм через PowerShell
    try:
        create_shortcut_powershell(dest_lnk, config)
        logger.info("Ярлык успешно создан через PowerShell fallback: %s", dest_lnk)
        return ShortcutResult(location_label, dest_lnk, True, "powershell")
    except Exception as ps_err:
        err_msg = f"Ошибка PowerShell: {ps_err}"
        logger.error("Не удалось создать ярлык: %s", err_msg)
        return ShortcutResult(location_label, dest_lnk, False, "failed", err_msg)


def setup_shortcuts(
    desktop: bool = False,
    startup: bool = False,
    target_mode: str = "auto",
    shortcut_name: str = DEFAULT_SHORTCUT_NAME,
    custom_icon: Path | None = None,
) -> list[ShortcutResult]:
    """
    Выполняет создание ярлыков в выбранных расположениях Windows.

    Args:
        desktop: Флаг создания ярлыка на Рабочем столе.
        startup: Флаг создания ярлыка в системной папке автозагрузки.
        target_mode: Режим выбора целевого бинарника ('auto', 'pythonw', 'uv').
        shortcut_name: Базовое имя файла ярлыка без расширения .lnk.
        custom_icon: Пользовательский путь к файлу иконки.

    Returns:
        list[ShortcutResult]: Список результатов для каждого расположения.
    """
    project_root = resolve_project_root()
    target_path, arguments = resolve_execution_target(project_root, mode=target_mode)

    icon_path = custom_icon or (project_root / "assets" / "gupka_commander.ico")
    icon_loc = f"{icon_path.resolve()},0" if icon_path.exists() else ""

    config = ShortcutConfig(
        target_path=target_path,
        arguments=arguments,
        working_dir=str(project_root.resolve()),
        icon_location=icon_loc,
        description=DEFAULT_DESCRIPTION,
    )

    results: list[ShortcutResult] = []

    if desktop:
        desk_path = get_desktop_dir() / f"{shortcut_name}.lnk"
        res_desk = create_shortcut_file(desk_path, config, location_label="Desktop")
        results.append(res_desk)

    if startup:
        start_path = get_startup_dir() / f"{shortcut_name}.lnk"
        res_start = create_shortcut_file(start_path, config, location_label="Startup")
        results.append(res_start)

    return results


def main() -> None:
    """Точка входа командной строки для управления ярлыками."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="Создание ярлыков Windows .lnk для GupkaCommander",
    )
    parser.add_argument(
        "--desktop",
        action="store_true",
        help="Создать ярлык на Рабочем столе Windows",
    )
    parser.add_argument(
        "--startup",
        action="store_true",
        help="Создать ярлык в системной автозагрузке Windows",
    )
    parser.add_argument(
        "--target-mode",
        choices=["auto", "pythonw", "uv"],
        default="auto",
        help="Целевой режим запуска (auto: pythonw при наличии, иначе uv)",
    )
    parser.add_argument(
        "--name",
        type=str,
        default=DEFAULT_SHORTCUT_NAME,
        help="Имя создаваемого ярлыка (по умолчанию: GupkaCommander)",
    )

    args = parser.parse_args()

    # Если ни один флаг не передан, по умолчанию создаем ярлык на Рабочем столе
    desktop_flag = args.desktop
    startup_flag = args.startup
    if not desktop_flag and not startup_flag:
        desktop_flag = True

    results = setup_shortcuts(
        desktop=desktop_flag,
        startup=startup_flag,
        target_mode=args.target_mode,
        shortcut_name=args.name,
    )

    for r in results:
        status_str = "УСПЕХ" if r.success else "ОШИБКА"
        print(f"[{status_str}] {r.location_name}: {r.file_path} (метод: {r.method})")
        if not r.success:
            print(f"  Причина: {r.error_message}")


if __name__ == "__main__":
    main()
