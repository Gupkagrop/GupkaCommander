"""Сквозные интеграционные тесты для Вектора 3: Долговременная память и база знаний SQLite.

> [!NOTE]
> **Назначение файла:** Проверка сквозной цепочки работы с памятью:
> 1. Маршрутизация команд сохранения фактов и заметок через IntentRouter в Fast-Path.
> 2. Сохранение, поиск и управление заметками и фактами в SQLite базе данных.
> 3. Автоматическая инъекция контекста памяти в системный промпт LLM.
> 4. Двустороннее логирование истории диалогов (user/assistant) в conversations.
> 5. Спецификации Function Calling и выполнение инструментов памяти.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtWidgets import QApplication

from core.app_finder import AppFinder
from core.brain.llm_client import SYSTEM_TOOLS, FallbackRuleEngine, LocalLLMClient
from core.failsafe import FailSafeMonitor
from core.memory.memory_manager import MemoryManager
from core.os_controller import OSController
from core.routing.intent_router import IntentRouter
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
def memory_manager() -> Iterator[MemoryManager]:
    """Изолированный менеджер памяти в оперативной памяти :memory:."""
    mgr = MemoryManager(db_path=":memory:")
    yield mgr
    mgr.close()


@pytest.fixture
def app_bundle(
    qapp: QApplication,
    memory_manager: MemoryManager,
) -> tuple[GupkaCommanderApp, MagicMock, MagicMock]:
    """Фикстура с изолированным приложением GupkaCommanderApp и mock-зависимостями."""
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
    mock_failsafe = MagicMock(spec=FailSafeMonitor)
    mock_failsafe.is_interrupted.return_value = False

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
        failsafe_monitor=mock_failsafe,
        memory_manager=memory_manager,
        enable_voice=False,
        enable_hotkey=False,
        enable_tray=False,
    )

    return app, mock_spotlight, mock_voice


class TestVector3FastPathAndStorage:
    """Интеграционные тесты быстрого пути Fast-Path и персистентного хранения."""

    def test_remember_fact_fast_path_and_storage(
        self,
        app_bundle: tuple[GupkaCommanderApp, MagicMock, MagicMock],
    ) -> None:
        """Интеграционный тест: «запомни, что мой редактор VS Code» -> запись в БД -> проверка."""
        app, _, _ = app_bundle

        # Выполняем голосовую/текстовую команду через точку входа
        response = app.execute_command("запомни, что мой редактор VS Code")

        # Проверяем ответ пользователю
        assert "редактор" in response
        assert "VS Code" in response

        # Проверяем прямое сохранение факта в базе данных SQLite
        stored_fact = app.memory_manager.get_fact("редактор")
        assert stored_fact == "VS Code"

        # Проверяем список фактов
        all_facts = app.memory_manager.list_facts()
        assert all_facts.get("редактор") == "VS Code"

    def test_add_note_fast_path_and_fts_search(
        self,
        app_bundle: tuple[GupkaCommanderApp, MagicMock, MagicMock],
    ) -> None:
        """Интеграционный тест: «запиши заметку купить хлеб» -> поиск заметки через FTS5."""
        app, _, _ = app_bundle

        # Добавляем заметку через команду приложения
        response = app.execute_command("запиши заметку купить хлеб")
        assert "Заметка" in response
        assert "сохранена" in response
        assert "купить хлеб" in response

        # Проверяем извлечение заметки из базы через полнотекстовый поиск
        found_notes = app.memory_manager.list_notes(only_active=True, query="хлеб")
        assert len(found_notes) == 1
        assert "купить хлеб" in found_notes[0]["content"]
        assert found_notes[0]["is_done"] is False

    def test_list_and_manage_notes_lifecycle(
        self,
        app_bundle: tuple[GupkaCommanderApp, MagicMock, MagicMock],
    ) -> None:
        """Интеграционный тест: создание, просмотр, выполнение и удаление заметок."""
        app, _, _ = app_bundle

        # 1. Создаем заметку
        app.execute_command("запиши заметку подготовить презентацию")
        notes = app.memory_manager.list_notes(only_active=True)
        assert len(notes) == 1
        note_id = notes[0]["id"]

        # 2. Просмотр списка заметок
        list_resp = app.execute_command("покажи мои заметки")
        assert f"#{note_id}" in list_resp
        assert "подготовить презентацию" in list_resp

        # 3. Отметка о выполнении
        complete_resp = app.execute_command(f"выполни заметку {note_id}")
        assert f"#{note_id}" in complete_resp
        assert "отмечена как выполненная" in complete_resp

        # Активных больше нет
        assert len(app.memory_manager.list_notes(only_active=True)) == 0

        # 4. Удаление заметки
        del_resp = app.execute_command(f"удали заметку {note_id}")
        assert f"#{note_id}" in del_resp
        assert "удалена" in del_resp
        assert len(app.memory_manager.list_notes(only_active=False)) == 0


class TestVector3LLMContextInjectionAndHistory:
    """Интеграционные тесты инъекции контекста памяти в системный промпт LLM и логирования."""

    def test_memory_context_injection_in_system_prompt(
        self,
        app_bundle: tuple[GupkaCommanderApp, MagicMock, MagicMock],
    ) -> None:
        """Интеграционный тест: факты из памяти инжектируются в системный промпт LLM."""
        app, _, _ = app_bundle

        # Предварительно наполняем память фактами о пользователе
        app.memory_manager.set_fact("имя", "Денис", category="profile")
        app.memory_manager.set_fact("редактор", "VS Code", category="tools")

        captured_system_prompts: list[str] = []

        def mock_generate_stream(
            user_text: str,
            system_prompt: str | None = None,
        ) -> Iterator[str]:
            if system_prompt:
                captured_system_prompts.append(system_prompt)
            yield "Ответ"
            yield " модели"

        with (
            patch.object(app.llm_client, "is_healthy", return_value=True),
            patch.object(
                app.llm_client,
                "generate_response_stream",
                side_effect=mock_generate_stream,
            ),
        ):
            # Запрос, не попадающий под быстрые правила (свободный диалог)
            app.execute_command("Расскажи интересную мысль о программировании")

        # Проверяем, что в системном промпте присутствовал блок памяти
        assert len(captured_system_prompts) > 0
        prompt_text = captured_system_prompts[0]
        assert "### Известные факты о пользователе:" in prompt_text
        assert "имя (profile): Денис" in prompt_text
        assert "редактор (tools): VS Code" in prompt_text

    def test_planner_system_prompt_context_injection(
        self,
        app_bundle: tuple[GupkaCommanderApp, MagicMock, MagicMock],
    ) -> None:
        """Интеграционный тест: факты из памяти в планировщике действий (Tool Calling)."""
        app, _, _ = app_bundle

        app.memory_manager.set_fact("город", "Москва", category="location")
        captured_planner_prompts: list[str] = []

        def mock_plan_actions(
            prompt: str,
            image_b64: str | None = None,
            system_prompt: str | None = None,
        ) -> list[dict[str, Any]]:
            if system_prompt:
                captured_planner_prompts.append(system_prompt)
            return []

        with (
            patch.object(app.llm_client, "is_healthy", return_value=True),
            patch.object(app.llm_client, "plan_actions", side_effect=mock_plan_actions),
        ):
            app.execute_command("Какая погода на улице?")

        assert len(captured_planner_prompts) > 0
        planner_prompt = captured_planner_prompts[0]
        assert "### Известные факты о пользователе:" in planner_prompt
        assert "город (location): Москва" in planner_prompt

    def test_dialog_turn_logging_in_conversations(
        self,
        app_bundle: tuple[GupkaCommanderApp, MagicMock, MagicMock],
    ) -> None:
        """Интеграционный тест: двустороннее логирование шагов диалога в таблицу conversations."""
        app, _, _ = app_bundle

        # 1. Выполняем команду памяти
        app.execute_command("запомни, что моя ОС Windows 11")

        # 2. Выполняем команду управления громкостью
        app.execute_command("сделай громче")

        # Проверяем историю из базы данных
        history = app.memory_manager.get_recent_history(limit=10)
        assert len(history) == 4

        # Шаг 1: пользователь и ассистент
        assert history[0]["role"] == "user"
        assert history[0]["text"] == "запомни, что моя ОС Windows 11"
        assert history[0]["intent"] == "memory"

        assert history[1]["role"] == "assistant"
        assert "Windows 11" in history[1]["text"]

        # Шаг 2: пользователь и ассистент
        assert history[2]["role"] == "user"
        assert history[2]["text"] == "сделай громче"
        assert history[2]["intent"] == "volume_control"

        assert history[3]["role"] == "assistant"
        assert "Громкость" in history[3]["text"]


class TestVector3FunctionCallingAndTools:
    """Интеграционные тесты для Function Calling и инструментов долговременной памяти."""

    def test_system_tools_contain_memory_specifications(self) -> None:
        """Проверка регистрации 5 инструментов памяти в SYSTEM_TOOLS."""
        tool_names = {t["function"]["name"] for t in SYSTEM_TOOLS if t.get("type") == "function"}

        expected_tools = {
            "store_user_fact",
            "retrieve_user_facts",
            "add_user_note",
            "list_user_notes",
            "delete_user_note",
        }
        for expected in expected_tools:
            assert expected in tool_names

    def test_fallback_rule_engine_memory_planning(self) -> None:
        """Проверка планирования действий FallbackRuleEngine для команд памяти."""
        router = IntentRouter()
        engine = FallbackRuleEngine(router=router)

        # 1. store_user_fact
        plan_fact = engine.plan("запомни, что любимый цвет это зеленый")
        assert len(plan_fact) == 1
        assert plan_fact[0]["tool"] == "store_user_fact"
        assert plan_fact[0]["parameters"]["key"] == "любимый цвет"
        assert plan_fact[0]["parameters"]["value"] == "зеленый"

        # 2. add_user_note
        plan_note = engine.plan("запиши заметку проверить почту")
        assert len(plan_note) == 1
        assert plan_note[0]["tool"] == "add_user_note"
        assert plan_note[0]["parameters"]["content"] == "проверить почту"

        # 3. list_user_notes
        plan_list = engine.plan("покажи мои заметки")
        assert len(plan_list) == 1
        assert plan_list[0]["tool"] == "list_user_notes"

        # 4. delete_user_note
        plan_del = engine.plan("удали заметку 42")
        assert len(plan_del) == 1
        assert plan_del[0]["tool"] == "delete_user_note"
        assert plan_del[0]["parameters"]["note_id"] == 42

    def test_execute_planned_tools_memory_in_app(
        self,
        app_bundle: tuple[GupkaCommanderApp, MagicMock, MagicMock],
    ) -> None:
        """Проверка исполнения спланированных инструментов памяти через _execute_planned_tool."""
        app, _, _ = app_bundle

        # 1. store_user_fact
        res1 = app._execute_planned_tool("store_user_fact", {"key": "shell", "value": "powershell"})
        assert "сохранен в память" in res1
        assert app.memory_manager.get_fact("shell") == "powershell"

        # 2. retrieve_user_facts
        res2 = app._execute_planned_tool("retrieve_user_facts", {})
        assert "shell: powershell" in res2

        # 3. add_user_note
        res3 = app._execute_planned_tool("add_user_note", {"content": "Купить кабель Type-C"})
        assert "добавлена" in res3

        # 4. list_user_notes
        res4 = app._execute_planned_tool("list_user_notes", {"query": "кабель"})
        assert "кабель" in res4

        # 5. delete_user_note
        notes = app.memory_manager.list_notes()
        assert len(notes) == 1
        nid = notes[0]["id"]
        res5 = app._execute_planned_tool("delete_user_note", {"note_id": nid})
        assert f"Заметка #{nid} удалена." == res5
