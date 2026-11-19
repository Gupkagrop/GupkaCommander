"""Модуль управления операционной системой Windows 11 (OSController).

Предоставляет детерминированное управление системными функциями:
- WebLauncher: запуск браузера, алиасы сайтов и поисковые запросы.
- ShellExecutor: безопасное выполнение команд PowerShell и запуск терминала.
- WindowManager: управление окнами и системной громкостью через Win32 API.
- OSController: единый фасад быстрого пути (Fast-Path).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

import win32api  # type: ignore[import-untyped]
import win32con  # type: ignore[import-untyped]
import win32gui  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


class DangerousCommandError(PermissionError, ValueError):
    """Исключение, вызываемое при попытке выполнить потенциально опасную команду."""


class WebLauncher:
    """Компонент для запуска веб-страниц, поиска и разрешения веб-алиасов."""

    def __init__(self, config_path: Path | str | None = None) -> None:
        if config_path is not None:
            self.config_path = Path(config_path)
        else:
            from core.paths import get_config_path

            self.config_path = get_config_path("commands.json")
        self.web_aliases: dict[str, str] = {}
        self.search_engines: dict[str, str] = {}
        self.load_config()

    def load_config(self) -> None:
        """Загрузка конфигурации поисковых систем и веб-алиасов."""
        if not self.config_path.exists():
            logger.warning(
                "Конфигурация команд не найдена: %s. Используются значения по умолчанию.",
                self.config_path,
            )
            self._set_default_config()
            return

        try:
            with open(self.config_path, encoding="utf-8") as file:
                data: dict[str, Any] = json.load(file)
            self.web_aliases = {
                str(key).lower(): str(val) for key, val in data.get("web_aliases", {}).items()
            }
            self.search_engines = {
                str(key).lower(): str(val) for key, val in data.get("search_engines", {}).items()
            }
        except Exception as error:
            logger.error("Ошибка при чтении конфигурации команд: %s", error)
            self._set_default_config()

    def _set_default_config(self) -> None:
        """Установка конфигурации по умолчанию при отсутствии или повреждении файла."""
        self.web_aliases = {
            "ютуб": "https://www.youtube.com",
            "youtube": "https://www.youtube.com",
            "гугл": "https://www.google.com",
            "google": "https://www.google.com",
            "яндекс": "https://ya.ru",
            "yandex": "https://ya.ru",
            "гитхаб": "https://github.com",
            "github": "https://github.com",
        }
        self.search_engines = {
            "google": "https://www.google.com/search?q={query}",
            "yandex": "https://ya.ru/search/?text={query}",
            "youtube": "https://www.youtube.com/results?search_query={query}",
        }

    def open_url(self, url: str) -> bool:
        """Открытие URL-адреса в системном браузере по умолчанию."""
        target_url = url.strip()
        if not target_url:
            return False

        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", target_url):
            target_url = f"https://{target_url}"

        if hasattr(os, "startfile"):
            try:
                os.startfile(target_url)
                return True
            except Exception as error:
                logger.debug("os.startfile завершился ошибкой: %s", error)

        try:
            return bool(webbrowser.open(target_url))
        except Exception as error:
            logger.error("Не удалось открыть URL %s: %s", target_url, error)
            return False

    def resolve_alias(self, alias: str) -> str | None:
        """Разрешение веб-алиаса в полноценный URL."""
        cleaned_alias = alias.strip().lower()
        return self.web_aliases.get(cleaned_alias)

    def open_alias(self, alias: str) -> bool:
        """Открытие веб-сайта по зарегистрированному алиасу."""
        resolved_url = self.resolve_alias(alias)
        if not resolved_url:
            return False
        return self.open_url(resolved_url)

    def search(self, query: str, engine: str = "google") -> bool:
        """Выполнение поискового запроса в указанной поисковой системе."""
        cleaned_query = query.strip()
        if not cleaned_query:
            return False

        engine_key = engine.strip().lower()
        template = self.search_engines.get(
            engine_key,
            self.search_engines.get("google", "https://www.google.com/search?q={query}"),
        )
        encoded_query = urllib.parse.quote_plus(cleaned_query)
        target_url = template.format(query=encoded_query)
        return self.open_url(target_url)


class ShellExecutor:
    """Компонент для выполнения команд оболочки и запуска терминала с усиленной защитой."""

    DANGEROUS_CMD_PATTERNS: tuple[str, ...] = (
        r"\bclear-disk\b",
        r"\binitialize-disk\b",
        r"\bformat-volume\b",
        r"\bformat\s+[a-z]:?(?:\s|$)",
        r"\bdiskpart\b",
        r"\bstop-computer\b",
        r"\brestart-computer\b",
        r"\bshutdown\b.*?(?:/|-)[sr]\b",
        r"\bvssadmin\b",
        r"\bbcdedit\b",
        r"\binvoke-expression\b",
        r"\biex\b",
        r"\.downloadstring\(",
        r"\.downloadfile\(",
        r"\breg\s+delete\b",
    )

    ROOT_TARGET_PATTERN: str = (
        r"(?:(?<=[\s\"':=])|^)"
        r"(?:[a-z]:(?:/(?:\*|(?=[\s\"']|$))|\*|(?=[\s\"']|$))|/(?:\*|(?=[\s\"']|$)))"
    )
    DELETE_CMD_PATTERN: str = r"\b(remove-item|ri|del|erase|rmdir|rd|rm)\b"
    DRIVE_PATH_PATTERN: str = r"(?:(?<=[\s\"':=])|^)[a-z]:/"
    RECURSIVE_FLAG_PATTERN: str = r"(-recurse|-force|-r\b|-f\b|/s\b)"
    DIR_DELETE_PATTERN: str = r"\b(rmdir|rd)\b"

    # Обратная совместимость с внешними ссылками
    DANGEROUS_PATTERNS: tuple[str, ...] = DANGEROUS_CMD_PATTERNS

    def _normalize_command(self, command: str) -> str:
        """Нормализация строки команды.

        Приводит к нижнему регистру, заменяет обратные слеши и сжимает пробелы.
        """
        normalized = command.strip().replace("\\", "/").lower()
        return re.sub(r"\s+", " ", normalized)

    def _check_dangerous_cmdlets(self, normalized: str) -> str | None:
        """Проверка на наличие опасных командлетов, системных утилит и удаленного исполнения."""
        for pattern in self.DANGEROUS_CMD_PATTERNS:
            if re.search(pattern, normalized):
                return pattern
        return None

    def _check_filesystem_deletion(self, normalized: str) -> str | None:
        """Проверка на деструктивное удаление корневых дисков, разделов и системных путей."""
        if not re.search(self.DELETE_CMD_PATTERN, normalized):
            return None

        if re.search(self.ROOT_TARGET_PATTERN, normalized):
            return "деструктивное удаление корневого диска или файловой системы"

        is_drive_targeted = bool(re.search(self.DRIVE_PATH_PATTERN, normalized))
        is_recursive_or_forced = bool(re.search(self.RECURSIVE_FLAG_PATTERN, normalized))
        is_dir_removal = bool(re.search(self.DIR_DELETE_PATTERN, normalized))

        if is_drive_targeted and (is_recursive_or_forced or is_dir_removal):
            return "деструктивное рекурсивное удаление системных или дисковых путей"

        return None

    def is_safe_command(self, command: str) -> tuple[bool, str | None]:
        """Проверка команды на наличие запрещенных деструктивных шаблонов и векторов обхода."""
        normalized = self._normalize_command(command)
        if not normalized:
            return True, None

        cmdlet_match = self._check_dangerous_cmdlets(normalized)
        if cmdlet_match is not None:
            return False, cmdlet_match

        deletion_match = self._check_filesystem_deletion(normalized)
        if deletion_match is not None:
            return False, deletion_match

        return True, None

    def run_command(self, command: str, timeout: int = 15) -> tuple[int, str, str]:
        """Выполнение команды в PowerShell с ограничением времени и проверкой безопасности."""
        is_safe, pattern = self.is_safe_command(command)
        if not is_safe:
            raise DangerousCommandError(f"Команда отклонена: обнаружен опасный шаблон '{pattern}'")

        cmd_args: list[str] = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ]

        try:
            result = subprocess.run(
                cmd_args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logger.warning("Время выполнения команды превысило %d секунд: %s", timeout, command)
            return -1, "", f"Превышено время ожидания выполнения команды ({timeout} с)"
        except Exception as error:
            logger.error("Ошибка при выполнении команды: %s", error)
            return -1, "", str(error)

    def open_terminal(
        self, interactive: bool = True
    ) -> subprocess.Popen[str] | subprocess.Popen[bytes]:
        """Запуск терминала Windows (Windows Terminal 'wt.exe' с откатом на 'powershell.exe')."""
        terminal_path = shutil.which("wt.exe") or shutil.which("wt")
        if terminal_path:
            return subprocess.Popen([terminal_path])

        powershell_args: list[str] = (
            ["powershell.exe", "-NoExit"] if interactive else ["powershell.exe"]
        )
        return subprocess.Popen(powershell_args)


class WindowManager:
    """Компонент для управления окнами и системными мультимедиа-клавишами Windows."""

    def list_windows(self) -> list[tuple[int, str]]:
        """Получение списка видимых окон Windows в формате (hwnd, заголовок)."""
        windows: list[tuple[int, str]] = []

        def enum_windows_callback(hwnd: int, extra: list[tuple[int, str]]) -> bool:
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd).strip()
                if title:
                    extra.append((hwnd, title))
            return True

        try:
            win32gui.EnumWindows(enum_windows_callback, windows)
        except Exception as error:
            logger.error("Ошибка при перечислении окон Windows: %s", error)

        return windows

    def find_window(self, title_query: str) -> int | None:
        """Поиск hwnd окна по подстроке заголовка без учета регистра."""
        normalized_query = title_query.strip().lower()
        if not normalized_query:
            return None

        for hwnd, title in self.list_windows():
            if normalized_query in title.lower():
                return hwnd
        return None

    def focus_window(self, title_query: str) -> bool:
        """Перевод окна на передний план с разворачиванием при необходимости."""
        hwnd = self.find_window(title_query)
        if hwnd is None:
            return False

        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            return True
        except Exception as error:
            logger.debug("SetForegroundWindow вызвал исключение, откат на ShowWindow: %s", error)
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                return True
            except Exception as inner_error:
                logger.error("Не удалось сфокусировать окно %d: %s", hwnd, inner_error)
                return False

    def minimize_window(self, title_query: str) -> bool:
        """Сворачивание целевого окна."""
        hwnd = self.find_window(title_query)
        if hwnd is None:
            return False

        try:
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            return True
        except Exception as error:
            logger.error("Не удалось свернуть окно %d: %s", hwnd, error)
            return False

    def maximize_window(self, title_query: str) -> bool:
        """Разворачивание целевого окна на весь экран."""
        hwnd = self.find_window(title_query)
        if hwnd is None:
            return False

        try:
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            return True
        except Exception as error:
            logger.error("Не удалось развернуть окно %d: %s", hwnd, error)
            return False

    def close_window(self, title_query: str) -> bool:
        """Безопасная отправка сигнала закрытия окна WM_CLOSE."""
        hwnd = self.find_window(title_query)
        if hwnd is None:
            return False

        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            return True
        except Exception as error:
            logger.error("Не удалось закрыть окно %d: %s", hwnd, error)
            return False

    def minimize_all(self) -> bool:
        """Сворачивание всех окон на рабочем столе комбинацией клавиш Win+M."""
        try:
            win32api.keybd_event(win32con.VK_LWIN, 0, 0, 0)
            win32api.keybd_event(ord("M"), 0, 0, 0)
            win32api.keybd_event(ord("M"), 0, win32con.KEYEVENTF_KEYUP, 0)
            win32api.keybd_event(win32con.VK_LWIN, 0, win32con.KEYEVENTF_KEYUP, 0)
            return True
        except Exception as error:
            logger.error("Не удалось свернуть все окна: %s", error)
            return False

    def volume_up(self) -> None:
        """Увеличение громкости системы на один шаг."""
        win32api.keybd_event(win32con.VK_VOLUME_UP, 0, 0, 0)
        win32api.keybd_event(win32con.VK_VOLUME_UP, 0, win32con.KEYEVENTF_KEYUP, 0)

    def volume_down(self) -> None:
        """Уменьшение громкости системы на один шаг."""
        win32api.keybd_event(win32con.VK_VOLUME_DOWN, 0, 0, 0)
        win32api.keybd_event(win32con.VK_VOLUME_DOWN, 0, win32con.KEYEVENTF_KEYUP, 0)

    def volume_mute(self) -> None:
        """Переключение режима отключения звука (Mute)."""
        win32api.keybd_event(win32con.VK_VOLUME_MUTE, 0, 0, 0)
        win32api.keybd_event(win32con.VK_VOLUME_MUTE, 0, win32con.KEYEVENTF_KEYUP, 0)


class OSController:
    """Единый фасад для взаимодействия с операционной системой Windows 11."""

    def __init__(self, config_path: Path | str | None = None) -> None:
        self.web = WebLauncher(config_path=config_path)
        self.shell = ShellExecutor()
        self.window = WindowManager()

    def open_url(self, url: str) -> bool:
        """Открытие URL-адреса в браузере."""
        return self.web.open_url(url)

    def search(self, query: str, engine: str = "google") -> bool:
        """Поиск запроса в поисковой системе."""
        return self.web.search(query=query, engine=engine)

    def resolve_alias(self, alias: str) -> str | None:
        """Разрешение псевдонима веб-сайта."""
        return self.web.resolve_alias(alias)

    def open_alias(self, alias: str) -> bool:
        """Открытие веб-сайта по псевдониму."""
        return self.web.open_alias(alias)

    def run_command(self, command: str, timeout: int = 15) -> tuple[int, str, str]:
        """Безопасное выполнение команды в PowerShell."""
        return self.shell.run_command(command=command, timeout=timeout)

    def open_terminal(
        self, interactive: bool = True
    ) -> subprocess.Popen[str] | subprocess.Popen[bytes]:
        """Открытие окна терминала Windows."""
        return self.shell.open_terminal(interactive=interactive)

    def list_windows(self) -> list[tuple[int, str]]:
        """Получение списка активных видимых окон."""
        return self.window.list_windows()

    def focus_window(self, title_query: str) -> bool:
        """Фокусировка на окне по заголовку."""
        return self.window.focus_window(title_query)

    def minimize_window(self, title_query: str) -> bool:
        """Сворачивание окна по заголовку."""
        return self.window.minimize_window(title_query)

    def minimize_all(self) -> bool:
        """Сворачивание всех окон."""
        self.minimize_window("")
        return self.window.minimize_all()

    def maximize_window(self, title_query: str) -> bool:
        """Разворачивание окна по заголовку."""
        return self.window.maximize_window(title_query)

    def close_window(self, title_query: str) -> bool:
        """Закрытие окна по заголовку."""
        return self.window.close_window(title_query)

    def volume_up(self) -> None:
        """Увеличение громкости."""
        self.window.volume_up()

    def volume_down(self) -> None:
        """Уменьшение громкости."""
        self.window.volume_down()

    def volume_mute(self) -> None:
        """Отключение/включение звука."""
        self.window.volume_mute()
