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
from typing import Any

import requests

from core.routing.intent_router import Intent, IntentRouter

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

        if route.intent == Intent.VISION_INTERACT:
            return self._plan_vision_fallback(route.params.get("action", ""), route.target)

        return []

    def _plan_vision_fallback(self, action: str, target: str) -> list[dict[str, Any]]:
        """Формирование запасных зрительных действий."""
        if action == "click_screen":
            return [{"tool": "click_screen", "parameters": {"x": 0, "y": 0, "button": "left"}}]
        if action == "type_text":
            return [{"tool": "type_text", "parameters": {"text": target, "press_enter": True}}]
        return [{"tool": "inspect_screen", "parameters": {}}]

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

        lower = prompt.strip().lower()
        if any(w in lower for w in ("привет", "здравствуй", "добрый день")):
            return "Привет! Я локальный оператор GupkaCommander. Чем помочь?"

        return f"Локальный ИИ-сервер недоступен. Запрос '{prompt}' принят в автономном режиме."


class LocalLLMClient:
    """OpenAI-совместимый клиент для локального инференса (llama-server/vLLM)."""

    def __init__(
        self,
        api_base: str | None = None,
        model_name: str = "qwen2.5-vl-3b-instruct",
        timeout_seconds: float = 5.0,
        router: IntentRouter | None = None,
        session: requests.Session | None = None,
    ) -> None:
        """Инициализация клиента локального LLM.

        Args:
            api_base: URL-адрес сервера API (по умолчанию http://127.0.0.1:8080/v1).
            model_name: Название используемой модели инференса.
            timeout_seconds: Таймаут сетевых запросов в секундах.
            router: Экземпляр IntentRouter для резервной логики.
            session: Сессия requests (создается новая при отсутствии).
        """
        env_base = os.getenv("LOCAL_LLM_API_BASE", "http://127.0.0.1:8080/v1")
        selected_base = api_base if api_base is not None else env_base
        self.api_base: str = selected_base.rstrip("/")
        self.model_name: str = model_name
        self.timeout_seconds: float = timeout_seconds
        self.session: requests.Session = session or requests.Session()
        self.router: IntentRouter = router or IntentRouter()
        self.fallback_engine: FallbackRuleEngine = FallbackRuleEngine(router=self.router)
        self.tools: list[dict[str, Any]] = SYSTEM_TOOLS

    def is_healthy(self) -> bool:
        """Проверка доступности локального инференс-сервера."""
        try:
            url = f"{self.api_base}/models"
            resp = self.session.get(url, timeout=min(2.0, self.timeout_seconds))
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def generate_response(self, prompt: str) -> str:
        """Генерация текстового ответа на запрос пользователя.

        При недоступности сервера переключается на FallbackRuleEngine.
        """
        url = f"{self.api_base}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Ты автономный оператор Windows 11 GupkaCommander. "
                        "Отвечай кратко на русском языке."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 512,
        }

        try:
            resp = self.session.post(url, json=payload, timeout=self.timeout_seconds)
            if resp.status_code == 200:
                data: dict[str, Any] = resp.json()
                choices: list[dict[str, Any]] = data.get("choices", [])
                if choices:
                    content: str = choices[0].get("message", {}).get("content", "")
                    if content:
                        return content.strip()
        except requests.RequestException as err:
            logger.warning("Локальный LLM недоступен (%s). Переключение на fallback.", err)

        return self.fallback_engine.respond(prompt)

    def plan_actions(
        self,
        prompt: str,
        image_b64: str | None = None,
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

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Ты планировщик действий операционной системы. "
                        "Выбирай подходящие инструменты."
                    ),
                },
                {"role": "user", "content": user_content},
            ],
            "tools": self.tools,
            "tool_choice": "auto",
            "temperature": 0.1,
            "max_tokens": 1024,
        }

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
