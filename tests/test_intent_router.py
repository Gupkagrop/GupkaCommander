"""Модульные тесты для IntentRouter (core.routing.intent_router).

Проверяют:
1. Определение всех типов Intent (OPEN_APP, OPEN_URL, SEARCH_WEB, RUN_COMMAND,
   WINDOW_CONTROL, VOLUME_CONTROL, AI_COGNITIVE, VISION_INTERACT).
2. Работу концепции Fast-Path First (регулярные выражения и rapidfuzz).
3. Точное соответствие спецификации тестовых запросов.
4. Fallback в AI_COGNITIVE для свободных естественно-языковых фраз.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.routing.intent_router import Intent, IntentRouter, RouteResult


@pytest.fixture
def router() -> IntentRouter:
    """Фикстура создания стандартного маршрутизатора."""
    return IntentRouter()


class TestIntentEnumAndRouteResult:
    """Проверка структуры Enum и dataclass RouteResult."""

    def test_intent_enum_values(self) -> None:
        """Проверка всех требуемых значений Enum Intent."""
        assert Intent.OPEN_APP.value == "open_app"
        assert Intent.OPEN_URL.value == "open_url"
        assert Intent.SEARCH_WEB.value == "search_web"
        assert Intent.RUN_COMMAND.value == "run_command"
        assert Intent.WINDOW_CONTROL.value == "window_control"
        assert Intent.VOLUME_CONTROL.value == "volume_control"
        assert Intent.AI_COGNITIVE.value == "ai_cognitive"
        assert Intent.VISION_INTERACT.value == "vision_interact"

    def test_route_result_fields(self) -> None:
        """Проверка структуры и неизменяемости RouteResult."""
        res = RouteResult(
            intent=Intent.OPEN_APP,
            query="открой telegram",
            target="telegram",
            params={"app": "telegram"},
            confidence=1.0,
            matched_rule="test_rule",
        )
        assert res.intent == Intent.OPEN_APP
        assert res.query == "открой telegram"
        assert res.target == "telegram"
        assert res.params == {"app": "telegram"}
        assert res.confidence == 1.0
        assert res.matched_rule == "test_rule"


class TestIntentRouterSpecifications:
    """Проверка обязательных сценариев маршрутизации из спецификации."""

    def test_match_open_app_telegram(self, router: IntentRouter) -> None:
        """Спецификация: 'открой telegram' -> OPEN_APP."""
        res = router.route("открой telegram")
        assert res.intent == Intent.OPEN_APP
        assert res.target == "telegram"
        assert res.params.get("app") == "telegram"

    def test_match_search_web_youtube_rock(self, router: IntentRouter) -> None:
        """Спецификация: 'найди в youtube rock' -> SEARCH_WEB."""
        res = router.route("найди в youtube rock")
        assert res.intent == Intent.SEARCH_WEB
        assert res.target == "rock"
        assert res.params.get("engine") == "youtube"
        assert res.params.get("query") == "rock"

    def test_match_volume_control_louder(self, router: IntentRouter) -> None:
        """Спецификация: 'сделай громче' -> VOLUME_CONTROL."""
        res = router.route("сделай громче")
        assert res.intent == Intent.VOLUME_CONTROL
        assert res.params.get("action") == "up"

    def test_match_window_control_minimize(self, router: IntentRouter) -> None:
        """Спецификация: 'сверни окно' -> WINDOW_CONTROL."""
        res = router.route("сверни окно")
        assert res.intent == Intent.WINDOW_CONTROL
        assert res.params.get("action") == "minimize"

    def test_match_run_command_powershell_ls(self, router: IntentRouter) -> None:
        """Спецификация: 'запусти powershell ls' -> RUN_COMMAND."""
        res = router.route("запусти powershell ls")
        assert res.intent == Intent.RUN_COMMAND
        assert res.params.get("shell") == "powershell"
        assert res.params.get("command") == "ls"


class TestIntentRouterComprehensive:
    """Расширенные тесты для всех категорий маршрутизации."""

    @pytest.mark.parametrize(
        ("query", "expected_action"),
        [
            ("погромче", "up"),
            ("сделай погромче", "up"),
            ("прибавь громкость", "up"),
            ("увеличь громкость", "up"),
            ("тише", "down"),
            ("сделай тише", "down"),
            ("потише", "down"),
            ("убавь громкость", "down"),
            ("выключи звук", "mute"),
            ("заглуши звук", "mute"),
            ("без звука", "mute"),
            ("мут", "mute"),
        ],
    )
    def test_volume_variations(
        self,
        router: IntentRouter,
        query: str,
        expected_action: str,
    ) -> None:
        """Проверка различных вариаций команд управления звуком."""
        res = router.route(query)
        assert res.intent == Intent.VOLUME_CONTROL
        assert res.params.get("action") == expected_action

    def test_volume_set_absolute(self, router: IntentRouter) -> None:
        """Проверка установки абсолютного значения громкости."""
        res = router.route("установи громкость 60")
        assert res.intent == Intent.VOLUME_CONTROL
        assert res.params.get("action") == "set"
        assert res.params.get("value") == 60

    @pytest.mark.parametrize(
        ("query", "expected_action"),
        [
            ("разверни окно", "maximize"),
            ("максимизируй окно", "maximize"),
            ("закрой окно", "close"),
            ("восстанови окно", "restore"),
            ("сверни все окна", "minimize_all"),
            ("свернуть все", "minimize_all"),
        ],
    )
    def test_window_control_variations(
        self,
        router: IntentRouter,
        query: str,
        expected_action: str,
    ) -> None:
        """Проверка вариаций команд управления окнами."""
        res = router.route(query)
        assert res.intent == Intent.WINDOW_CONTROL
        assert res.params.get("action") == expected_action

    @pytest.mark.parametrize(
        ("query", "expected_shell", "expected_cmd"),
        [
            ("запусти cmd dir", "cmd", "dir"),
            ("выполни команду git status", "powershell", "git status"),
            ("в терминале npm test", "powershell", "npm test"),
        ],
    )
    def test_run_command_variations(
        self,
        router: IntentRouter,
        query: str,
        expected_shell: str,
        expected_cmd: str,
    ) -> None:
        """Проверка вариаций выполнения терминальных команд."""
        res = router.route(query)
        assert res.intent == Intent.RUN_COMMAND
        assert res.params.get("shell") == expected_shell
        assert res.params.get("command") == expected_cmd

    def test_search_web_direct(self, router: IntentRouter) -> None:
        """Проверка прямого поиска в сети."""
        res = router.route("найди рецепт яблочного пирога")
        assert res.intent == Intent.SEARCH_WEB
        assert res.params.get("engine") == "google"
        assert "рецепт яблочного пирога" in res.target

    def test_open_url_explicit(self, router: IntentRouter) -> None:
        """Проверка открытия прямого URL."""
        res = router.route("открой https://ya.ru")
        assert res.intent == Intent.OPEN_URL
        assert res.target == "https://ya.ru"

    def test_open_url_web_alias(self, router: IntentRouter) -> None:
        """Проверка открытия веб-алиаса из commands.json."""
        res = router.route("открой ютуб")
        assert res.intent == Intent.OPEN_URL
        assert res.target == "https://www.youtube.com"

    def test_vision_interact_inspect(self, router: IntentRouter) -> None:
        """Проверка распознавания команды осмотра экрана."""
        res = router.route("что на экране")
        assert res.intent == Intent.VISION_INTERACT
        assert res.params.get("action") == "inspect_screen"

    def test_vision_interact_click(self, router: IntentRouter) -> None:
        """Проверка распознавания визуального клика по элементу."""
        res = router.route("кликни на кнопку сохранить")
        assert res.intent == Intent.VISION_INTERACT
        assert res.params.get("action") == "click_screen"
        assert "сохранить" in res.target

    def test_fuzzy_app_alias_match(self, router: IntentRouter) -> None:
        """Проверка нечеткого сопоставления алиаса приложения через rapidfuzz."""
        res = router.route("открой телегу")
        assert res.intent == Intent.OPEN_APP
        assert res.target == "telegram"

    @pytest.mark.parametrize(
        "query",
        [
            "расскажи анекдот",
            "какая завтра погода в Париже",
            "напиши функцию быстрой сортировки",
            "кто президент Франции",
        ],
    )
    def test_fallback_ai_cognitive(self, router: IntentRouter, query: str) -> None:
        """Проверка fallback на AI_COGNITIVE при отсутствии детерминированных команд."""
        res = router.route(query)
        assert res.intent == Intent.AI_COGNITIVE
        assert res.target == query
        assert res.matched_rule == "fallback_ai_cognitive"

    def test_empty_query(self, router: IntentRouter) -> None:
        """Проверка пустой строки."""
        res = router.route("   ")
        assert res.intent == Intent.AI_COGNITIVE
        assert res.matched_rule == "empty_query"

    def test_missing_config_defaults(self, tmp_path: Path) -> None:
        """Проверка корректной работы с дефолтами при отсутствии файла конфигурации."""
        non_existent = tmp_path / "missing_commands.json"
        custom_router = IntentRouter(config_path=non_existent)
        assert "ютуб" in custom_router.web_aliases
        assert "телега" in custom_router.app_aliases

        res = custom_router.route("открой telegram")
        assert res.intent == Intent.OPEN_APP


class TestUIAutomationRouting:
    """Модульные тесты для маршрутизации команд Microsoft UI Automation."""

    def test_ui_automation_click_button(self, router: IntentRouter) -> None:
        """Проверка распознавания команды 'нажми кнопку <имя>'."""
        res = router.route("нажми кнопку Сохранить")
        assert res.intent == Intent.UI_AUTOMATION
        assert res.target == "Сохранить"
        assert res.params["action"] == "click"
        assert res.params["element_name"] == "Сохранить"
        assert res.params["text"] == ""
        assert res.params["window_title"] == ""

    def test_ui_automation_click_by(self, router: IntentRouter) -> None:
        """Проверка распознавания команды 'кликни по <имя>'."""
        res = router.route("кликни по Отмена")
        assert res.intent == Intent.UI_AUTOMATION
        assert res.params["action"] == "click"
        assert res.params["element_name"] == "Отмена"

    def test_ui_automation_click_on(self, router: IntentRouter) -> None:
        """Проверка распознавания команды 'щелкни на <имя>'."""
        res = router.route("щелкни на ОК")
        assert res.intent == Intent.UI_AUTOMATION
        assert res.params["action"] == "click"
        assert res.params["element_name"] == "ОК"

    def test_ui_automation_type_field_text(self, router: IntentRouter) -> None:
        """Проверка распознавания 'введи в поле <имя> текст <текст>'."""
        res = router.route("введи в поле Поиск текст Привет мир")
        assert res.intent == Intent.UI_AUTOMATION
        assert res.params["action"] == "type"
        assert res.params["element_name"] == "Поиск"
        assert res.params["text"] == "Привет мир"

    def test_ui_automation_type_colon(self, router: IntentRouter) -> None:
        """Проверка распознавания 'напечатай в <имя> : <текст>'."""
        res = router.route("напечатай в Логин : admin")
        assert res.intent == Intent.UI_AUTOMATION
        assert res.params["action"] == "type"
        assert res.params["element_name"] == "Логин"
        assert res.params["text"] == "admin"

    def test_ui_automation_checkbox_set(self, router: IntentRouter) -> None:
        """Проверка распознавания 'поставь галочку <имя>'."""
        res = router.route("поставь галочку Запомнить меня")
        assert res.intent == Intent.UI_AUTOMATION
        assert res.params["action"] == "set_toggle"
        assert res.params["element_name"] == "Запомнить меня"
        assert res.params["text"] == "true"

    def test_ui_automation_checkbox_unset(self, router: IntentRouter) -> None:
        """Проверка распознавания 'сними галочку <имя>'."""
        res = router.route("сними галочку Автосохранение")
        assert res.intent == Intent.UI_AUTOMATION
        assert res.params["action"] == "set_toggle"
        assert res.params["element_name"] == "Автосохранение"
        assert res.params["text"] == "false"

    def test_ui_automation_with_window_suffix(self, router: IntentRouter) -> None:
        """Проверка распознавания контекста окна в конце запроса ('в окне ...')."""
        res = router.route("нажми кнопку Сохранить в окне Блокнот")
        assert res.intent == Intent.UI_AUTOMATION
        assert res.params["action"] == "click"
        assert res.params["element_name"] == "Сохранить"
        assert res.params["window_title"] == "Блокнот"

    def test_ui_automation_with_window_prefix(self, router: IntentRouter) -> None:
        """Проверка распознавания контекста окна в начале запроса ('внутри ...')."""
        res = router.route("внутри Калькулятор щелкни на Ок")
        assert res.intent == Intent.UI_AUTOMATION
        assert res.params["action"] == "click"
        assert res.params["element_name"] == "Ок"
        assert res.params["window_title"] == "Калькулятор"

    def test_ui_automation_type_with_window(self, router: IntentRouter) -> None:
        """Проверка ввода текста с указанием окна."""
        res = router.route("введи в поле Имя текст Денис в окне Профиль")
        assert res.intent == Intent.UI_AUTOMATION
        assert res.params["action"] == "type"
        assert res.params["element_name"] == "Имя"
        assert res.params["text"] == "Денис"
        assert res.params["window_title"] == "Профиль"
