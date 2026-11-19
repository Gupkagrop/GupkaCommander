"""Модуль инициализации и управления базой данных SQLite для долговременной памяти."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

# SQL-схема инициализации таблиц долговременной памяти и полнотекстового поиска FTS5
SCHEMA_SQL: str = """
-- Таблица структурированных фактов о пользователе и его предпочтениях
CREATE TABLE IF NOT EXISTS user_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    value TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_facts_category ON user_facts(category);
CREATE INDEX IF NOT EXISTS idx_user_facts_updated_at ON user_facts(updated_at);

-- Таблица заметок и задач пользователя
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    tags TEXT DEFAULT '',
    is_done INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_is_done ON notes(is_done);

-- Виртуальная таблица FTS5 для полнотекстового поиска по заметкам
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    content,
    tags,
    content='notes',
    content_rowid='id'
);

-- Триггеры автоматической синхронизации FTS5 индекса с таблицей notes
CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, content, tags)
    VALUES (new.id, new.content, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, content, tags)
    VALUES ('delete', old.id, old.content, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, content, tags)
    VALUES ('delete', old.id, old.content, old.tags);
    INSERT INTO notes_fts(rowid, content, tags)
    VALUES (new.id, new.content, new.tags);
END;

-- Таблица истории диалогов и вызовов инструментов
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    intent TEXT,
    tool_calls_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversations_created_at ON conversations(created_at);
"""


def get_utc_now_iso() -> str:
    """Возвращает текущую временную метку UTC в формате ISO-8601."""
    return datetime.now(UTC).isoformat()


def init_database(db_path: str | Path = "config/memory.db") -> sqlite3.Connection:
    """Инициализирует базу данных SQLite, настраивает прагмы и создает схему таблиц.

    Args:
        db_path: Путь к файлу БД SQLite или ':memory:' для работы в оперативной памяти.

    Returns:
        Активное подключение sqlite3.Connection с настроенным row_factory.
    """
    is_memory: bool = str(db_path) == ":memory:"

    if not is_memory:
        resolved_path: Path = Path(db_path).resolve()
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(resolved_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
    else:
        conn = sqlite3.connect(":memory:", check_same_thread=False)

    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()

    return conn
