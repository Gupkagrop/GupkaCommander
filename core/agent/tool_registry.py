"""Единый реестр инструментов автономного агента GupkaCommander (ToolRegistry).

Объединяет Fast-Path (OSController/AppFinder), Microsoft UIA (UIAutomationManager),
Browser CDP (BrowserCDPBridge), Vision/SoM (VisionAgent/SoMAnnotator) и MemoryManager.
Обеспечивает жёсткое усечение вывода (<= 2000 символов) и Safety Gate для опасных команд.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
import subprocess
import time
from collections.abc import Callable
from typing import Any, Literal

from core.app_finder import AppFinder
from core.browser.cdp_bridge import BrowserCDPBridge
from core.failsafe import FailSafeMonitor, get_failsafe_monitor, safe_execution
from core.memory.memory_manager import MemoryManager
from core.os_controller import OSController
from core.uia.uia_controller import UIAutomationManager
from core.vision.monitor_manager import MonitorManager
from core.vision.som_annotator import SoMAnnotator
from core.vision.vision_agent import VisionAgent

logger = logging.getLogger(__name__)

EngineType = Literal["Fast-Path", "UIA", "CDP", "Vision", "LLM"]
MAX_OUTPUT_CHARS: int = 2000
_MAX_WAIT_SECONDS: float = 10.0

_EXTRA_DANGEROUS_PATTERNS: tuple[str, ...] = (
    r"\btaskkill\b.*?/im\s+(?:explorer|svchost|lsass|csrss|wininit|winlogon)(?:\.exe)?\b",
    r"\bstop-process\b.*?(?:explorer|svchost|lsass|csrss|wininit|winlogon)\b",
    r"\bremove-itemproperty\b.*?\bhklm:",
    r"\bset-itemproperty\b.*?\bhklm:",
    r"\breg\s+add\b.*?\bhklm\\",
    r"\bcipher\s+/w:",
    r"\btakeown\b.*?\bc:\\windows",
    r"\bicacls\b.*?\bc:\\windows.*?/reset",
    r"\bnet\s+user\b.*?/delete",
    r"\bbcdboot\b",
)


def truncate_output(text: str, max_chars: int = MAX_OUTPUT_CHARS) -> str:
    """Усекает текстовый вывод до <= max_chars символов с сохранением начала и конца."""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 40:
        return text[:max_chars]

    marker = f"\n...[обрезано {len(text) - max_chars} симв.]...\n"
    budget = max_chars - len(marker)
    if budget <= 10:
        return text[:max_chars]
    head_len = budget // 2
    tail_len = budget - head_len
    return f"{text[:head_len]}{marker}{text[-tail_len:]}"


@dataclasses.dataclass(frozen=True)
class ToolExecutionResult:
    """Структурированный результат выполнения одного системного инструмента."""

    tool: str
    success: bool
    output: str
    engine: EngineType
    error: str = ""
    finished: bool = False


def _tool_schema(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    """Вспомогательный конструктор OpenAI Function Calling схемы."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


AGENT_TOOL_SCHEMAS: list[dict[str, Any]] = [
    _tool_schema(
        "open_application",
        "Запуск установленного приложения Windows по имени или алиасу.",
        {"app_name": {"type": "string", "description": "Имя приложения (notepad, chrome, code)."}},
        ["app_name"],
    ),
    _tool_schema(
        "open_url",
        "Открытие URL в системном браузере по умолчанию.",
        {"url": {"type": "string", "description": "Полный адрес URL или домен."}},
        ["url"],
    ),
    _tool_schema(
        "search_web",
        "Поиск информации через Google, Yandex или YouTube.",
        {
            "query": {"type": "string", "description": "Поисковый запрос."},
            "engine": {
                "type": "string",
                "enum": ["google", "yandex", "youtube"],
                "description": "Поисковая система.",
            },
        },
        ["query"],
    ),
    _tool_schema(
        "run_command",
        "Выполнение безопасной команды PowerShell или cmd (с фильтром Safety Gate).",
        {
            "command": {"type": "string", "description": "Команда для выполнения."},
            "shell": {"type": "string", "enum": ["powershell", "cmd"]},
        },
        ["command"],
    ),
    _tool_schema(
        "window_control",
        "Управление окнами и системной громкостью.",
        {
            "action": {
                "type": "string",
                "enum": [
                    "minimize",
                    "maximize",
                    "close",
                    "restore",
                    "minimize_all",
                    "focus",
                    "volume_up",
                    "volume_down",
                    "mute",
                ],
            },
            "target_window": {"type": "string", "description": "Заголовок окна (опционально)."},
        },
        ["action"],
    ),
    _tool_schema(
        "browser_inspect",
        "Инспекция DOM активной вкладки браузера через CDP ([index]<tag>...</tag>).",
        {"max_elements": {"type": "integer", "description": "Лимит элементов (по умолч. 80)."}},
    ),
    _tool_schema(
        "browser_click",
        "Клик по DOM-элементу браузера по его [index] из browser_inspect.",
        {"index": {"type": "integer", "description": "Индекс элемента DOM."}},
        ["index"],
    ),
    _tool_schema(
        "browser_type",
        "Ввод текста в поле браузера по [index] из browser_inspect.",
        {
            "index": {"type": "integer", "description": "Индекс поля ввода DOM."},
            "text": {"type": "string", "description": "Текст для ввода."},
            "press_enter": {"type": "boolean", "description": "Нажать Enter после ввода."},
        },
        ["index", "text"],
    ),
    _tool_schema(
        "browser_navigate",
        "Переход активной вкладки браузера по URL через CDP.",
        {"url": {"type": "string", "description": "Целевой URL страницы."}},
        ["url"],
    ),
    _tool_schema(
        "click_ui_element",
        "Клик по элементу окна Windows через Microsoft UI Automation.",
        {
            "element_name": {"type": "string", "description": "Имя или AutomationId элемента."},
            "window_title": {"type": "string", "description": "Заголовок окна (опционально)."},
            "control_type": {"type": "string", "description": "Тип контрола (Button, MenuItem)."},
        },
        ["element_name"],
    ),
    _tool_schema(
        "type_into_ui_element",
        "Ввод текста в поле окна через Microsoft UI Automation.",
        {
            "element_name": {"type": "string", "description": "Имя или AutomationId поля."},
            "text": {"type": "string", "description": "Текст для ввода."},
            "window_title": {"type": "string", "description": "Заголовок окна (опционально)."},
        },
        ["element_name", "text"],
    ),
    _tool_schema(
        "inspect_ui_elements",
        "Получение компактного дерева интерактивных элементов окна через UIA.",
        {
            "window_title": {"type": "string", "description": "Заголовок окна (опционально)."},
            "max_depth": {"type": "integer", "description": "Глубина обхода (по умолч. 6)."},
        },
    ),
    _tool_schema(
        "click_som",
        "Клик по метке [mark_id] Set-of-Mark на последнем аннотированном кадре.",
        {"mark_id": {"type": "integer", "description": "Номер метки SoM [1..N]."}},
        ["mark_id"],
    ),
    _tool_schema(
        "click_screen",
        "Клик мышью по координатам экрана.",
        {
            "x": {"type": "integer", "description": "Координата X."},
            "y": {"type": "integer", "description": "Координата Y."},
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
            "normalized": {"type": "boolean", "description": "True для [0..1000] координат."},
            "monitor_index": {"type": "integer", "description": "Индекс монитора (опционально)."},
        },
        ["x", "y"],
    ),
    _tool_schema(
        "type_text",
        "Ввод текста с клавиатуры в текущий фокус.",
        {
            "text": {"type": "string", "description": "Текст для ввода."},
            "press_enter": {"type": "boolean", "description": "Нажать Enter после ввода."},
        },
        ["text"],
    ),
    _tool_schema(
        "press_key",
        "Нажатие клавиши или комбинации (например, 'enter', 'ctrl+l', 'alt+tab').",
        {
            "key": {"type": "string", "description": "Клавиша или комбинация через '+'."},
            "presses": {"type": "integer", "description": "Количество нажатий."},
        },
        ["key"],
    ),
    _tool_schema(
        "scroll_screen",
        "Прокрутка колёсиком мыши (>0 вверх, <0 вниз).",
        {
            "clicks": {"type": "integer", "description": "Величина прокрутки."},
            "x": {"type": "integer", "description": "X координата (опционально)."},
            "y": {"type": "integer", "description": "Y координата (опционально)."},
        },
    ),
    _tool_schema(
        "wait_seconds",
        "Пауза ожидания (0..10 сек) с контролем Esc FailSafe.",
        {"seconds": {"type": "number", "description": "Длительность паузы в секундах."}},
    ),
    _tool_schema(
        "store_user_fact",
        "Сохранение факта о пользователе в долговременную память.",
        {
            "key": {"type": "string", "description": "Ключ факта."},
            "value": {"type": "string", "description": "Значение факта."},
            "category": {"type": "string", "description": "Категория факта."},
        },
        ["key", "value"],
    ),
    _tool_schema(
        "add_user_note",
        "Добавление новой заметки пользователя в память.",
        {"content": {"type": "string", "description": "Текст заметки."}},
        ["content"],
    ),
    _tool_schema(
        "finish_task",
        "Завершение автономной задачи с финальным кратким итогом на русском языке.",
        {
            "summary": {"type": "string", "description": "Краткий итог выполнения для ответа."},
            "success": {"type": "boolean", "description": "Успешно ли выполнена задача."},
        },
        ["summary"],
    ),
]


class ToolRegistry:
    """Типизированный диспетчер инструментов с Safety Gate и усечением вывода."""

    def __init__(
        self,
        os_controller: OSController | None = None,
        app_finder: AppFinder | None = None,
        uia_manager: UIAutomationManager | None = None,
        browser_bridge: BrowserCDPBridge | None = None,
        vision_agent: VisionAgent | None = None,
        som_annotator: SoMAnnotator | None = None,
        memory_manager: MemoryManager | None = None,
        monitor_manager: MonitorManager | None = None,
        failsafe_monitor: FailSafeMonitor | None = None,
        confirm_callback: Callable[[str, dict[str, Any], str], bool] | None = None,
        max_output_chars: int = MAX_OUTPUT_CHARS,
    ) -> None:
        """Инициализация реестра инструментов с опциональными зависимостями."""
        self.os_controller = os_controller or OSController()
        self.app_finder = app_finder or AppFinder()
        self.uia_manager = uia_manager
        self.browser_bridge = browser_bridge
        self.vision_agent = vision_agent
        self.som_annotator = som_annotator or SoMAnnotator()
        self.memory_manager = memory_manager
        self.monitor_manager = monitor_manager or MonitorManager()
        self.failsafe_monitor = failsafe_monitor or get_failsafe_monitor()
        self.confirm_callback = confirm_callback
        self.max_output_chars = max(100, min(MAX_OUTPUT_CHARS, max_output_chars))
        self._handlers: dict[str, Callable[[dict[str, Any]], ToolExecutionResult]] = {
            "open_application": self._tool_open_application,
            "open_url": self._tool_open_url,
            "search_web": self._tool_search_web,
            "run_command": self._tool_run_command,
            "window_control": self._tool_window_control,
            "browser_inspect": self._tool_browser_inspect,
            "browser_click": self._tool_browser_click,
            "browser_type": self._tool_browser_type,
            "browser_navigate": self._tool_browser_navigate,
            "click_ui_element": self._tool_click_ui_element,
            "type_into_ui_element": self._tool_type_into_ui_element,
            "inspect_ui_elements": self._tool_inspect_ui_elements,
            "click_som": self._tool_click_som,
            "click_screen": self._tool_click_screen,
            "type_text": self._tool_type_text,
            "press_key": self._tool_press_key,
            "scroll_screen": self._tool_scroll_screen,
            "wait_seconds": self._tool_wait_seconds,
            "store_user_fact": self._tool_store_user_fact,
            "add_user_note": self._tool_add_user_note,
            "finish_task": self._tool_finish_task,
        }

    @staticmethod
    def get_openai_tools() -> list[dict[str, Any]]:
        """Возвращает полный список OpenAI Function Calling схем."""
        return list(AGENT_TOOL_SCHEMAS)

    @staticmethod
    def infer_engine(tool_name: str) -> EngineType:
        """Определяет уровень движка для отображения прогресса карточки шага."""
        if tool_name in ("browser_inspect", "browser_click", "browser_type", "browser_navigate"):
            return "CDP"
        if tool_name in ("click_ui_element", "type_into_ui_element", "inspect_ui_elements"):
            return "UIA"
        if tool_name in ("click_som", "click_screen", "type_text", "press_key", "scroll_screen"):
            return "Vision"
        if tool_name == "finish_task":
            return "LLM"
        return "Fast-Path"

    def list_tools(self) -> list[str]:
        """Возвращает имена всех зарегистрированных инструментов."""
        return list(self._handlers.keys())

    def check_safety(self, tool_name: str, params: dict[str, Any]) -> tuple[bool, str]:
        """Проверяет действие через Safety Gate на деструктивность."""
        if tool_name == "run_command":
            command = str(params.get("command", ""))
            is_safe, reason = self.os_controller.shell.is_safe_command(command)
            if not is_safe:
                return False, f"Запрещённый шаблон команды: {reason}"
            normalized = self.os_controller.shell._normalize_command(command)
            for pattern in _EXTRA_DANGEROUS_PATTERNS:
                if re.search(pattern, normalized):
                    return False, f"Деструктивная системная команда: {pattern}"
            return True, ""

        if tool_name == "window_control" and str(params.get("action", "")) == "close":
            target = str(params.get("target_window", "")).strip().lower()
            if target in ("explorer", "program manager", "task manager", "диспетчер задач"):
                if self.confirm_callback and self.confirm_callback(
                    tool_name, params, f"Закрытие системного окна '{target}'"
                ):
                    return True, ""
                return False, f"Закрытие системного окна '{target}' заблокировано Safety Gate"

        return True, ""

    def execute(self, tool_name: str, params: dict[str, Any] | None = None) -> ToolExecutionResult:
        """Исполняет инструмент по имени с проверками безопасности и усечением вывода."""
        args = dict(params or {})
        engine = self.infer_engine(tool_name)
        handler = self._handlers.get(tool_name)
        if handler is None:
            return self._result(
                tool_name,
                False,
                f"Неизвестный инструмент: {tool_name}",
                engine=engine,
                error="unknown_tool",
            )

        self.failsafe_monitor.check_abort()
        is_safe, reason = self.check_safety(tool_name, args)
        if not is_safe:
            return self._result(
                tool_name,
                False,
                f"Отклонено Safety Gate: {reason}",
                engine=engine,
                error="safety_gate_blocked",
            )

        try:
            res = handler(args)
        except InterruptedError:
            raise
        except Exception as exc:
            logger.warning("Ошибка инструмента %s: %s", tool_name, exc)
            return self._result(
                tool_name,
                False,
                f"Ошибка выполнения {tool_name}: {exc}",
                engine=engine,
                error=str(exc),
            )

        return dataclasses.replace(res, output=truncate_output(res.output, self.max_output_chars))

    def _result(
        self,
        tool: str,
        success: bool,
        output: str,
        engine: EngineType | None = None,
        error: str = "",
        finished: bool = False,
    ) -> ToolExecutionResult:
        """Создаёт ToolExecutionResult с усечением вывода до <= max_output_chars."""
        return ToolExecutionResult(
            tool=tool,
            success=success,
            output=truncate_output(output, self.max_output_chars),
            engine=engine or self.infer_engine(tool),
            error=error,
            finished=finished,
        )

    @staticmethod
    def _launch_path(app_path: str) -> bool:
        """Безопасный запуск исполняемого файла или ярлыка без блокировки."""
        if hasattr(os, "startfile"):
            try:
                os.startfile(app_path)
                return True
            except OSError:
                pass
        try:
            kwargs: dict[str, Any] = {}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            subprocess.Popen([app_path], **kwargs)
            return True
        except OSError:
            return False

    def _tool_open_application(self, params: dict[str, Any]) -> ToolExecutionResult:
        app_name = str(params.get("app_name", "")).strip()
        if not app_name:
            return self._result("open_application", False, "Не указано имя приложения.")

        apps = self.app_finder.find(app_name)
        if apps and self._launch_path(apps[0].path):
            return self._result("open_application", True, f"Запущено приложение: {apps[0].name}.")

        alias_url = self.os_controller.resolve_alias(app_name)
        if alias_url and self.os_controller.open_url(alias_url):
            return self._result("open_application", True, f"Открыт веб-сервис: {alias_url}.")

        return self._result(
            "open_application",
            False,
            f"Приложение '{app_name}' не найдено.",
            error="not_found",
        )

    def _tool_open_url(self, params: dict[str, Any]) -> ToolExecutionResult:
        url = str(params.get("url", "")).strip()
        if not url:
            return self._result("open_url", False, "Пустой URL.")
        resolved = self.os_controller.resolve_alias(url) or url
        ok = self.os_controller.open_url(resolved)
        msg = f"Открыт URL: {resolved}." if ok else f"Не удалось открыть URL: {resolved}."
        return self._result("open_url", ok, msg)

    def _tool_search_web(self, params: dict[str, Any]) -> ToolExecutionResult:
        query = str(params.get("query", "")).strip()
        engine = str(params.get("engine", "google")).strip().lower() or "google"
        if not query:
            return self._result("search_web", False, "Пустой поисковый запрос.")
        ok = self.os_controller.search(query=query, engine=engine)
        msg = (
            f"Выполнен поиск в {engine}: '{query}'."
            if ok
            else f"Не удалось открыть поиск '{query}'."
        )
        return self._result("search_web", ok, msg)

    def _tool_run_command(self, params: dict[str, Any]) -> ToolExecutionResult:
        command = str(params.get("command", "")).strip()
        shell = str(params.get("shell", "powershell")).strip().lower()
        if not command:
            return self._result("run_command", False, "Пустая команда.")

        effective_cmd = f"cmd /c {command}" if shell == "cmd" else command
        code, stdout, stderr = self.os_controller.run_command(effective_cmd)
        combined = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
        if not combined:
            combined = f"Команда завершена (код {code})."
        return self._result(
            "run_command", code == 0, combined, error="" if code == 0 else f"exit={code}"
        )

    def _tool_window_control(self, params: dict[str, Any]) -> ToolExecutionResult:
        action = str(params.get("action", "")).strip().lower()
        target = str(params.get("target_window", "")).strip()
        if action == "volume_up":
            self.os_controller.volume_up()
            return self._result("window_control", True, "Громкость увеличена.")
        if action == "volume_down":
            self.os_controller.volume_down()
            return self._result("window_control", True, "Громкость уменьшена.")
        if action == "mute":
            self.os_controller.volume_mute()
            return self._result("window_control", True, "Переключен режим без звука.")
        if action == "minimize_all":
            ok = self.os_controller.minimize_all()
            return self._result("window_control", ok, "Все окна свернуты.")

        action_map: dict[str, Callable[[str], bool]] = {
            "minimize": self.os_controller.minimize_window,
            "maximize": self.os_controller.maximize_window,
            "close": self.os_controller.close_window,
            "focus": self.os_controller.focus_window,
            "restore": self.os_controller.focus_window,
        }
        fn = action_map.get(action)
        if fn is None:
            return self._result("window_control", False, f"Неизвестное действие окна: {action}")
        ok = fn(target)
        status_txt = "выполнено" if ok else "не найдено"
        msg = f"Действие '{action}' для окна '{target or 'активного'}': {status_txt}."
        return self._result("window_control", ok, msg)

    def _tool_browser_inspect(self, params: dict[str, Any]) -> ToolExecutionResult:
        if self.browser_bridge is None or not self.browser_bridge.is_available():
            return self._result(
                "browser_inspect", False, "Browser CDP недоступен.", error="cdp_unavailable"
            )
        max_el = int(params.get("max_elements", 80))
        summary = self.browser_bridge.get_page_summary(max_elements=max_el)
        text = f"URL: {summary.url}\nTitle: {summary.title}\n{summary.manifest}"
        return self._result("browser_inspect", True, text)

    def _tool_browser_click(self, params: dict[str, Any]) -> ToolExecutionResult:
        if self.browser_bridge is None or not self.browser_bridge.is_available():
            return self._result(
                "browser_click", False, "Browser CDP недоступен.", error="cdp_unavailable"
            )
        idx = int(params.get("index", -1))
        ok = self.browser_bridge.click_element(idx)
        return self._result(
            "browser_click",
            ok,
            f"CDP клик по элементу [{idx}]: {'успешно' if ok else 'не найден'}.",
        )

    def _tool_browser_type(self, params: dict[str, Any]) -> ToolExecutionResult:
        if self.browser_bridge is None or not self.browser_bridge.is_available():
            return self._result(
                "browser_type", False, "Browser CDP недоступен.", error="cdp_unavailable"
            )
        idx = int(params.get("index", -1))
        text = str(params.get("text", ""))
        press_enter = bool(params.get("press_enter", False))
        ok = self.browser_bridge.type_text(idx, text, press_enter=press_enter)
        return self._result(
            "browser_type", ok, f"CDP ввод в [{idx}]: {'успешно' if ok else 'ошибка'}."
        )

    def _tool_browser_navigate(self, params: dict[str, Any]) -> ToolExecutionResult:
        if self.browser_bridge is None or not self.browser_bridge.is_available():
            return self._result(
                "browser_navigate", False, "Browser CDP недоступен.", error="cdp_unavailable"
            )
        url = str(params.get("url", "")).strip()
        ok = self.browser_bridge.navigate(url)
        return self._result(
            "browser_navigate", ok, f"CDP переход на {url}: {'успешно' if ok else 'ошибка'}."
        )

    def _tool_click_ui_element(self, params: dict[str, Any]) -> ToolExecutionResult:
        if self.uia_manager is None:
            return self._result("click_ui_element", False, "UIA менеджер не инициализирован.")
        name = str(params.get("element_name", "")).strip()
        win = str(params.get("window_title", "")).strip() or None
        ctype = str(params.get("control_type", "")).strip() or None
        ok = self.uia_manager.click_element(name, window_title=win, control_type=ctype)
        return self._result(
            "click_ui_element",
            ok,
            f"UIA клик '{name}': {'успешно' if ok else 'элемент не найден'}.",
        )

    def _tool_type_into_ui_element(self, params: dict[str, Any]) -> ToolExecutionResult:
        if self.uia_manager is None:
            return self._result("type_into_ui_element", False, "UIA менеджер не инициализирован.")
        name = str(params.get("element_name", "")).strip()
        text = str(params.get("text", ""))
        win = str(params.get("window_title", "")).strip() or None
        ok = self.uia_manager.type_into_element(name, text, window_title=win)
        return self._result(
            "type_into_ui_element", ok, f"UIA ввод в '{name}': {'успешно' if ok else 'ошибка'}."
        )

    def _tool_inspect_ui_elements(self, params: dict[str, Any]) -> ToolExecutionResult:
        if self.uia_manager is None:
            return self._result("inspect_ui_elements", False, "UIA менеджер не инициализирован.")
        win = str(params.get("window_title", "")).strip() or None
        depth = int(params.get("max_depth", 6))
        monitor = self.monitor_manager.get_active_monitor()
        items = self.uia_manager.inspect_window_elements(
            window_title=win, max_depth=depth, monitor=monitor
        )
        if not items:
            return self._result(
                "inspect_ui_elements", False, "Интерактивные UIA-элементы не найдены."
            )
        lines = [
            f"[{idx}] {el.get('control_type', 'Element')} "
            f'"{el.get("name") or el.get("automation_id")}" '
            f"@ {el.get('norm_center', el.get('global_center', (0, 0)))}"
            for idx, el in enumerate(items[:60], start=1)
        ]
        return self._result("inspect_ui_elements", True, "\n".join(lines))

    def _tool_click_som(self, params: dict[str, Any]) -> ToolExecutionResult:
        if self.vision_agent is None:
            return self._result("click_som", False, "VisionAgent не инициализирован.")
        mark_id = int(params.get("mark_id", 0))
        som_el = self.som_annotator.get_element(mark_id)
        if som_el is None:
            return self._result(
                "click_som", False, f"Метка SoM [{mark_id}] не найдена в последнем кадре."
            )
        ok = self.vision_agent.click_at(
            som_el.global_x,
            som_el.global_y,
            normalized=False,
            verify_change=False,
            monitor_index=som_el.monitor_index,
        )
        return self._result(
            "click_som",
            ok,
            f"SoM клик [{mark_id}] '{som_el.name}' @ ({som_el.global_x}, {som_el.global_y}).",
        )

    def _tool_click_screen(self, params: dict[str, Any]) -> ToolExecutionResult:
        if self.vision_agent is None:
            return self._result("click_screen", False, "VisionAgent не инициализирован.")
        x = int(params.get("x", 0))
        y = int(params.get("y", 0))
        button = str(params.get("button", "left"))
        normalized = bool(params.get("normalized", True))
        mon_raw = params.get("monitor_index")
        monitor_index = int(mon_raw) if mon_raw is not None else None
        ok = self.vision_agent.click_at(
            x,
            y,
            button=button,
            verify_change=False,
            normalized=normalized,
            monitor_index=monitor_index,
        )
        return self._result("click_screen", ok, f"Клик ({x}, {y}) [normalized={normalized}].")

    def _tool_type_text(self, params: dict[str, Any]) -> ToolExecutionResult:
        if self.vision_agent is None:
            return self._result("type_text", False, "VisionAgent не инициализирован.")
        text = str(params.get("text", ""))
        press_enter = bool(params.get("press_enter", False))
        self.vision_agent.type_text(text)
        if press_enter:
            self.vision_agent.press_key("enter")
        return self._result("type_text", True, f"Введён текст ({len(text)} симв.).")

    def _tool_press_key(self, params: dict[str, Any]) -> ToolExecutionResult:
        if self.vision_agent is None:
            return self._result("press_key", False, "VisionAgent не инициализирован.")
        key = str(params.get("key", "")).strip()
        presses = max(1, int(params.get("presses", 1)))
        if not key:
            return self._result("press_key", False, "Не указана клавиша.")
        if "+" in key:
            parts = [part.strip().lower() for part in key.split("+") if part.strip()]
            self.vision_agent.hotkey(*parts)
        else:
            self.vision_agent.press_key(key.lower(), presses=presses)
        return self._result("press_key", True, f"Нажата клавиша '{key}'.")

    def _tool_scroll_screen(self, params: dict[str, Any]) -> ToolExecutionResult:
        if self.vision_agent is None:
            return self._result("scroll_screen", False, "VisionAgent не инициализирован.")
        clicks = int(params.get("clicks", -3))
        x_val = params.get("x")
        y_val = params.get("y")
        with safe_execution(self.failsafe_monitor):
            if x_val is not None and y_val is not None:
                self.vision_agent._pyautogui.scroll(clicks, x=int(x_val), y=int(y_val))
            else:
                self.vision_agent._pyautogui.scroll(clicks)
        return self._result("scroll_screen", True, f"Прокрутка экрана: {clicks}.")

    def _tool_wait_seconds(self, params: dict[str, Any]) -> ToolExecutionResult:
        seconds = max(0.0, min(_MAX_WAIT_SECONDS, float(params.get("seconds", 1.0))))
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.failsafe_monitor.check_abort()
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        return self._result("wait_seconds", True, f"Ожидание {seconds:.1f} с завершено.")

    def _tool_store_user_fact(self, params: dict[str, Any]) -> ToolExecutionResult:
        if self.memory_manager is None:
            return self._result("store_user_fact", False, "MemoryManager не инициализирован.")
        key = str(params.get("key", "")).strip()
        val = str(params.get("value", "")).strip()
        cat = str(params.get("category", "general")).strip() or "general"
        self.memory_manager.set_fact(key, val, category=cat)
        return self._result("store_user_fact", True, f"Факт сохранён: {key} = {val}.")

    def _tool_add_user_note(self, params: dict[str, Any]) -> ToolExecutionResult:
        if self.memory_manager is None:
            return self._result("add_user_note", False, "MemoryManager не инициализирован.")
        content = str(params.get("content", "")).strip()
        raw_tags = params.get("tags")
        tags = [str(t) for t in raw_tags] if isinstance(raw_tags, list) else None
        note_id = self.memory_manager.add_note(content, tags=tags)
        return self._result("add_user_note", True, f"Заметка #{note_id} добавлена.")

    def _tool_finish_task(self, params: dict[str, Any]) -> ToolExecutionResult:
        summary = str(params.get("summary", "")).strip() or "Задача выполнена."
        success = bool(params.get("success", True))
        return self._result("finish_task", success, summary, engine="LLM", finished=True)
