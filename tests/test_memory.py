"""Модульные тесты для базы данных SQLite и менеджера долговременной памяти."""

from __future__ import annotations

import concurrent.futures
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from core.memory.db import init_database
from core.memory.memory_manager import MemoryManager


@pytest.fixture
def memory_manager() -> Iterator[MemoryManager]:
    """Фикстура менеджера памяти с базой данных в оперативной памяти."""
    mgr = MemoryManager(db_path=":memory:")
    yield mgr
    mgr.close()


def test_db_initialization_in_memory() -> None:
    """Проверка корректной инициализации схемы БД в памяти."""
    conn = init_database(":memory:")
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY name"
    )
    tables = [row["name"] for row in cursor.fetchall()]

    assert "user_facts" in tables
    assert "notes" in tables
    assert "notes_fts" in tables
    assert "conversations" in tables
    conn.close()


def test_db_initialization_file_path(tmp_path: Path) -> None:
    """Проверка инициализации БД по файловому пути и создание директории."""
    db_file = tmp_path / "subdir" / "test_memory.db"
    assert not db_file.parent.exists()

    conn = init_database(db_file)
    assert db_file.exists()

    cursor = conn.execute("PRAGMA foreign_keys;")
    fk_status = cursor.fetchone()[0]
    assert fk_status == 1

    cursor = conn.execute("PRAGMA journal_mode;")
    mode = cursor.fetchone()[0].lower()
    assert mode == "wal"

    conn.close()


def test_set_and_get_fact(memory_manager: MemoryManager) -> None:
    """Проверка добавления и извлечения факта."""
    memory_manager.set_fact("user_name", "Денис", category="personal")
    fact = memory_manager.get_fact("user_name")
    assert fact == "Денис"

    # Проверка несуществующего ключа
    assert memory_manager.get_fact("non_existent") is None
    assert memory_manager.get_fact("   ") is None


def test_set_fact_upsert(memory_manager: MemoryManager) -> None:
    """Проверка обновления существующего факта (upsert)."""
    memory_manager.set_fact("favorite_editor", "Sublime", category="dev")
    assert memory_manager.get_fact("favorite_editor") == "Sublime"

    # Обновление значения и категории
    memory_manager.set_fact("favorite_editor", "VS Code", category="tools")
    assert memory_manager.get_fact("favorite_editor") == "VS Code"

    facts = memory_manager.list_facts(category="tools")
    assert facts == {"favorite_editor": "VS Code"}


def test_set_fact_empty_key_validation(memory_manager: MemoryManager) -> None:
    """Проверка валидации пустого ключа факта."""
    with pytest.raises(ValueError, match="Ключ факта не может быть пустым"):
        memory_manager.set_fact("   ", "Значение")


def test_list_facts_filtering(memory_manager: MemoryManager) -> None:
    """Проверка списка фактов с фильтрацией по категории."""
    memory_manager.set_fact("k1", "v1", category="system")
    memory_manager.set_fact("k2", "v2", category="personal")
    memory_manager.set_fact("k3", "v3", category="system")

    all_facts = memory_manager.list_facts()
    assert all_facts == {"k1": "v1", "k2": "v2", "k3": "v3"}

    sys_facts = memory_manager.list_facts(category="system")
    assert sys_facts == {"k1": "v1", "k3": "v3"}

    empty_facts = memory_manager.list_facts(category="finance")
    assert empty_facts == {}


def test_delete_fact(memory_manager: MemoryManager) -> None:
    """Проверка удаления факта."""
    memory_manager.set_fact("to_delete", "val")
    assert memory_manager.get_fact("to_delete") == "val"

    deleted = memory_manager.delete_fact("to_delete")
    assert deleted is True
    assert memory_manager.get_fact("to_delete") is None

    # Повторное удаление возвращает False
    assert memory_manager.delete_fact("to_delete") is False
    assert memory_manager.delete_fact("   ") is False


def test_add_and_list_notes(memory_manager: MemoryManager) -> None:
    """Проверка создания и перечисления заметок."""
    note_id1 = memory_manager.add_note("Купить хлеб", tags=["покупки", "еда"])
    note_id2 = memory_manager.add_note("Починить баг в UI", tags=["работа", "код"])

    assert note_id1 > 0
    assert note_id2 > note_id1

    notes = memory_manager.list_notes(only_active=True)
    assert len(notes) == 2
    assert notes[0]["id"] == note_id2
    assert notes[0]["content"] == "Починить баг в UI"
    assert notes[0]["tags"] == ["работа", "код"]
    assert notes[0]["is_done"] is False


def test_add_note_empty_validation(memory_manager: MemoryManager) -> None:
    """Проверка валидации пустого содержимого заметки."""
    with pytest.raises(ValueError, match="Содержимое заметки не может быть пустым"):
        memory_manager.add_note("   ")


def test_notes_fts_search(memory_manager: MemoryManager) -> None:
    """Проверка полнотекстового поиска по заметкам и тегам."""
    memory_manager.add_note("Купить молоко и сыр", tags=["продукты"])
    memory_manager.add_note("Подготовить квартальный отчет", tags=["работа"])
    memory_manager.add_note("Позвонить врачу в 14:00", tags=["здоровье"])

    # Поиск по слову из текста
    results_milk = memory_manager.list_notes(query="молоко")
    assert len(results_milk) == 1
    assert "молоко" in results_milk[0]["content"]

    # Поиск по тегу
    results_work = memory_manager.list_notes(query="работа")
    assert len(results_work) == 1
    assert results_work[0]["content"] == "Подготовить квартальный отчет"

    # Поиск по началу слова (префиксный)
    results_prefix = memory_manager.list_notes(query="кварт")
    assert len(results_prefix) == 1

    # Поиск несуществующего запроса
    assert len(memory_manager.list_notes(query="несуществующее")) == 0


def test_notes_fts_triggers_sync(memory_manager: MemoryManager) -> None:
    """Проверка триггеров синхронизации FTS при обновлении и удалении заметок."""
    note_id = memory_manager.add_note("Исходная заметка", tags=["черновик"])
    assert len(memory_manager.list_notes(query="Исходная")) == 1

    # Завершение заметки
    completed = memory_manager.complete_note(note_id)
    assert completed is True

    # В активных заметках больше не находится
    assert len(memory_manager.list_notes(only_active=True, query="Исходная")) == 0
    # При only_active=False находится
    all_notes = memory_manager.list_notes(only_active=False, query="Исходная")
    assert len(all_notes) == 1
    assert all_notes[0]["is_done"] is True

    # Удаление заметки
    deleted = memory_manager.delete_note(note_id)
    assert deleted is True

    # Заметка полностью удалена из основной таблицы и FTS
    assert len(memory_manager.list_notes(only_active=False, query="Исходная")) == 0
    assert memory_manager.delete_note(99999) is False
    assert memory_manager.complete_note(99999) is False


def test_log_turn_and_recent_history(memory_manager: MemoryManager) -> None:
    """Проверка логирования диалоговых шагов и извлечения истории."""
    memory_manager.log_turn(
        role="user",
        text="Какая погода завтра?",
        intent="weather",
        tool_calls=None,
    )
    tools: list[dict[str, Any]] = [{"name": "get_weather", "args": {"city": "Москва"}}]
    memory_manager.log_turn(
        role="assistant",
        text="Завтра в Москве ожидается +15°C.",
        intent="weather",
        tool_calls=tools,
    )

    history = memory_manager.get_recent_history(limit=10)
    assert len(history) == 2
    # Хронологический порядок: сначала user, затем assistant
    assert history[0]["role"] == "user"
    assert history[0]["text"] == "Какая погода завтра?"
    assert history[0]["intent"] == "weather"
    assert history[0]["tool_calls"] is None

    assert history[1]["role"] == "assistant"
    assert history[1]["text"] == "Завтра в Москве ожидается +15°C."
    assert history[1]["tool_calls"] == tools

    # Проверка ограничения limit
    recent_one = memory_manager.get_recent_history(limit=1)
    assert len(recent_one) == 1
    assert recent_one[0]["role"] == "assistant"

    # Проверка некорректного limit
    assert memory_manager.get_recent_history(limit=0) == []
    assert memory_manager.get_recent_history(limit=-5) == []


def test_format_system_prompt_context(memory_manager: MemoryManager) -> None:
    """Проверка форматирования markdown-контекста для LLM."""
    # Пустая база
    assert memory_manager.format_system_prompt_context() == ""
    assert memory_manager.format_system_prompt_context(max_facts=0) == ""

    # Заполнение фактов
    memory_manager.set_fact("имя", "Денис", category="general")
    memory_manager.set_fact("город", "Москва", category="location")
    memory_manager.set_fact("редактор", "VS Code", category="dev")

    context = memory_manager.format_system_prompt_context(max_facts=2)
    assert "### Известные факты о пользователе:" in context
    # Должно быть ровно 2 факта согласно max_facts
    lines = context.strip().split("\n")
    # 1 строка заголовок + 2 строки фактов
    assert len(lines) == 3


def test_context_manager(tmp_path: Path) -> None:
    """Проверка корректной работы менеджера контекста."""
    db_file = tmp_path / "ctx_mem.db"
    with MemoryManager(db_path=db_file) as mm:
        mm.set_fact("test_k", "test_v")
        assert mm.get_fact("test_k") == "test_v"


def test_thread_safety(tmp_path: Path) -> None:
    """Проверка потокобезопасности параллельных операций с базой данных."""
    db_file = tmp_path / "concurrent_mem.db"
    mgr = MemoryManager(db_path=db_file)

    def worker_insert_fact(idx: int) -> None:
        mgr.set_fact(f"worker_key_{idx}", f"val_{idx}", category="stress")

    def worker_insert_note(idx: int) -> None:
        mgr.add_note(f"Заметка потока #{idx}", tags=["concurrency", f"tag_{idx}"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = []
        for i in range(25):
            futures.append(executor.submit(worker_insert_fact, i))
            futures.append(executor.submit(worker_insert_note, i))
        for f in concurrent.futures.as_completed(futures):
            f.result()

    facts = mgr.list_facts(category="stress")
    assert len(facts) == 25

    notes = mgr.list_notes(only_active=True)
    assert len(notes) == 25

    mgr.close()
