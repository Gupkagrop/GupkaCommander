"""Клиент взаимодействия с локальным сервером LLM/VLM по OpenAI-совместимому API.

Поддерживает:
- Работу с llama-server (Qwen2.5-VL / Qwen2.5-3B) на GPU.
- Спецификацию 8 системных инструментов (Function Calling).
- Мультимодальный ввод (текст + base64 изображение рабочего стола).
- Встроенный резервный движок правил (FallbackRuleEngine) при недоступности сервера.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterator
from typing import Any

import requests

from core.routing.intent_router import Intent, IntentRouter, RouteResult

logger = logging.getLogger(__name__)

SYSTEM_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "open_application",
            "description": "Запуск установленного приложения Windows по имени или алиасу.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Имя приложения или файла (например, telegram, notepad).",
                    }
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Открытие URL-адреса или веб-сайта в браузере по умолчанию.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Полный адрес страницы или веб-алиас (например, https://ya.ru).",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Поиск информации в интернете через поисковые системы.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Поисковый запрос.",
                    },
                    "engine": {
                        "type": "string",
                        "enum": ["google", "yandex", "youtube"],
                        "description": "Поисковая система (по умолчанию google).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Выполнение безопасной команды PowerShell или командной строки Windows.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Строка команды для выполнения.",
                    },
                    "shell": {
                        "type": "string",
                        "enum": ["powershell", "cmd"],
                        "description": "Оболочка для выполнения команды (по умолчанию powershell).",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "window_control",
            "description": "Управление состоянием окон и системной громкостью звука.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "minimize",
                            "maximize",
                            "close",
                            "restore",
                            "minimize_all",
                            "volume_up",
                            "volume_down",
                            "mute",
                        ],
                        "description": "Действие с окном или системной громкостью.",
                    },
                    "target_window": {
                        "type": "string",
                        "description": "Заголовок целевого окна (по умолчанию активное окно).",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_screen",
            "description": "Визуальный клик мыши по координатам на экране.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "Координата X в пикселях."},
                    "y": {"type": "integer", "description": "Координата Y в пикселях."},
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "description": "Кнопка мыши (по умолчанию left).",
                    },
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Ввод текста с клавиатуры в активный текстовый элемент.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Вводимый текст."},
                    "press_enter": {
                        "type": "boolean",
                        "description": "Нажать ли Enter после ввода текста.",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_screen",
            "description": "Анализ текущего состояния экрана и обнаружение элементов интерфейса.",
            "parameters": {
                "type": "object",
                "properties": {
                    "focus_region": {
                        "type": "string",
                        "description": "Область экрана или целевой элемент для поиска.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_ui_element",
            "description": "Клик по элементу интерфейса Windows через Microsoft UI Automation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "element_name": {
                        "type": "string",
                        "description": "Имя или текст элемента UI (кнопка, меню, ссылка).",
                    },
                    "window_title": {
                        "type": "string",
                        "description": "Заголовок целевого окна (по умолчанию активное окно).",
                    },
                    "control_type": {
                        "type": "string",
                        "description": "Тип элемента (Button, MenuItem, Hyperlink).",
                    },
                },
                "required": ["element_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_into_ui_element",
            "description": "Ввод текста в текстовое поле окна через Microsoft UI Automation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "element_name": {
                        "type": "string",
                        "description": "Название или идентификатор поля ввода.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Вводимый текст.",
                    },
                    "window_title": {
                        "type": "string",
                        "description": "Заголовок целевого окна (по умолчанию активное окно).",
                    },
                },
                "required": ["element_name", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_ui_elements",
            "description": "Инспекция дерева интерактивных элементов интерфейса через UIA.",
            "parameters": {
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Заголовок целевого окна (по умолчанию активное окно).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "store_user_fact",
            "description": "Сохранение структурированного факта или предпочтения в память.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Уникальный ключ факта (например, 'редактор', 'город').",
                    },
                    "value": {
                        "type": "string",
                        "description": "Значение факта (например, 'VS Code', 'Москва').",
                    },
                    "category": {
                        "type": "string",
                        "description": "Категория факта (по умолчанию 'general').",
                    },
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retrieve_user_facts",
            "description": "Получение сохраненных фактов и предпочтений пользователя из памяти.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Категория фактов для фильтрации (или пустая строка).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_user_note",
            "description": "Создание новой текстовой заметки или напоминания пользователя.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Содержимое заметки или задачи.",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_user_notes",
            "description": "Просмотр списка заметок пользователя с возможностью поиска.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Поисковый запрос по заметкам (или пустая строка).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_user_note",
            "description": "Удаление заметки пользователя по числовому идентификатору.",
            "parameters": {
                "type": "object",
                "properties": {
                    "note_id": {
                        "type": "integer",
                        "description": "Числовой идентификатор удаляемой заметки.",
                    },
                },
                "required": ["note_id"],
            },
        },
    },
]


class FallbackRuleEngine:
    """Резервный движок детерминированных правил при недоступности локального LLM."""

    def __init__(self, router: IntentRouter | None = None) -> None:
        """Инициализация резервного движка правил."""
        self.router: IntentRouter = router or IntentRouter()

    def plan(self, prompt: str) -> list[dict[str, Any]]:
        """Формирование списка действий по правилам при отключенном LLM."""
        route = self.router.route(prompt)

        if route.intent == Intent.OPEN_APP:
            return [{"tool": "open_application", "parameters": {"app_name": route.target}}]

        if route.intent == Intent.OPEN_URL:
            return [{"tool": "open_url", "parameters": {"url": route.target}}]

        if route.intent == Intent.SEARCH_WEB:
            engine = route.params.get("engine", "google")
            return [{"tool": "search_web", "parameters": {"query": route.target, "engine": engine}}]

        if route.intent == Intent.RUN_COMMAND:
            cmd = route.params.get("command", route.target)
            shell = route.params.get("shell", "powershell")
            return [{"tool": "run_command", "parameters": {"command": cmd, "shell": shell}}]

        if route.intent == Intent.WINDOW_CONTROL:
            action = route.params.get("action", "minimize")
            return [{"tool": "window_control", "parameters": {"action": action}}]

        if route.intent == Intent.VOLUME_CONTROL:
            vol_act = route.params.get("action", "up")
            action_map = {"up": "volume_up", "down": "volume_down", "mute": "mute"}
            return [
                {
                    "tool": "window_control",
                    "parameters": {"action": action_map.get(vol_act, "volume_up")},
                }
            ]

        if route.intent == Intent.UI_AUTOMATION:
            return self._plan_ui_automation_fallback(route)

        if route.intent == Intent.VISION_INTERACT:
            return self._plan_vision_fallback(route.params.get("action", ""), route.target)

        if route.intent == Intent.MEMORY:
            return self._plan_memory_fallback(route)

        return []

    def _plan_ui_automation_fallback(self, route: RouteResult) -> list[dict[str, Any]]:
        """Формирование запасных действий UI Automation по семантическим правилам."""
        action = route.params.get("action", "click")
        elem = str(route.params.get("element_name", route.target))
        win = str(route.params.get("window_title", ""))

        if action in ("click", "click_element"):
            ctrl = str(route.params.get("control_type", ""))
            return [
                {
                    "tool": "click_ui_element",
                    "parameters": {
                        "element_name": elem,
                        "window_title": win,
                        "control_type": ctrl,
                    },
                }
            ]

        if action in ("type", "type_text", "type_into_element"):
            txt = str(route.params.get("text", ""))
            return [
                {
                    "tool": "type_into_ui_element",
                    "parameters": {
                        "element_name": elem,
                        "text": txt,
                        "window_title": win,
                    },
                }
            ]

        if action in ("set_toggle", "toggle", "check", "uncheck"):
            return [
                {
                    "tool": "click_ui_element",
                    "parameters": {
                        "element_name": elem,
                        "window_title": win,
                        "control_type": "CheckBox",
                    },
                }
            ]

        if action == "inspect":
            return [
                {
                    "tool": "inspect_ui_elements",
                    "parameters": {"window_title": win},
                }
            ]

        return [
            {
                "tool": "click_ui_element",
                "parameters": {"element_name": elem, "window_title": win, "control_type": ""},
            }
        ]

    def _plan_vision_fallback(self, action: str, target: str) -> list[dict[str, Any]]:
        """Формирование запасных зрительных действий."""
        if action == "click_screen":
            return [{"tool": "click_screen", "parameters": {"x": 0, "y": 0, "button": "left"}}]
        if action == "type_text":
            return [{"tool": "type_text", "parameters": {"text": target, "press_enter": True}}]
        return [{"tool": "inspect_screen", "parameters": {}}]

    def _plan_memory_fallback(self, route: RouteResult) -> list[dict[str, Any]]:
        """Формирование запасных действий долговременной памяти."""
        action = str(route.params.get("action", ""))
        if action in ("store_fact", "save_fact", "store_user_fact"):
            return [
                {
                    "tool": "store_user_fact",
                    "parameters": {
                        "key": str(route.params.get("key", "")),
                        "value": str(route.params.get("value", "")),
                        "category": str(route.params.get("category", "general")),
                    },
                }
            ]
        if action in ("retrieve_facts", "list_facts", "retrieve_user_facts"):
            return [
                {
                    "tool": "retrieve_user_facts",
                    "parameters": {"category": str(route.params.get("category", ""))},
                }
            ]
        if action in ("add_note", "create_note", "add_user_note"):
            return [
                {
                    "tool": "add_user_note",
                    "parameters": {"content": str(route.params.get("content", route.target))},
                }
            ]
        if action in ("list_notes", "list_user_notes"):
            return [
                {
                    "tool": "list_user_notes",
                    "parameters": {"query": str(route.params.get("query", ""))},
                }
            ]
        if action in ("delete_note", "delete_user_note"):
            return [
                {
                    "tool": "delete_user_note",
                    "parameters": {"note_id": int(route.params.get("note_id", 0))},
                }
            ]
        return []

    def respond(self, prompt: str) -> str:
        """Формирование текстового ответа пользователю по правилам."""
        route = self.router.route(prompt)

        if route.intent == Intent.OPEN_APP:
            return f"Выполняю: запуск приложения {route.target}."

        if route.intent == Intent.OPEN_URL:
            return f"Открываю веб-ссылку: {route.target}."

        if route.intent == Intent.SEARCH_WEB:
            engine = route.params.get("engine", "интернете")
            return f"Выполняю поиск в {engine}: {route.target}."

        if route.intent == Intent.RUN_COMMAND:
            cmd = route.params.get("command", route.target)
            shell = route.params.get("shell", "терминале")
            return f"Выполняю команду '{cmd}' в {shell}."

        if route.intent == Intent.WINDOW_CONTROL:
            return f"Выполняю управление окном: {route.params.get('action', '')}."

        if route.intent == Intent.VOLUME_CONTROL:
            return "Настройка громкости выполнена."

        if route.intent == Intent.VISION_INTERACT:
            return "Анализирую состояние экрана в автономном режиме."

        if route.intent == Intent.UI_AUTOMATION:
            action = route.params.get("action", "click")
            elem = str(route.params.get("element_name", route.target))
            win = str(route.params.get("window_title", ""))
            win_str = f" в окне '{win}'" if win else ""
            if action in ("click", "click_element"):
                return f"Выполняю клик по элементу '{elem}'{win_str}."
            if action in ("type", "type_text", "type_into_element"):
                txt = str(route.params.get("text", ""))
                return f"Ввожу текст '{txt}' в элемент '{elem}'{win_str}."
            if action in ("set_toggle", "toggle", "check", "uncheck"):
                state_str = "включение" if route.params.get("text") == "true" else "отключение"
                return f"Выполняю {state_str} переключателя '{elem}'{win_str}."
            return f"Выполняю действие UI Automation с элементом '{elem}'{win_str}."

        if route.intent == Intent.MEMORY:
            action = str(route.params.get("action", ""))
            if action in ("store_fact", "save_fact", "store_user_fact"):
                k = route.params.get("key", "")
                v = route.params.get("value", "")
                return f"Запомнил: {k} — это {v}."
            if action in ("retrieve_facts", "list_facts", "retrieve_user_facts"):
                return "Запрашиваю сохраненные факты из памяти."
            if action in ("add_note", "create_note", "add_user_note"):
                c = route.params.get("content", route.target)
                return f"Заметка сохранена: {c}."
            if action in ("list_notes", "list_user_notes"):
                return "Читаю список ваших заметок."
            if action in ("delete_note", "delete_user_note"):
                nid = route.params.get("note_id", "")
                return f"Заметка #{nid} удалена."
            if action in ("complete_note", "close_note"):
                nid = route.params.get("note_id", "")
                return f"Заметка #{nid} выполнена."

        lower = prompt.strip().lower()
        if any(w in lower for w in ("привет", "здравствуй", "добрый день")):
            return "Привет! Я локальный оператор GupkaCommander. Чем помочь?"

        return f"Локальный ИИ-сервер недоступен. Запрос '{prompt}' принят в автономном режиме."


DEFAULT_MODEL_NAME: str = "qwen3-vl-4b-instruct"
QWEN35_MODEL_NAME: str = "qwen3.5-4b"


class ThinkTagFilter:
    """Потоковый фильтр тегов внутренних рассуждений `<think>...</think>`.

    Корректно вырезает блоки рассуждений даже в случае разбиения открывающего
    или закрывающего тега между несколькими SSE-чанками.
    """

    _OPEN_TAG: str = "<think>"
    _CLOSE_TAG: str = "</think>"

    def __init__(self) -> None:
        """Инициализация состояния потокового фильтра."""
        self._in_think: bool = False
        self._buffer: str = ""
        self._has_emitted: bool = False
        self._trim_leading_ws: bool = False

    @staticmethod
    def _trailing_tag_prefix_len(text_lower: str, tag: str) -> int:
        """Возвращает длину суффикса строки, совпадающего с префиксом тега."""
        max_len: int = min(len(text_lower), len(tag) - 1)
        for length in range(max_len, 0, -1):
            if text_lower.endswith(tag[:length]):
                return length
        return 0

    def _consume_in_think(self, lower_buf: str) -> bool:
        """Обрабатывает буфер в режиме рассуждений. Возвращает True для продолжения цикла."""
        close_idx: int = lower_buf.find(self._CLOSE_TAG)
        if close_idx != -1:
            self._buffer = self._buffer[close_idx + len(self._CLOSE_TAG) :]
            self._in_think = False
            if not self._has_emitted:
                self._trim_leading_ws = True
            return True

        keep: int = self._trailing_tag_prefix_len(lower_buf, self._CLOSE_TAG)
        self._buffer = self._buffer[len(self._buffer) - keep :] if keep else ""
        return False

    def feed(self, chunk: str) -> str:
        """Фильтрует очередной чанк текста и возвращает безопасную для показа/TTS часть."""
        if not chunk:
            return ""

        self._buffer += chunk
        out_parts: list[str] = []

        while self._buffer:
            lower_buf: str = self._buffer.lower()
            if self._in_think:
                if self._consume_in_think(lower_buf):
                    continue
                break

            if self._trim_leading_ws:
                stripped = self._buffer.lstrip()
                if not stripped:
                    self._buffer = ""
                    break
                self._buffer = stripped
                self._trim_leading_ws = False
                lower_buf = self._buffer.lower()

            open_idx: int = lower_buf.find(self._OPEN_TAG)
            if open_idx != -1:
                visible = self._buffer[:open_idx]
                if visible:
                    out_parts.append(visible)
                    self._has_emitted = True
                self._buffer = self._buffer[open_idx + len(self._OPEN_TAG) :]
                self._in_think = True
                continue

            keep: int = self._trailing_tag_prefix_len(lower_buf, self._OPEN_TAG)
            split_at: int = len(self._buffer) - keep
            visible = self._buffer[:split_at]
            if visible:
                out_parts.append(visible)
                self._has_emitted = True
            self._buffer = self._buffer[split_at:]
            break

        return "".join(out_parts)

    def flush(self) -> str:
        """Сбрасывает оставшийся незадержанный текст после завершения потока."""
        if self._in_think:
            self._buffer = ""
            return ""
        remaining: str = self._buffer.lstrip() if self._trim_leading_ws else self._buffer
        self._buffer = ""
        self._trim_leading_ws = False
        if remaining:
            self._has_emitted = True
        return remaining


def strip_thinking_tags(text: str) -> str:
    """Удаляет блоки `<think>...</think>` из готового текстового ответа модели."""
    if not text:
        return ""
    tag_filter = ThinkTagFilter()
    cleaned: str = tag_filter.feed(text) + tag_filter.flush()
    return cleaned.strip()


class LocalLLMClient:
    """OpenAI-совместимый клиент для локального инференса (llama-server/vLLM)."""

    def __init__(
        self,
        api_base: str | None = None,
        model_name: str | None = None,
        timeout_seconds: float = 30.0,
        router: IntentRouter | None = None,
        session: requests.Session | None = None,
        enable_thinking: bool = False,
    ) -> None:
        """Инициализация клиента локального LLM.

        Args:
            api_base: URL-адрес сервера API (по умолчанию http://127.0.0.1:8080/v1).
            model_name: Модель инференса (из LOCAL_LLM_MODEL или qwen3-vl-4b-instruct).
            timeout_seconds: Таймаут сетевых запросов в секундах.
            router: Экземпляр IntentRouter для резервной логики.
            session: Сессия requests (создается новая при отсутствии).
            enable_thinking: Разрешать ли модели режим внутренних рассуждений.
        """
        env_base = os.getenv("LOCAL_LLM_API_BASE", "http://127.0.0.1:8080/v1")
        selected_base = api_base if api_base is not None else env_base
        env_model = os.getenv("LOCAL_LLM_MODEL", DEFAULT_MODEL_NAME)
        self.api_base: str = selected_base.rstrip("/")
        self.model_name: str = model_name if model_name is not None else env_model
        self.timeout_seconds: float = timeout_seconds
        self.session: requests.Session = session or requests.Session()
        self.router: IntentRouter = router or IntentRouter()
        self.fallback_engine: FallbackRuleEngine = FallbackRuleEngine(router=self.router)
        self.tools: list[dict[str, Any]] = SYSTEM_TOOLS
        self.enable_thinking: bool = enable_thinking

    def is_healthy(self) -> bool:
        """Проверка доступности локального инференс-сервера."""
        try:
            url = f"{self.api_base}/models"
            resp = self.session.get(url, timeout=min(1.5, self.timeout_seconds))
            return resp.status_code == 200
        except requests.RequestException:
            return False

    @staticmethod
    def _build_messages(
        system_prompt: str,
        user_content: Any,
        history: list[dict[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        """Формирует массив сообщений с опциональной многоходовой историей диалога."""
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        if history:
            for item in history:
                role: str = str(item.get("role", "")).strip()
                content: str = str(item.get("content", "")).strip()
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_content})
        return messages

    def _apply_thinking_option(
        self,
        payload: dict[str, Any],
        enable_thinking: bool | None,
    ) -> None:
        """Добавляет chat_template_kwargs для отключения thinking-режима Qwen3.5."""
        use_thinking: bool = self.enable_thinking if enable_thinking is None else enable_thinking
        if not use_thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": False}

    def generate_response(
        self,
        prompt: str,
        system_prompt: str | None = None,
        history: list[dict[str, str]] | None = None,
        enable_thinking: bool | None = None,
    ) -> str:
        """Генерация текстового ответа на запрос пользователя.

        При недоступности сервера переключается на FallbackRuleEngine.
        """
        sys_prompt = system_prompt or (
            "Ты автономный оператор Windows 11 GupkaCommander. Отвечай кратко на русском языке."
        )
        url = f"{self.api_base}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": self._build_messages(sys_prompt, prompt, history=history),
            "temperature": 0.3,
            "max_tokens": 512,
        }
        self._apply_thinking_option(payload, enable_thinking)

        try:
            resp = self.session.post(url, json=payload, timeout=self.timeout_seconds)
            if resp.status_code == 200:
                data: dict[str, Any] = resp.json()
                choices: list[dict[str, Any]] = data.get("choices", [])
                if choices:
                    raw_content = choices[0].get("message", {}).get("content")
                    if isinstance(raw_content, str):
                        cleaned = strip_thinking_tags(raw_content)
                        if cleaned:
                            return cleaned
        except requests.RequestException as err:
            logger.warning("Локальный LLM недоступен (%s). Переключение на fallback.", err)

        return self.fallback_engine.respond(prompt)

    def _parse_sse_line(self, raw_line: str | bytes) -> tuple[str | None, bool]:
        """Парсинг отдельной строки Server-Sent Events (SSE).

        Игнорирует поле `reasoning_content` для предотвращения озвучивания рассуждений.

        Returns:
            tuple[str | None, bool]: (текстовый фрагмент дельты, флаг окончания [DONE]).
        """
        line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8")
        line = line.strip()
        if not line or not line.startswith("data:"):
            return None, False

        data_str = line[5:].strip()
        if data_str == "[DONE]":
            return None, True

        try:
            data_json: dict[str, Any] = json.loads(data_str)
            choices: list[dict[str, Any]] = data_json.get("choices", [])
            if not choices:
                return None, False
            delta: dict[str, Any] = choices[0].get("delta", {})
            raw_content = delta.get("content")
            content: str = raw_content if isinstance(raw_content, str) else ""
            return (content if content else None), False
        except (json.JSONDecodeError, TypeError, KeyError):
            return None, False

    def _read_sse_stream(self, resp: requests.Response) -> Iterator[str]:
        """Построчное чтение и фильтрация `<think>`-тегов в потоке SSE."""
        resp.encoding = "utf-8"
        tag_filter = ThinkTagFilter()
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            chunk, is_done = self._parse_sse_line(raw_line)
            if is_done:
                break
            if chunk is not None:
                filtered = tag_filter.feed(chunk)
                if filtered:
                    yield filtered

        tail = tag_filter.flush()
        if tail:
            yield tail

    def _fallback_stream(self, text: str) -> Iterator[str]:
        """Генерация потока токенов из запасного ответа движка правил."""
        response = self.fallback_engine.respond(text)
        tokens = re.findall(r"\S+\s*", response)
        yield from tokens

    def generate_response_stream(
        self,
        user_text: str,
        system_prompt: str | None = None,
        history: list[dict[str, str]] | None = None,
        enable_thinking: bool | None = None,
    ) -> Iterator[str]:
        """Потоковая генерация ответа через Server-Sent Events (SSE).

        При ошибке или недоступности сервера переключается на FallbackRuleEngine.

        Args:
            user_text: Текст запроса пользователя.
            system_prompt: Опциональный системный промпт.
            history: Опциональная история предыдущих ходов диалога.
            enable_thinking: Локальное переопределение thinking-режима.

        Yields:
            str: Текстовые токены ответа по мере их генерации.
        """
        clean_text = user_text.strip()
        if not clean_text:
            return

        sys_prompt = system_prompt or (
            "Ты автономный оператор Windows 11 GupkaCommander. Отвечай кратко на русском языке."
        )
        url = f"{self.api_base}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": self._build_messages(sys_prompt, clean_text, history=history),
            "temperature": 0.3,
            "max_tokens": 512,
            "stream": True,
        }
        self._apply_thinking_option(payload, enable_thinking)

        streamed = False
        try:
            resp = self.session.post(
                url,
                json=payload,
                timeout=self.timeout_seconds,
                stream=True,
            )
            if resp.status_code == 200:
                for chunk in self._read_sse_stream(resp):
                    streamed = True
                    yield chunk
                if streamed:
                    return
        except requests.RequestException as err:
            logger.warning("Ошибка потокового LLM запроса (%s). Запуск fallback.", err)
        except Exception as exc:
            logger.warning("Сбой потокового ответа LLM (%s). Запуск fallback.", exc)

        if not streamed:
            yield from self._fallback_stream(clean_text)

    def plan_actions(
        self,
        prompt: str,
        image_b64: str | None = None,
        system_prompt: str | None = None,
        history: list[dict[str, str]] | None = None,
        enable_thinking: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Генерация плана системных действий (Tool Calling) по промпту и кадру экрана.

        При недоступности сервера переключается на FallbackRuleEngine.
        """
        url = f"{self.api_base}/chat/completions"
        user_content: Any
        if image_b64 is not None:
            user_content = [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                },
            ]
        else:
            user_content = prompt

        sys_prompt = system_prompt or (
            "Ты голосовой ассистент и планировщик действий ОС Windows. "
            "Всегда выбирай подходящий инструмент (function calling), когда пользователь "
            "просит выполнить действие на компьютере или в интернете. "
            "Если пользователь просит включить, найти или открыть видео/музыку на YouTube "
            "(включая 'открой прикольное видео на ютуб на свой вкус'), обязательно вызывай "
            "search_web с engine='youtube' и конкретным интересным поисковым запросом."
        )

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": self._build_messages(sys_prompt, user_content, history=history),
            "tools": self.tools,
            "tool_choice": "auto",
            "temperature": 0.1,
            "max_tokens": 1024,
        }
        self._apply_thinking_option(payload, enable_thinking)

        try:
            resp = self.session.post(url, json=payload, timeout=self.timeout_seconds)
            if resp.status_code == 200:
                actions = self._parse_tool_calls_from_response(resp.json())
                if actions:
                    return actions
        except requests.RequestException as err:
            logger.warning("Ошибка планирования через LLM (%s). Переключение на fallback.", err)

        return self.fallback_engine.plan(prompt)

    def _parse_tool_calls_from_response(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Парсинг вызовов инструментов из ответа OpenAI Chat Completions."""
        choices: list[dict[str, Any]] = data.get("choices", [])
        if not choices:
            return []

        message: dict[str, Any] = choices[0].get("message", {})
        tool_calls: list[dict[str, Any]] = message.get("tool_calls", [])
        if not tool_calls:
            return []

        actions: list[dict[str, Any]] = []
        for call in tool_calls:
            fn = call.get("function", {})
            name = str(fn.get("name", ""))
            raw_args = fn.get("arguments", {})
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except Exception:
                    args = {"raw": raw_args}
            elif isinstance(raw_args, dict):
                args = raw_args
            else:
                args = {}
            actions.append({"tool": name, "parameters": args})

        return actions
