"""Модульные тесты для класса MemoryManager."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from core.memory.memory_manager import MemoryManager


@pytest.fixture
def memory_mgr() -> Iterator[MemoryManager]:
    """Фикстура изолированного менеджера памяти в :memory:."""
    mgr = MemoryManager(db_path=":memory:")
    yield mgr
    mgr.close()


def test_user_facts_crud(memory_mgr: MemoryManager) -> None:
    """Тестирование полного цикла CRUD для пользовательских фактов."""
    # 1. Добавление (Insert)
    memory_mgr.set_fact("user_name", "Денис", category="profile")
    assert memory_mgr.get_fact("user_name") == "Денис"

    # 2. Обновление (Update / Upsert)
    memory_mgr.set_fact("user_name", "Денис В.", category="profile")
    assert memory_mgr.get_fact("user_name") == "Денис В."

    # 3. Список фактов (List)
    memory_mgr.set_fact("editor", "VS Code", category="tools")
    facts_all = memory_mgr.list_facts()
    assert len(facts_all) == 2
    assert facts_all["user_name"] == "Денис В."
    assert facts_all["editor"] == "VS Code"

    facts_tools = memory_mgr.list_facts(category="tools")
    assert facts_tools == {"editor": "VS Code"}

    # 4. Удаление (Delete)
    assert memory_mgr.delete_fact("editor") is True
    assert memory_mgr.get_fact("editor") is None
    assert memory_mgr.delete_fact("editor") is False


def test_user_facts_validation(memory_mgr: MemoryManager) -> None:
    """Тестирование валидации некорректных аргументов фактов."""
    with pytest.raises(ValueError, match="Ключ факта не может быть пустым"):
        memory_mgr.set_fact("   ", "значение")

    assert memory_mgr.get_fact("") is None
    assert memory_mgr.get_fact("   ") is None
    assert memory_mgr.delete_fact("   ") is False


def test_notes_crud_and_completion(memory_mgr: MemoryManager) -> None:
    """Тестирование CRUD операций и отметки выполнения для заметок."""
    # 1. Создание
    note_id = memory_mgr.add_note("Купить хлеб и молоко", tags=["покупки", "быт"])
    assert note_id > 0

    # 2. Получение активных заметок
    active_notes = memory_mgr.list_notes(only_active=True)
    assert len(active_notes) == 1
    assert active_notes[0]["id"] == note_id
    assert active_notes[0]["content"] == "Купить хлеб и молоко"
    assert "покупки" in active_notes[0]["tags"]
    assert active_notes[0]["is_done"] is False

    # 3. Отметка о выполнении
    assert memory_mgr.complete_note(note_id) is True
    assert len(memory_mgr.list_notes(only_active=True)) == 0

    all_notes = memory_mgr.list_notes(only_active=False)
    assert len(all_notes) == 1
    assert all_notes[0]["is_done"] is True

    # 4. Удаление
    assert memory_mgr.delete_note(note_id) is True
    assert len(memory_mgr.list_notes(only_active=False)) == 0
    assert memory_mgr.delete_note(note_id) is False


def test_notes_empty_content_validation(memory_mgr: MemoryManager) -> None:
    """Тестирование валидации пустого содержимого при создании заметки."""
    with pytest.raises(ValueError, match="Содержимое заметки не может быть пустым"):
        memory_mgr.add_note("   ")


def test_notes_fts_and_like_search(memory_mgr: MemoryManager) -> None:
    """Тестирование полнотекстового поиска FTS5 и резервного поиска по заметкам."""
    memory_mgr.add_note("Подготовить отчет по проекту GupkaCommander", tags=["работа"])
    memory_mgr.add_note("Купить кофе в зернах и фильтры", tags=["дом"])
    memory_mgr.add_note("Записаться к врачу на осмотр", tags=["здоровье"])

    # FTS5 поиск по слову
    report_results = memory_mgr.list_notes(query="отчет")
    assert len(report_results) == 1
    assert "GupkaCommander" in report_results[0]["content"]

    # FTS5 поиск по префиксу/части
    coffee_results = memory_mgr.list_notes(query="кофе")
    assert len(coffee_results) == 1
    assert "фильтры" in coffee_results[0]["content"]

    # Поиск по несуществующему слову
    empty_results = memory_mgr.list_notes(query="абракадабра")
    assert len(empty_results) == 0


def test_conversation_logging_and_history(memory_mgr: MemoryManager) -> None:
    """Тестирование логирования диалогов и получения недавней истории."""
    # Логируем реплику пользователя
    memory_mgr.log_turn(
        role="user",
        text="Какая погода сегодня?",
        intent="weather_query",
    )

    # Логируем ответ ассистента с вызовами инструментов
    tool_calls: list[dict[str, Any]] = [{"name": "get_weather", "parameters": {"city": "Москва"}}]
    memory_mgr.log_turn(
        role="assistant",
        text="Сегодня в Москве ясно, +18°C.",
        intent=None,
        tool_calls=tool_calls,
    )

    history = memory_mgr.get_recent_history(limit=5)
    assert len(history) == 2

    # Хронологический порядок: от старых к новым
    assert history[0]["role"] == "user"
    assert history[0]["text"] == "Какая погода сегодня?"
    assert history[0]["intent"] == "weather_query"
    assert history[0]["tool_calls"] is None

    assert history[1]["role"] == "assistant"
    assert history[1]["text"] == "Сегодня в Москве ясно, +18°C."
    assert history[1]["tool_calls"] == tool_calls

    # Проверка лимита
    history_limit_1 = memory_mgr.get_recent_history(limit=1)
    assert len(history_limit_1) == 1
    assert history_limit_1[0]["role"] == "assistant"

    # Проверка неположительного лимита
    assert memory_mgr.get_recent_history(limit=0) == []


def test_format_system_prompt_context(memory_mgr: MemoryManager) -> None:
    """Тестирование формирования контекста для системного промпта LLM."""
    # Если база пуста — возвращается пустая строка
    assert memory_mgr.format_system_prompt_context() == ""

    # Добавляем факты
    memory_mgr.set_fact("имя_пользователя", "Денис", category="general")
    memory_mgr.set_fact("любимый_язык", "Python", category="preferences")

    context = memory_mgr.format_system_prompt_context(max_facts=5)
    assert "### Известные факты о пользователе:" in context
    assert "- имя_пользователя: Денис" in context
    assert "- любимый_язык (preferences): Python" in context

    # Проверка ограничения max_facts <= 0
    assert memory_mgr.format_system_prompt_context(max_facts=0) == ""
