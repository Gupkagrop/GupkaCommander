"""Интеграционные тесты для Microsoft UI Automation и Fallback (tests/test_integration_uia.py).

Проверяют:
1. Маршрутизацию команд Intent.UI_AUTOMATION (клики, ввод текста, флажки, контекст окна).
2. Спецификацию Function Calling и FallbackRuleEngine в LocalLLMClient для UIA-инструментов.
3. Каскад в GupkaCommanderApp: Fast-Path -> UIA -> Vision Fallback при отсутствии элемента UIA.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtWidgets import QApplication

from core.app_finder import AppFinder
from core.brain.llm_client import SYSTEM_TOOLS, FallbackRuleEngine, LocalLLMClient
from core.os_controller import OSController
from core.routing.intent_router import Intent, IntentRouter
from core.uia.uia_controller import UIAutomationManager
from core.vision.vision_agent import VisionAgent
from core.voice.voice_pipeline import VoicePipeline
from gui.hotkey_manager import HotkeyManager
from gui.spotlight_bar import SpotlightBar
from gui.tray_icon import TrayManager
from main import GupkaCommanderApp


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Синглтон QApplication для headless-тестирования Qt компонентов."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def intent_router() -> IntentRouter:
    """Фикстура маршрутизатора намерения IntentRouter."""
    return IntentRouter()


@pytest.fixture
def llm_client(intent_router: IntentRouter) -> LocalLLMClient:
    """Фикстура клиента локальной языковой модели LocalLLMClient."""
    return LocalLLMClient(router=intent_router)


class TestUIAutomationRoutingIntegration:
    """Интеграционные тесты распознавания команд UI Automation через IntentRouter."""

    def test_routing_click_button_variants(self, intent_router: IntentRouter) -> None:
        """Проверка распознавания клика по кнопкам в различных речевых формулировках."""
        # «нажми кнопку <имя>»
        res1 = intent_router.route("нажми кнопку Применить")
        assert res1.intent == Intent.UI_AUTOMATION
        assert res1.params["action"] == "click"
        assert res1.params["element_name"] == "Применить"

        # «кликни по <имя>»
        res2 = intent_router.route("кликни по Войти")
        assert res2.intent == Intent.UI_AUTOMATION
        assert res2.params["action"] == "click"
        assert res2.params["element_name"] == "Войти"

        # «щелкни на <имя>»
        res3 = intent_router.route("щелкни на Загрузить")
        assert res3.intent == Intent.UI_AUTOMATION
        assert res3.params["action"] == "click"
        assert res3.params["element_name"] == "Загрузить"

    def test_routing_type_text_variants(self, intent_router: IntentRouter) -> None:
        """Проверка распознавания ввода текста в поля интерфейса."""
        # «введи в поле <имя> текст <текст>»
        res1 = intent_router.route("введи в поле Поиск текст Python 3.12")
        assert res1.intent == Intent.UI_AUTOMATION
        assert res1.params["action"] == "type"
        assert res1.params["element_name"] == "Поиск"
        assert res1.params["text"] == "Python 3.12"

        # «напечатай в <имя> : <текст>»
        res2 = intent_router.route("напечатай в Заметка : Важная информация")
        assert res2.intent == Intent.UI_AUTOMATION
        assert res2.params["action"] == "type"
        assert res2.params["element_name"] == "Заметка"
        assert res2.params["text"] == "Важная информация"

    def test_routing_toggle_checkbox(self, intent_router: IntentRouter) -> None:
        """Проверка команд переключения флажков (CheckBox/RadioButton)."""
        # Установка флажка
        res_on = intent_router.route("поставь галочку Темная тема")
        assert res_on.intent == Intent.UI_AUTOMATION
        assert res_on.params["action"] == "set_toggle"
        assert res_on.params["element_name"] == "Темная тема"
        assert res_on.params["text"] == "true"

        # Снятие флажка
        res_off = intent_router.route("сними галочку Автозапуск")
        assert res_off.intent == Intent.UI_AUTOMATION
        assert res_off.params["action"] == "set_toggle"
        assert res_off.params["element_name"] == "Автозапуск"
        assert res_off.params["text"] == "false"

    def test_routing_with_window_context(self, intent_router: IntentRouter) -> None:
        """Проверка извлечения контекста целевого окна (префикс и суффикс)."""
        # Суффикс: «... в окне <окно>»
        res_suf = intent_router.route("нажми кнопку Сохранить в окне Блокнот")
        assert res_suf.intent == Intent.UI_AUTOMATION
        assert res_suf.params["element_name"] == "Сохранить"
        assert res_suf.params["window_title"] == "Блокнот"

        # Префикс: «внутри <окно> ...»
        res_pref = intent_router.route("внутри Калькулятор кликни по Стереть")
        assert res_pref.intent == Intent.UI_AUTOMATION
        assert res_pref.params["element_name"] == "Стереть"
        assert res_pref.params["window_title"] == "Калькулятор"


class TestLLMFunctionCallingUIAIntegration:
    """Тесты спецификации инструментов и планирования UIA в LocalLLMClient."""

    def test_system_tools_contain_uia_tools(self) -> None:
        """Проверка наличия всех трех инструментов UIA в схеме OpenAI Tools."""
        tool_names = {t["function"]["name"] for t in SYSTEM_TOOLS}
        assert "click_ui_element" in tool_names
        assert "type_into_ui_element" in tool_names
        assert "inspect_ui_elements" in tool_names

        # Проверка обязательных параметров схемы
        click_tool = next(t for t in SYSTEM_TOOLS if t["function"]["name"] == "click_ui_element")
        assert "element_name" in click_tool["function"]["parameters"]["required"]

        type_tool = next(t for t in SYSTEM_TOOLS if t["function"]["name"] == "type_into_ui_element")
        assert "element_name" in type_tool["function"]["parameters"]["required"]
        assert "text" in type_tool["function"]["parameters"]["required"]

    def test_fallback_engine_plans_uia_actions(self) -> None:
        """Проверка планирования действий FallbackRuleEngine для UIA."""
        engine = FallbackRuleEngine()

        # Клик
        plan_click = engine.plan("нажми кнопку Отправить в окне Форма")
        assert len(plan_click) == 1
        assert plan_click[0]["tool"] == "click_ui_element"
        assert plan_click[0]["parameters"]["element_name"] == "Отправить"
        assert plan_click[0]["parameters"]["window_title"] == "Форма"

        # Ввод текста
        plan_type = engine.plan("введи в поле Почта текст test@example.com")
        assert len(plan_type) == 1
        assert plan_type[0]["tool"] == "type_into_ui_element"
        assert plan_type[0]["parameters"]["element_name"] == "Почта"
        assert plan_type[0]["parameters"]["text"] == "test@example.com"

        # Флажок
        plan_toggle = engine.plan("поставь галочку Согласен с условиями")
        assert len(plan_toggle) == 1
        assert plan_toggle[0]["tool"] == "click_ui_element"
        assert plan_toggle[0]["parameters"]["control_type"] == "CheckBox"

    def test_fallback_engine_responds_in_russian(self) -> None:
        """Проверка русскоязычных ответов FallbackRuleEngine для намерений UIA."""
        engine = FallbackRuleEngine()

        resp_click = engine.respond("нажми кнопку Сохранить в окне Документ")
        assert "Сохранить" in resp_click
        assert "Документ" in resp_click

        resp_type = engine.respond("введи в поле Логин текст admin")
        assert "admin" in resp_type
        assert "Логин" in resp_type


class TestAppUIACascadeIntegration:
    """Тесты сквозного выполнения и каскадного отката UIA -> Vision в GupkaCommanderApp."""

    @pytest.fixture
    def app_bundle(self, qapp: QApplication) -> tuple[GupkaCommanderApp, MagicMock, MagicMock]:
        """Создает изолированный экземпляр приложения GupkaCommanderApp с моками."""
        mock_finder = MagicMock(spec=AppFinder)
        mock_finder.find.return_value = []

        mock_os = MagicMock(spec=OSController)
        mock_voice = MagicMock(spec=VoicePipeline)
        mock_spotlight = MagicMock(spec=SpotlightBar)
        mock_tray = MagicMock(spec=TrayManager)
        mock_tray.is_muted.return_value = True
        mock_hotkey = MagicMock(spec=HotkeyManager)

        mock_uia = MagicMock(spec=UIAutomationManager)
        mock_vision = MagicMock(spec=VisionAgent)
        mock_vision.inspect_screen.return_value = {
            "width": 1920,
            "height": 1080,
            "dhash": "0000ffff0000ffff",
            "image_b64": "",
        }

        router = IntentRouter()
        llm_client = LocalLLMClient(router=router)

        app = GupkaCommanderApp(
            app=qapp,
            app_finder=mock_finder,
            os_controller=mock_os,
            voice_pipeline=mock_voice,
            spotlight_bar=mock_spotlight,
            tray_manager=mock_tray,
            hotkey_manager=mock_hotkey,
            intent_router=router,
            llm_client=llm_client,
            vision_agent=mock_vision,
            uia_manager=mock_uia,
            enable_voice=False,
            enable_hotkey=False,
            enable_tray=False,
        )

        return app, mock_uia, mock_vision

    def test_uia_click_success_no_vision_fallback(
        self, app_bundle: tuple[GupkaCommanderApp, MagicMock, MagicMock]
    ) -> None:
        """При успешном клике через UIA визуальный агент VisionAgent НЕ должен вызываться."""
        app, mock_uia, mock_vision = app_bundle
        mock_uia.click_element.return_value = True

        resp = app.execute_command("нажми кнопку Сохранить")

        # UIA клик вызван с нужными аргументами
        mock_uia.click_element.assert_called_once_with("Сохранить", window_title=None)
        # VisionAgent клик НЕ вызывался
        mock_vision.click_at.assert_not_called()
        assert "Нажат элемент 'Сохранить'" in resp

    def test_uia_click_miss_cascade_to_vision_fallback(
        self, app_bundle: tuple[GupkaCommanderApp, MagicMock, MagicMock]
    ) -> None:
        """Если элемент не найден в UIA (False), при успехе VLM происходит каскад на click_at."""
        app, mock_uia, mock_vision = app_bundle
        # UIA не смог найти элемент в дереве
        mock_uia.click_element.return_value = False
        mock_vision.inspect_screen.return_value = {"image_b64": "fake"}

        with patch.object(
            app.llm_client,
            "plan_actions",
            return_value=[{"tool": "click_screen", "parameters": {"x": 150, "y": 250}}],
        ):
            resp = app.execute_command("нажми кнопку Подтвердить")

            # UIA попытался найти элемент
            mock_uia.click_element.assert_called_once_with("Подтвердить", window_title=None)
            # Автоматический каскадный вызов VisionAgent
            mock_vision.click_at.assert_called_once_with(150, 250, verify_change=False)
            assert "визуальный" in resp.lower()

    def test_uia_click_miss_no_blind_click_without_vlm_coords(
        self, app_bundle: tuple[GupkaCommanderApp, MagicMock, MagicMock]
    ) -> None:
        """Если VLM не вернул координаты, клик вслепую не выполняется."""
        app, mock_uia, mock_vision = app_bundle
        mock_uia.click_element.return_value = False
        mock_vision.inspect_screen.return_value = {"image_b64": "fake"}

        with patch.object(app.llm_client, "plan_actions", return_value=[]):
            resp = app.execute_command("нажми кнопку Подтвердить")

            mock_uia.click_element.assert_called_once_with("Подтвердить", window_title=None)
            mock_vision.click_at.assert_not_called()
            assert "не найден на экране через визуальный анализ" in resp

    def test_uia_type_success_and_fallback(
        self, app_bundle: tuple[GupkaCommanderApp, MagicMock, MagicMock]
    ) -> None:
        """Проверка успешного ввода текста через UIA и fallback на type_text."""
        app, mock_uia, mock_vision = app_bundle

        # 1. Успешный ввод через UIA
        mock_uia.type_into_element.return_value = True
        resp_ok = app.execute_command("введи в поле Логин текст admin")
        mock_uia.type_into_element.assert_called_once_with("Логин", "admin", window_title=None)
        mock_vision.type_text.assert_not_called()
        assert "Введен текст 'admin'" in resp_ok

        # 2. Неуспешный ввод через UIA -> fallback на клавиатурный ввод VisionAgent
        mock_uia.type_into_element.reset_mock()
        mock_uia.type_into_element.return_value = False
        resp_fallback = app.execute_command("введи в поле Пароль текст secret")
        mock_uia.type_into_element.assert_called_once_with("Пароль", "secret", window_title=None)
        mock_vision.type_text.assert_called_once_with("secret")
        assert "secret" in resp_fallback

    def test_uia_toggle_state_handling(
        self, app_bundle: tuple[GupkaCommanderApp, MagicMock, MagicMock]
    ) -> None:
        """Проверка управления флажками через UIA в главном цикле приложения."""
        app, mock_uia, _ = app_bundle
        mock_uia.set_toggle_state.return_value = True

        resp = app.execute_command("поставь галочку Автосохранение")
        mock_uia.set_toggle_state.assert_called_once_with("Автосохранение", True, window_title=None)
        assert "Автосохранение" in resp
        assert "включен" in resp.lower() or "переключен" in resp.lower()

    def test_planned_tool_inspect_ui_elements(
        self, app_bundle: tuple[GupkaCommanderApp, MagicMock, MagicMock]
    ) -> None:
        """Проверка вызова инструмента inspect_ui_elements через _execute_planned_tool."""
        app, mock_uia, _ = app_bundle
        mock_uia.inspect_window_elements.return_value = [
            {"name": "ОК", "control_type": "ButtonControl"},
            {"name": "Отмена", "control_type": "ButtonControl"},
        ]

        res = app._execute_planned_tool("inspect_ui_elements", {"window_title": "Блокнот"})
        mock_uia.inspect_window_elements.assert_called_once_with(window_title="Блокнот")
        assert "Найдено 2 элементов" in res
        assert "Блокнот" in res

    def test_failsafe_interruption_in_app(
        self, app_bundle: tuple[GupkaCommanderApp, MagicMock, MagicMock]
    ) -> None:
        """Проверка перехвата аварийного прерывания FailSafe при выполнении команды UIA."""
        app, mock_uia, _ = app_bundle
        mock_uia.click_element.side_effect = InterruptedError("FailSafe abort: Escape pressed")

        resp = app.execute_command("нажми кнопку Экстренная остановка")
        assert "прервана пользователем" in resp
        assert "FailSafe" in resp
