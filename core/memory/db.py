"""Модуль инициализации и управления базой данных SQLite для долговременной памяти."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION: int = 2

# Базовая SQL-схема v1: факты о пользователе, заметки (с FTS5) и диалоги
SCHEMA_V1_SQL: str = """
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

# Расширение SQL-схемы v2: FTS5 для диалогов, эпизодическая память и сводки
SCHEMA_V2_SQL: str = """
-- Виртуальная таблица FTS5 для полнотекстового поиска по истории диалогов
CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts USING fts5(
    text,
    content='conversations',
    content_rowid='id'
);

-- Триггеры синхронизации FTS5 индекса с таблицей conversations
CREATE TRIGGER IF NOT EXISTS conversations_ai AFTER INSERT ON conversations BEGIN
    INSERT INTO conversations_fts(rowid, text)
    VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS conversations_ad AFTER DELETE ON conversations BEGIN
    INSERT INTO conversations_fts(conversations_fts, rowid, text)
    VALUES ('delete', old.id, old.text);
END;

CREATE TRIGGER IF NOT EXISTS conversations_au AFTER UPDATE ON conversations BEGIN
    INSERT INTO conversations_fts(conversations_fts, rowid, text)
    VALUES ('delete', old.id, old.text);
    INSERT INTO conversations_fts(rowid, text)
    VALUES (new.id, new.text);
END;

-- Таблица эпизодической памяти выполненных многошаговых задач
CREATE TABLE IF NOT EXISTS task_episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    outcome TEXT NOT NULL,
    app_context TEXT DEFAULT '',
    success INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_episodes_created_at ON task_episodes(created_at);
CREATE INDEX IF NOT EXISTS idx_task_episodes_success ON task_episodes(success);

-- Виртуальная таблица FTS5 для поиска похожего опыта по цели, результату и контексту
CREATE VIRTUAL TABLE IF NOT EXISTS task_episodes_fts USING fts5(
    goal,
    outcome,
    app_context,
    content='task_episodes',
    content_rowid='id'
);

-- Триггеры синхронизации FTS5 индекса с таблицей task_episodes
CREATE TRIGGER IF NOT EXISTS task_episodes_ai AFTER INSERT ON task_episodes BEGIN
    INSERT INTO task_episodes_fts(rowid, goal, outcome, app_context)
    VALUES (new.id, new.goal, new.outcome, new.app_context);
END;

CREATE TRIGGER IF NOT EXISTS task_episodes_ad AFTER DELETE ON task_episodes BEGIN
    INSERT INTO task_episodes_fts(task_episodes_fts, rowid, goal, outcome, app_context)
    VALUES ('delete', old.id, old.goal, old.outcome, old.app_context);
END;

CREATE TRIGGER IF NOT EXISTS task_episodes_au AFTER UPDATE ON task_episodes BEGIN
    INSERT INTO task_episodes_fts(task_episodes_fts, rowid, goal, outcome, app_context)
    VALUES ('delete', old.id, old.goal, old.outcome, old.app_context);
    INSERT INTO task_episodes_fts(rowid, goal, outcome, app_context)
    VALUES (new.id, new.goal, new.outcome, new.app_context);
END;

-- Таблица сжатых сводок диалога для экономии контекстного окна
CREATE TABLE IF NOT EXISTS memory_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary TEXT NOT NULL,
    turn_range TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_summaries_created_at ON memory_summaries(created_at);
"""

# Полная актуальная схема инициализации базы данных
SCHEMA_SQL: str = f"{SCHEMA_V1_SQL}\n{SCHEMA_V2_SQL}"


def get_utc_now_iso() -> str:
    """Возвращает текущую временную метку UTC в формате ISO-8601."""
    return datetime.now(UTC).isoformat()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Контекстный менеджер транзакции с автоматическим commit и rollback при сбоях."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def migrate_schema_v2(conn: sqlite3.Connection) -> None:
    """Выполняет идемпотентную миграцию базы данных до схемы v2.

    Создает виртуальные таблицы FTS5 для диалогов и эпизодов, таблицу сводок,
    синхронизирующие триггеры, а также индексирует ранее сохраненные записи.

    Args:
        conn: Активное подключение к базе данных SQLite.
    """
    version_row = conn.execute("PRAGMA user_version;").fetchone()
    current_version: int = int(version_row[0]) if version_row is not None else 0
    if current_version >= SCHEMA_VERSION:
        return

    conn.executescript(SCHEMA_V2_SQL)
    conn.execute("INSERT INTO conversations_fts(conversations_fts) VALUES ('rebuild');")
    conn.execute("INSERT INTO task_episodes_fts(task_episodes_fts) VALUES ('rebuild');")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")
    conn.commit()


def init_database(db_path: str | Path = "config/memory.db") -> sqlite3.Connection:
    """Инициализирует базу данных SQLite, настраивает прагмы и создает схему таблиц.

    Args:
        db_path: Путь к файлу БД SQLite или ':memory:' для работы в оперативной памяти.

    Returns:
        Активное подключение sqlite3.Connection с настроенным row_factory.
    """
    is_memory: bool = str(db_path) == ":memory:"

    if not is_memory:
        if str(db_path) == "config/memory.db":
            from core.paths import get_database_path

            resolved_path = get_database_path("memory.db")
        else:
            resolved_path = Path(db_path).resolve()
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(resolved_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
    else:
        conn = sqlite3.connect(":memory:", check_same_thread=False)

    try:
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)
        migrate_schema_v2(conn)
    except Exception:
        conn.rollback()
        raise

    return conn
