"""Модуль маршрутизации намерений (Intent Router) для системы GupkaCommander.

Реализует концепцию Fast-Path First:
1. Детерминированное распознавание команд через регулярные выражения.
2. Нечеткое сопоставление (rapidfuzz) по алиасам приложений и веб-сервисов.
3. Fallback на AI_COGNITIVE для свободных запросов естественного языка.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)


class Intent(StrEnum):
    """Типы распознаваемых намерений пользователя."""

    OPEN_APP = "open_app"
    OPEN_URL = "open_url"
    SEARCH_WEB = "search_web"
    RUN_COMMAND = "run_command"
    WINDOW_CONTROL = "window_control"
    VOLUME_CONTROL = "volume_control"
    AI_COGNITIVE = "ai_cognitive"
    VISION_INTERACT = "vision_interact"


@dataclass(frozen=True)
class RouteResult:
    """Структурированный результат маршрутизации намерения."""

    intent: Intent
    query: str
    target: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    matched_rule: str = ""


class IntentRouter:
    """Маршрутизатор намерений пользователя на основе Fast-Path First."""

    def __init__(
        self,
        config_path: Path | str | None = None,
        fuzzy_threshold: float = 75.0,
    ) -> None:
        """Инициализация маршрутизатора намерения.

        Args:
            config_path: Путь к файлу алиасов commands.json.
            fuzzy_threshold: Порог нечеткого сравнения rapidfuzz (0-100).
        """
        self.config_path: Path = (
            Path(config_path)
            if config_path is not None
            else Path(__file__).resolve().parent.parent.parent / "config" / "commands.json"
        )
        self.fuzzy_threshold: float = fuzzy_threshold
        self.web_aliases: dict[str, str] = {}
        self.search_engines: dict[str, str] = {}
        self.app_aliases: dict[str, str] = {}
        self.load_config()

    def load_config(self) -> None:
        """Загрузка словарей алиасов и поисковых систем из конфигурационного файла."""
        if not self.config_path.exists():
            logger.warning("Конфиг команд не найден: %s. Применяются дефолты.", self.config_path)
            self._set_default_config()
            return

        try:
            with open(self.config_path, encoding="utf-8") as file:
                data: dict[str, Any] = json.load(file)
            self.web_aliases = {k.lower(): str(v) for k, v in data.get("web_aliases", {}).items()}
            self.search_engines = {
                k.lower(): str(v) for k, v in data.get("search_engines", {}).items()
            }
            self.app_aliases = {k.lower(): str(v) for k, v in data.get("app_aliases", {}).items()}
        except Exception as err:
            logger.error("Ошибка при чтении %s: %s. Применяются дефолты.", self.config_path, err)
            self._set_default_config()

    def _set_default_config(self) -> None:
        """Установка конфигурации по умолчанию при отсутствии файла."""
        self.web_aliases = {
            "ютуб": "https://www.youtube.com",
            "youtube": "https://www.youtube.com",
            "яндекс": "https://ya.ru",
            "yandex": "https://ya.ru",
            "гугл": "https://www.google.com",
            "google": "https://www.google.com",
            "гитхаб": "https://github.com",
            "github": "https://github.com",
            "вк": "https://vk.com",
            "телеграм": "https://web.telegram.org",
        }
        self.search_engines = {
            "google": "https://www.google.com/search?q={query}",
            "yandex": "https://ya.ru/search/?text={query}",
            "youtube": "https://www.youtube.com/results?search_query={query}",
        }
        self.app_aliases = {
            "телега": "telegram",
            "телефон": "telegram",
            "браузер": "chrome",
            "хром": "chrome",
            "терминал": "wt",
            "консоль": "wt",
            "блокнот": "notepad",
            "калькулятор": "calculator",
            "проводник": "explorer",
            "диспетчер задач": "taskmgr",
            "настройки": "ms-settings:",
        }

    def route(self, query: str) -> RouteResult:
        """Определение намерения по входящей строке запроса.

        Args:
            query: Текст запроса от пользователя или распознанный голос.

        Returns:
            RouteResult с определенным намерением, параметрами и уверенностью.
        """
        clean_query = query.strip()
        if not clean_query:
            return RouteResult(
                intent=Intent.AI_COGNITIVE,
                query="",
                target="",
                params={},
                confidence=1.0,
                matched_rule="empty_query",
            )

        lower_query = clean_query.lower()

        handlers = (
            self._match_volume_control,
            self._match_window_control,
            self._match_run_command,
            self._match_search_web,
            self._match_vision_interact,
            self._match_open_url,
            self._match_open_app,
            self._match_fuzzy_app_or_web,
        )

        for handler in handlers:
            result = handler(clean_query, lower_query)
            if result is not None:
                return result

        return RouteResult(
            intent=Intent.AI_COGNITIVE,
            query=clean_query,
            target=clean_query,
            params={"prompt": clean_query},
            confidence=1.0,
            matched_rule="fallback_ai_cognitive",
        )

    def _match_volume_control(self, raw_query: str, lower_query: str) -> RouteResult | None:
        """Распознавание команд изменения системной громкости."""
        vol_up_patterns = (
            r"^(?:сделай\s+)?погромче$",
            r"^(?:сделай\s+)?громче$",
            r"^(?:прибавь|увеличь)\s+громкость$",
            r"^(?:громкость|звук)\s+(?:выше|больше)$",
            r"^(?:прибавь|увеличь)\s+звук$",
        )
        for pat in vol_up_patterns:
            if re.search(pat, lower_query):
                return RouteResult(
                    intent=Intent.VOLUME_CONTROL,
                    query=raw_query,
                    target="up",
                    params={"action": "up", "step": 2},
                    matched_rule="volume_up_pattern",
                )

        vol_down_patterns = (
            r"^(?:сделай\s+)?потише$",
            r"^(?:сделай\s+)?тише$",
            r"^(?:убавь|уменьши)\s+громкость$",
            r"^(?:громкость|звук)\s+(?:ниже|меньше)$",
            r"^(?:убавь|уменьши)\s+звук$",
        )
        for pat in vol_down_patterns:
            if re.search(pat, lower_query):
                return RouteResult(
                    intent=Intent.VOLUME_CONTROL,
                    query=raw_query,
                    target="down",
                    params={"action": "down", "step": 2},
                    matched_rule="volume_down_pattern",
                )

        mute_pat = r"^(?:выключи|заглуши)\s+звук$|^без\s+звука$|^мут$|^mute$"
        mute_match = re.search(mute_pat, lower_query)
        if mute_match:
            return RouteResult(
                intent=Intent.VOLUME_CONTROL,
                query=raw_query,
                target="mute",
                params={"action": "mute"},
                matched_rule="volume_mute_pattern",
            )

        set_match = re.search(r"^(?:установи\s+)?громкость\s+(\d+)$", lower_query)
        if set_match:
            val = int(set_match.group(1))
            return RouteResult(
                intent=Intent.VOLUME_CONTROL,
                query=raw_query,
                target=str(val),
                params={"action": "set", "value": val},
                matched_rule="volume_set_pattern",
            )

        return None

    def _match_window_control(self, raw_query: str, lower_query: str) -> RouteResult | None:
        """Распознавание команд управления окнами Windows."""
        patterns: list[tuple[str, str]] = [
            (r"^(?:сверни|свернуть|минимизируй)\s+окно$", "minimize"),
            (r"^(?:разверни|развернуть|максимизируй)\s+окно$", "maximize"),
            (r"^(?:закрой|закрыть)\s+окно$", "close"),
            (r"^(?:восстанови|восстановить)\s+окно$", "restore"),
            (r"^(?:сверни|свернуть)\s+(?:все|всё)(?:\s+окна)?$", "minimize_all"),
        ]
        for pattern, action in patterns:
            if re.search(pattern, lower_query):
                return RouteResult(
                    intent=Intent.WINDOW_CONTROL,
                    query=raw_query,
                    target=action,
                    params={"action": action},
                    matched_rule=f"window_{action}_pattern",
                )
        return None

    def _match_run_command(self, raw_query: str, lower_query: str) -> RouteResult | None:
        """Распознавание команд выполнения скриптов и терминала."""
        if re.search(r"^(?:открой\s+|запусти\s+)?(?:терминал|консоль)$", lower_query):
            return RouteResult(
                intent=Intent.RUN_COMMAND,
                query=raw_query,
                target="terminal",
                params={"shell": "powershell", "command": ""},
                matched_rule="terminal_open_pattern",
            )

        pwsh_match = re.match(
            r"^(?:запусти|выполни|выполнить)?\s*(powershell|pwsh|cmd|bash)\s+(.+)$",
            raw_query,
            re.IGNORECASE,
        )
        if pwsh_match:
            shell_name = pwsh_match.group(1).lower()
            cmd_text = pwsh_match.group(2).strip()
            return RouteResult(
                intent=Intent.RUN_COMMAND,
                query=raw_query,
                target=cmd_text,
                params={"shell": shell_name, "command": cmd_text},
                matched_rule="shell_explicit_command",
            )

        term_match = re.match(
            r"^(?:выполни\s+команду|в\s+терминале|в\s+консоли)\s+(.+)$",
            raw_query,
            re.IGNORECASE,
        )
        if term_match:
            cmd_text = term_match.group(1).strip()
            return RouteResult(
                intent=Intent.RUN_COMMAND,
                query=raw_query,
                target=cmd_text,
                params={"shell": "powershell", "command": cmd_text},
                matched_rule="terminal_command_pattern",
            )

        return None

    def _match_search_web(self, raw_query: str, lower_query: str) -> RouteResult | None:
        """Распознавание поисковых запросов в сети и конкретных сервисах."""
        search_in_match = re.match(
            r"^(?:найди|поищи|погугли|поиск)\s+в\s+([a-zA-Zа-яА-ЯёЁ0-9_-]+)\s+(.+)$",
            lower_query,
        )
        if search_in_match:
            engine_raw = search_in_match.group(1).strip()
            subquery = raw_query[search_in_match.start(2) : search_in_match.end(2)].strip()
            engine = self._normalize_search_engine(engine_raw)
            return RouteResult(
                intent=Intent.SEARCH_WEB,
                query=raw_query,
                target=subquery,
                params={"engine": engine, "query": subquery},
                matched_rule="search_in_engine_pattern",
            )

        direct_search = re.match(
            r"^(?:найди|поищи|погугли)\s+(?:в\s+интернете\s+|в\s+сети\s+)?(.+)$",
            raw_query,
            re.IGNORECASE,
        )
        if direct_search:
            subquery = direct_search.group(1).strip()
            return RouteResult(
                intent=Intent.SEARCH_WEB,
                query=raw_query,
                target=subquery,
                params={"engine": "google", "query": subquery},
                matched_rule="direct_search_pattern",
            )

        return None

    def _normalize_search_engine(self, engine_raw: str) -> str:
        """Нормализация идентификатора поисковой системы."""
        lowered = engine_raw.lower()
        if lowered in ("youtube", "ютуб"):
            return "youtube"
        if lowered in ("yandex", "яндекс"):
            return "yandex"
        if lowered in ("google", "гугл"):
            return "google"

        keys = list(self.search_engines.keys()) + ["youtube", "yandex", "google"]
        match = process.extractOne(lowered, keys, scorer=fuzz.ratio)
        if match and match[1] >= self.fuzzy_threshold:
            normalized = str(match[0])
            if normalized in ("ютуб", "youtube"):
                return "youtube"
            if normalized in ("яндекс", "yandex"):
                return "yandex"
            return "google"
        return "google"

    def _match_vision_interact(self, raw_query: str, lower_query: str) -> RouteResult | None:
        """Распознавание команд компьютерного зрения и кликов по экрану."""
        inspect_match = re.search(
            r"^(?:что\s+на\s+экране|посмотри\s+на\s+экран|проанализируй\s+экран|снимок\s+экрана)$",
            lower_query,
        )
        if inspect_match:
            return RouteResult(
                intent=Intent.VISION_INTERACT,
                query=raw_query,
                target="screen",
                params={"action": "inspect_screen"},
                matched_rule="inspect_screen_pattern",
            )

        coord_match = re.match(
            r"^(?:кликни|нажми|клик)(?:\s+(?:на|по))?(?:\s+координатам)?\s+(\d+)\s*[, ]\s*(\d+)$",
            raw_query,
            re.IGNORECASE,
        )
        if coord_match:
            x_val = int(coord_match.group(1))
            y_val = int(coord_match.group(2))
            return RouteResult(
                intent=Intent.VISION_INTERACT,
                query=raw_query,
                target=f"{x_val}, {y_val}",
                params={"action": "click_screen", "x": x_val, "y": y_val},
                matched_rule="vision_click_coordinates_pattern",
            )

        click_match = re.match(
            r"^(?:кликни|нажми|клик)(?:\s+(?:на|по))?(?:\s+(?:кнопку|иконку|ссылку))?\s+(.+)$",
            raw_query,
            re.IGNORECASE,
        )
        if click_match:
            target_elem = click_match.group(1).strip()
            return RouteResult(
                intent=Intent.VISION_INTERACT,
                query=raw_query,
                target=target_elem,
                params={"action": "click_screen", "target": target_elem},
                matched_rule="vision_click_pattern",
            )

        type_match = re.match(
            r"^(?:введи|напечатай)(?:\s+текст)?\s+(.+)$|^напиши\s+текст\s+(.+)$",
            raw_query,
            re.IGNORECASE,
        )
        if type_match:
            text_to_type = (type_match.group(1) or type_match.group(2) or "").strip()
            return RouteResult(
                intent=Intent.VISION_INTERACT,
                query=raw_query,
                target=text_to_type,
                params={"action": "type_text", "text": text_to_type},
                matched_rule="vision_type_pattern",
            )

        return None

    def _match_open_url(self, raw_query: str, lower_query: str) -> RouteResult | None:
        """Распознавание команд перехода по веб-ссылкам и веб-алиасам."""
        url_match = re.match(
            r"^(?:открой|перейди\s+на)\s+(https?://\S+)$",
            raw_query,
            re.IGNORECASE,
        )
        if url_match:
            url = url_match.group(1).strip()
            return RouteResult(
                intent=Intent.OPEN_URL,
                query=raw_query,
                target=url,
                params={"url": url},
                matched_rule="explicit_url_pattern",
            )

        site_match = re.match(
            r"^(?:открой|перейди\s+на)\s+(?:сайт\s+)?([a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?:/\S*)?)$",
            raw_query,
            re.IGNORECASE,
        )
        if site_match:
            raw_domain = site_match.group(1).strip()
            full_url = f"https://{raw_domain}"
            return RouteResult(
                intent=Intent.OPEN_URL,
                query=raw_query,
                target=full_url,
                params={"url": full_url},
                matched_rule="domain_url_pattern",
            )

        return self._check_web_alias_command(raw_query, lower_query)

    def _check_web_alias_command(self, raw_query: str, lower_query: str) -> RouteResult | None:
        """Проверка явного запроса на открытие известного веб-алиаса."""
        match = re.match(
            r"^(?:открой|перейди\s+на)\s+(?:сайт\s+)?([a-zA-Zа-яА-ЯёЁ0-9_-]+)$",
            lower_query,
        )
        if not match:
            return None

        alias_key = match.group(1).strip()
        # Если alias_key явно в app_aliases, даем приоритет приложению
        if alias_key in self.app_aliases:
            return None

        resolved_web = self._resolve_web_alias(alias_key)
        if resolved_web is not None:
            url, matched_alias = resolved_web
            return RouteResult(
                intent=Intent.OPEN_URL,
                query=raw_query,
                target=url,
                params={"url": url, "alias": matched_alias},
                matched_rule="web_alias_matched",
            )

        return None

    def _resolve_web_alias(self, name: str) -> tuple[str, str] | None:
        """Поиск веб-алиаса с учетом нечеткого сопоставления."""
        lowered = name.lower()
        if lowered in self.web_aliases:
            return self.web_aliases[lowered], lowered

        if self.web_aliases:
            match = process.extractOne(lowered, list(self.web_aliases.keys()), scorer=fuzz.ratio)
            if match and match[1] >= self.fuzzy_threshold:
                matched_key = str(match[0])
                return self.web_aliases[matched_key], matched_key
        return None

    def _resolve_app_alias(self, candidate: str) -> str:
        """Поиск алиаса приложения с учетом прямого совпадения и rapidfuzz."""
        cand_lower = candidate.lower()
        if cand_lower in self.app_aliases:
            return self.app_aliases[cand_lower]

        if self.app_aliases:
            match = process.extractOne(cand_lower, list(self.app_aliases.keys()), scorer=fuzz.ratio)
            if match and match[1] >= self.fuzzy_threshold:
                return self.app_aliases[str(match[0])]
        return candidate

    def _match_open_app(self, raw_query: str, lower_query: str) -> RouteResult | None:
        """Распознавание команд открытия приложений с учетом алиасов."""
        match = re.match(
            r"^(?:открой|запусти|стартани)(?:\s+(?:приложение|программу))?\s+(.+)$",
            raw_query,
            re.IGNORECASE,
        )
        if not match:
            return None

        candidate = match.group(1).strip()
        cand_lower = candidate.lower()

        # Исключаем попадание веб-алиасов в запуск приложений
        if cand_lower in self.web_aliases and cand_lower not in self.app_aliases:
            return None

        resolved_name = self._resolve_app_alias(candidate)
        return RouteResult(
            intent=Intent.OPEN_APP,
            query=raw_query,
            target=resolved_name,
            params={"app": resolved_name, "raw_target": candidate},
            matched_rule="open_app_pattern",
        )

    def _match_fuzzy_app_or_web(self, raw_query: str, lower_query: str) -> RouteResult | None:
        """Нечеткое сопоставление (rapidfuzz) по алиасам при наличии опечаток."""
        clean = re.sub(r"^(?:открой|запусти|перейди\s+на)\s+", "", lower_query).strip()
        if not clean or len(clean) < 3:
            return None

        if self.app_aliases:
            app_match = process.extractOne(clean, list(self.app_aliases.keys()), scorer=fuzz.ratio)
            if app_match and app_match[1] >= self.fuzzy_threshold:
                matched_alias = str(app_match[0])
                resolved = self.app_aliases[matched_alias]
                return RouteResult(
                    intent=Intent.OPEN_APP,
                    query=raw_query,
                    target=resolved,
                    params={"app": resolved, "fuzzy_alias": matched_alias},
                    confidence=float(app_match[1]) / 100.0,
                    matched_rule="fuzzy_app_alias",
                )

        if self.web_aliases:
            web_match = process.extractOne(clean, list(self.web_aliases.keys()), scorer=fuzz.ratio)
            if web_match and web_match[1] >= self.fuzzy_threshold:
                matched_alias = str(web_match[0])
                url = self.web_aliases[matched_alias]
                return RouteResult(
                    intent=Intent.OPEN_URL,
                    query=raw_query,
                    target=url,
                    params={"url": url, "fuzzy_alias": matched_alias},
                    confidence=float(web_match[1]) / 100.0,
                    matched_rule="fuzzy_web_alias",
                )

        return None
