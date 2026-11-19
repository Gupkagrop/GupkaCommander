"""Тесты базы данных SQLite для подсистемы долговременной памяти."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.memory.db import get_utc_now_iso, init_database


def test_init_database_in_memory() -> None:
    """Проверка инициализации схемы базы данных в оперативной памяти."""
    conn: sqlite3.Connection = init_database(":memory:")
    try:
        cursor = conn.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY name"
        )
        tables: dict[str, str] = {row["name"]: row["type"] for row in cursor.fetchall()}

        assert "user_facts" in tables
        assert "notes" in tables
        assert "notes_fts" in tables
        assert "conversations" in tables
    finally:
        conn.close()


def test_init_database_file_wal_and_fk(tmp_path: Path) -> None:
    """Проверка создания файла БД, включения WAL-режима и foreign keys."""
    db_file: Path = tmp_path / "memory_store" / "storage.db"
    assert not db_file.parent.exists()

    conn: sqlite3.Connection = init_database(db_file)
    try:
        assert db_file.exists()

        fk_cursor = conn.execute("PRAGMA foreign_keys;")
        fk_val = fk_cursor.fetchone()[0]
        assert fk_val == 1

        wal_cursor = conn.execute("PRAGMA journal_mode;")
        journal_mode: str = str(wal_cursor.fetchone()[0]).lower()
        assert journal_mode == "wal"
    finally:
        conn.close()


def test_user_facts_unique_key_constraint() -> None:
    """Проверка ограничения уникальности UNIQUE для ключа в user_facts."""
    conn: sqlite3.Connection = init_database(":memory:")
    try:
        now_iso: str = get_utc_now_iso()
        conn.execute(
            """
            INSERT INTO user_facts (key, value, category, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("theme", "dark", "preferences", now_iso, now_iso),
        )
        conn.commit()

        with pytest.raises(
            sqlite3.IntegrityError, match="UNIQUE constraint failed: user_facts.key"
        ):
            conn.execute(
                """
                INSERT INTO user_facts (key, value, category, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("theme", "light", "preferences", now_iso, now_iso),
            )
    finally:
        conn.close()


def test_notes_fts_triggers_insert() -> None:
    """Проверка триггера notes_ai: автоматическое добавление в notes_fts при INSERT."""
    conn: sqlite3.Connection = init_database(":memory:")
    try:
        now_iso: str = get_utc_now_iso()
        cursor = conn.execute(
            """
            INSERT INTO notes (content, tags, is_done, created_at, updated_at)
            VALUES (?, ?, 0, ?, ?)
            """,
            ("Купить молоко и хлеб", "покупки продукты", now_iso, now_iso),
        )
        note_id: int = int(cursor.lastrowid or 0)
        conn.commit()

        fts_cursor = conn.execute(
            "SELECT rowid, content, tags FROM notes_fts WHERE notes_fts MATCH ?",
            ('"молоко"*',),
        )
        matches = fts_cursor.fetchall()
        assert len(matches) == 1
        assert matches[0]["rowid"] == note_id
        assert "молоко" in matches[0]["content"]
    finally:
        conn.close()


def test_notes_fts_triggers_update() -> None:
    """Проверка триггера notes_au: обновление notes_fts при изменении notes."""
    conn: sqlite3.Connection = init_database(":memory:")
    try:
        now_iso: str = get_utc_now_iso()
        cursor = conn.execute(
            """
            INSERT INTO notes (content, tags, is_done, created_at, updated_at)
            VALUES (?, ?, 0, ?, ?)
            """,
            ("Первоначальный текст задачи", "работа", now_iso, now_iso),
        )
        note_id: int = int(cursor.lastrowid or 0)
        conn.commit()

        # Проверяем старый поиск
        old_search = conn.execute(
            "SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?",
            ('"первоначальный"*',),
        ).fetchall()
        assert len(old_search) == 1

        # Обновляем заметку
        conn.execute(
            "UPDATE notes SET content = ?, tags = ? WHERE id = ?",
            ("Срочно исправить баг в UI", "баги срочно", note_id),
        )
        conn.commit()

        # Старый термин больше не должен находиться
        old_search_after = conn.execute(
            "SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?",
            ('"первоначальный"*',),
        ).fetchall()
        assert len(old_search_after) == 0

        # Новый термин должен находиться
        new_search = conn.execute(
            "SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?",
            ('"исправить"*',),
        ).fetchall()
        assert len(new_search) == 1
        assert new_search[0]["rowid"] == note_id
    finally:
        conn.close()


def test_notes_fts_triggers_delete() -> None:
    """Проверка триггера notes_ad: удаление из notes_fts при DELETE из notes."""
    conn: sqlite3.Connection = init_database(":memory:")
    try:
        now_iso: str = get_utc_now_iso()
        cursor = conn.execute(
            """
            INSERT INTO notes (content, tags, is_done, created_at, updated_at)
            VALUES (?, ?, 0, ?, ?)
            """,
            ("Секретная заметка для удаления", "секреты", now_iso, now_iso),
        )
        note_id: int = int(cursor.lastrowid or 0)
        conn.commit()

        # Убедимся, что запись в FTS есть
        fts_before = conn.execute(
            "SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?",
            ('"секретная"*',),
        ).fetchall()
        assert len(fts_before) == 1

        # Удаляем из notes
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        conn.commit()

        # Проверяем, что из FTS удалилась синхронно
        fts_after = conn.execute(
            "SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?",
            ('"секретная"*',),
        ).fetchall()
        assert len(fts_after) == 0
    finally:
        conn.close()


def test_conversations_table_crud() -> None:
    """Проверка CRUD операций с таблицей conversations."""
    conn: sqlite3.Connection = init_database(":memory:")
    try:
        now_iso: str = get_utc_now_iso()
        conn.execute(
            """
            INSERT INTO conversations (role, text, intent, tool_calls_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("user", "Привет, Гупка", "general_greeting", '{"action": "greet"}', now_iso),
        )
        conn.commit()

        cursor = conn.execute(
            "SELECT role, text, intent, tool_calls_json FROM conversations WHERE role = ?",
            ("user",),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row["role"] == "user"
        assert row["text"] == "Привет, Гупка"
        assert row["intent"] == "general_greeting"
        assert '{"action": "greet"}' in row["tool_calls_json"]
    finally:
        conn.close()
