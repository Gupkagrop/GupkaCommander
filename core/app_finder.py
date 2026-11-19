"""Модуль обнаружения, сканирования и нечеткого поиска установленных приложений Windows."""

import json
import os
import winreg
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

_EXCLUDED_SHORTCUT_KEYWORDS: tuple[str, ...] = (
    "uninstall",
    "удалить",
    "деинсталл",
    "deinstall",
    "help",
    "readme",
)


@dataclass
class AppInfo:
    """Модель данных информации об установленном приложении Windows."""

    name: str
    path: str
    source: str
    icon_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Сериализует объект AppInfo в словарь для сохранения в JSON."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppInfo":
        """Создает объект AppInfo из словаря."""
        return cls(
            name=str(data.get("name", "")),
            path=str(data.get("path", "")),
            source=str(data.get("source", "")),
            icon_path=data.get("icon_path"),
        )


def _clean_executable_path(raw_path: str) -> str:
    """Очищает строку пути к исполняемому файлу от кавычек и аргументов."""
    cleaned = raw_path.strip()
    if not cleaned:
        return ""

    if cleaned.startswith('"'):
        end_quote = cleaned.find('"', 1)
        if end_quote != -1:
            return cleaned[1:end_quote].strip()
        return cleaned.strip('"').strip()

    if "," in cleaned:
        cleaned = cleaned.split(",")[0].strip('"').strip()

    for sep in (" -", " /"):
        if sep in cleaned:
            cleaned = cleaned.split(sep)[0].strip()

    return cleaned


def _read_registry_string(key: winreg.HKEYType, value_name: str) -> str | None:
    """Безопасно считывает строковое значение из ключа реестра Windows."""
    try:
        val, _ = winreg.QueryValueEx(key, value_name)
        if isinstance(val, str) and val.strip():
            return val.strip()
        return None
    except OSError:
        return None


def _scan_single_uninstall_subkey(root_key: winreg.HKEYType, sub_name: str) -> AppInfo | None:
    """Считывает данные приложения из отдельного подраздела Uninstall."""
    try:
        with winreg.OpenKey(root_key, sub_name, 0, winreg.KEY_READ) as app_key:
            display_name = _read_registry_string(app_key, "DisplayName")
            if not display_name:
                return None

            try:
                sys_comp, _ = winreg.QueryValueEx(app_key, "SystemComponent")
                if sys_comp == 1:
                    return None
            except OSError:
                pass

            icon_val = _read_registry_string(app_key, "DisplayIcon")
            app_path = _clean_executable_path(icon_val) if icon_val else ""

            if not app_path:
                loc_val = _read_registry_string(app_key, "InstallLocation")
                if loc_val:
                    app_path = loc_val.strip().strip('"')

            if not app_path:
                return None

            return AppInfo(
                name=display_name,
                path=app_path,
                source="registry_uninstall",
                icon_path=icon_val,
            )
    except OSError:
        return None


def _scan_uninstall_key(hive: int, subkey_path: str) -> list[AppInfo]:
    """Сканирует раздел реестра Uninstall и возвращает найденные приложения."""
    results: list[AppInfo] = []
    try:
        with winreg.OpenKey(hive, subkey_path, 0, winreg.KEY_READ) as root_key:
            num_subkeys, _, _ = winreg.QueryInfoKey(root_key)
            for i in range(num_subkeys):
                sub_name = winreg.EnumKey(root_key, i)
                app = _scan_single_uninstall_subkey(root_key, sub_name)
                if app is not None:
                    results.append(app)
    except OSError:
        return results
    return results


def _scan_single_app_path_subkey(root_key: winreg.HKEYType, sub_name: str) -> AppInfo | None:
    """Считывает запись отдельного исполняемого файла из раздела App Paths."""
    try:
        with winreg.OpenKey(root_key, sub_name, 0, winreg.KEY_READ) as app_key:
            raw_path = _read_registry_string(app_key, "")
            if not raw_path:
                return None
            clean_path = _clean_executable_path(raw_path)
            if not clean_path:
                return None

            name = sub_name[:-4] if sub_name.lower().endswith(".exe") else sub_name
            return AppInfo(
                name=name,
                path=clean_path,
                source="registry_app_paths",
                icon_path=clean_path,
            )
    except OSError:
        return None


def _scan_app_paths_key(hive: int, subkey_path: str) -> list[AppInfo]:
    """Сканирует раздел реестра App Paths и возвращает найденные приложения."""
    results: list[AppInfo] = []
    try:
        with winreg.OpenKey(hive, subkey_path, 0, winreg.KEY_READ) as root_key:
            num_subkeys, _, _ = winreg.QueryInfoKey(root_key)
            for i in range(num_subkeys):
                sub_name = winreg.EnumKey(root_key, i)
                app = _scan_single_app_path_subkey(root_key, sub_name)
                if app is not None:
                    results.append(app)
    except OSError:
        return results
    return results


def _is_valid_shortcut(lnk_path: Path) -> bool:
    """Проверяет, что ярлык не относится к деинсталляторам или справке."""
    stem_lower = lnk_path.stem.lower()
    return not any(kw in stem_lower for kw in _EXCLUDED_SHORTCUT_KEYWORDS)


def _scan_start_menu_directory(dir_path: Path) -> list[AppInfo]:
    """Рекурсивно сканирует директорию меню Пуск на наличие ярлыков .lnk."""
    if not dir_path.exists() or not dir_path.is_dir():
        return []

    results: list[AppInfo] = []
    for lnk in dir_path.rglob("*.lnk"):
        if not _is_valid_shortcut(lnk):
            continue

        try:
            resolved_path = str(lnk.resolve())
        except OSError:
            resolved_path = str(lnk.absolute())

        results.append(
            AppInfo(
                name=lnk.stem,
                path=resolved_path,
                source="start_menu",
                icon_path=str(lnk),
            )
        )
    return results


class AppFinder:
    """Менеджер обнаружения, сканирования и поиска приложений Windows."""

    def __init__(
        self,
        cache_path: Path | str | None = None,
        commands_path: Path | str | None = None,
    ) -> None:
        from core.paths import get_cache_path, get_config_path

        if cache_path is not None and str(cache_path) != "config/app_cache.json":
            self.cache_path = Path(cache_path)
        else:
            self.cache_path = get_cache_path("app_cache.json")

        if commands_path is not None and str(commands_path) != "config/commands.json":
            self.commands_path = Path(commands_path)
        else:
            self.commands_path = get_config_path("commands.json")

        self._apps: list[AppInfo] = []
        self._aliases: dict[str, str] = {}
        self.load_aliases()

    @property
    def apps(self) -> list[AppInfo]:
        """Возвращает текущий список зарегистрированных приложений."""
        return list(self._apps)

    @property
    def aliases(self) -> dict[str, str]:
        """Возвращает копию словаря зарегистрированных алиасов."""
        return dict(self._aliases)

    def load_aliases(self) -> dict[str, str]:
        """Загружает алиасы приложений из конфигурации commands.json."""
        if not self.commands_path.exists():
            self._aliases = {}
            return self._aliases

        try:
            with open(self.commands_path, encoding="utf-8") as f:
                data = json.load(f)
            raw_aliases = data.get("app_aliases", {})
            if isinstance(raw_aliases, dict):
                self._aliases = {
                    str(k).strip().lower(): str(v).strip() for k, v in raw_aliases.items()
                }
        except (OSError, json.JSONDecodeError):
            self._aliases = {}

        return self._aliases

    def resolve_alias(self, query: str) -> str:
        """Разрешает синоним приложения в его каноническое имя или команду."""
        return self._aliases.get(query.strip().lower(), query)

    def save_cache(self) -> None:
        """Сохраняет текущий список приложений в локальный JSON-кэш."""
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [app.to_dict() for app in self._apps]
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def load_cache(self) -> list[AppInfo]:
        """Загружает список приложений из файла кэша, если он существует."""
        if not self.cache_path.exists():
            return []

        try:
            with open(self.cache_path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return []
            self._apps = [AppInfo.from_dict(item) for item in data if isinstance(item, dict)]
            return list(self._apps)
        except (OSError, json.JSONDecodeError):
            return []

    def scan_registry(self) -> list[AppInfo]:
        """Сканирует разделы Uninstall и App Paths в реестре Windows."""
        registry_apps: list[AppInfo] = []

        # 1. HKLM Uninstall
        registry_apps.extend(
            _scan_uninstall_key(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            )
        )
        # 2. HKCU Uninstall
        registry_apps.extend(
            _scan_uninstall_key(
                winreg.HKEY_CURRENT_USER,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            )
        )
        # 3. HKLM WOW6432Node Uninstall
        registry_apps.extend(
            _scan_uninstall_key(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
            )
        )
        # 4. HKLM App Paths
        registry_apps.extend(
            _scan_app_paths_key(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
            )
        )

        return registry_apps

    def scan_start_menu(self) -> list[AppInfo]:
        """Сканирует папки меню Пуск для всех пользователей и текущего пользователя."""
        start_apps: list[AppInfo] = []

        program_data = Path(r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs")
        start_apps.extend(_scan_start_menu_directory(program_data))

        appdata_str = os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs")
        user_start = Path(appdata_str)
        start_apps.extend(_scan_start_menu_directory(user_start))

        return start_apps

    def scan(self, force: bool = False) -> list[AppInfo]:
        """Выполняет полное сканирование приложений с поддержкой кэширования."""
        if not force:
            cached = self.load_cache()
            if cached:
                return cached

        collected: list[AppInfo] = []
        collected.extend(self.scan_start_menu())
        collected.extend(self.scan_registry())

        seen_names: set[str] = set()
        unique_apps: list[AppInfo] = []

        for app in collected:
            clean_name = app.name.strip().lower()
            if not clean_name or clean_name in seen_names:
                continue
            seen_names.add(clean_name)
            unique_apps.append(app)

        self._apps = unique_apps
        self.save_cache()
        return list(self._apps)

    def _compute_similarity(self, query_text: str, target_text: str) -> float:
        """Оценивает схожесть строк через комбинацию алгоритмов rapidfuzz."""
        s1 = float(fuzz.token_sort_ratio(query_text, target_text))
        s2 = float(fuzz.partial_ratio(query_text, target_text))
        return max(s1, s2)

    def _score_apps(
        self, original_query: str, resolved_query: str, threshold: float
    ) -> list[tuple[AppInfo, float]]:
        """Рассчитывает баллы соответствия для всех приложений в реестре поиска."""
        orig_lower = original_query.lower()
        res_lower = resolved_query.lower()
        scored: list[tuple[AppInfo, float]] = []

        for app in self._apps:
            target_name = app.name.lower()
            score = self._compute_similarity(orig_lower, target_name)

            if res_lower != orig_lower:
                alt_score = self._compute_similarity(res_lower, target_name)
                score = max(score, alt_score)

            if score >= threshold:
                scored.append((app, score))

        return scored

    def _fallback_alias_app(self, original_query: str, resolved_query: str) -> list[AppInfo]:
        """Создает синтетический результат для системных алиасов при отсутствии совпадений."""
        orig_lower = original_query.lower()
        if orig_lower in self._aliases:
            target = self._aliases[orig_lower]
            return [AppInfo(name=original_query, path=target, source="alias")]
        return []

    def find(self, query: str, threshold: float = 60.0) -> list[AppInfo]:
        """Ищет приложения по запросу с использованием нечеткого поиска rapidfuzz."""
        clean_query = query.strip()
        if not clean_query:
            return []

        if not self._apps:
            self.scan(force=False)

        resolved_query = self.resolve_alias(clean_query)
        scored = self._score_apps(clean_query, resolved_query, threshold)
        if scored:
            scored.sort(key=lambda item: item[1], reverse=True)
            return [app for app, _ in scored]

        return self._fallback_alias_app(clean_query, resolved_query)
