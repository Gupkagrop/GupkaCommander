"""Модуль высокоуровневого управления долговременной памятью ассистента."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from core.memory.db import get_utc_now_iso, init_database, migrate_schema_v2, transaction


class MemoryManager:
    """Менеджер долговременной памяти ассистента.

    Обеспечивает хранение фактов, заметок с полнотекстовым поиском FTS5,
    истории диалогов, эпизодической памяти и сводок.
    """

    def __init__(
        self,
        db_path: str | Path = "config/memory.db",
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Инициализация менеджера памяти.

        Args:
            db_path: Путь к файлу базы данных SQLite или ':memory:'.
            connection: Готовое подключение sqlite3.Connection (для тестов или DI).
        """
        if connection is not None:
            self._conn = connection
            if isinstance(connection, sqlite3.Connection):
                migrate_schema_v2(self._conn)
        else:
            self._conn = init_database(db_path)
        self._lock: threading.RLock = threading.RLock()

    def close(self) -> None:
        """Закрывает активное подключение к базе данных."""
        with self._lock:
            self._conn.close()

    def __enter__(self) -> MemoryManager:
        """Поддержка контекстного менеджера."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Выход из контекстного менеджера с закрытием подключения."""
        self.close()

    def set_fact(self, key: str, value: str, category: str = "general") -> None:
        """Сохраняет или обновляет факт о пользователе (Upsert).

        Args:
            key: Уникальный строковый ключ факта.
            value: Значение факта.
            category: Категория факта (по умолчанию 'general').

        Raises:
            ValueError: Если ключ пустой.
        """
        clean_key: str = key.strip()
        clean_val: str = value.strip()
        if not clean_key:
            raise ValueError("Ключ факта не может быть пустым.")

        now_iso: str = get_utc_now_iso()
        clean_cat: str = (category or "general").strip()

        with self._lock, transaction(self._conn):
            self._conn.execute(
                """
                INSERT INTO user_facts (key, value, category, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    category = excluded.category,
                    updated_at = excluded.updated_at
                """,
                (clean_key, clean_val, clean_cat, now_iso, now_iso),
            )

    def get_fact(self, key: str) -> str | None:
        """Получает значение факта по ключу.

        Args:
            key: Ключ факта.

        Returns:
            Текст факта или None, если факт не найден.
        """
        clean_key: str = key.strip()
        if not clean_key:
            return None

        with self._lock:
            cursor = self._conn.execute(
                "SELECT value FROM user_facts WHERE key = ?",
                (clean_key,),
            )
            row = cursor.fetchone()
            if row is not None:
                return str(row["value"])

            # Запасной поиск без притяжательных местоимений (например, "мой редактор" -> "редактор")
            stripped_key = re.sub(
                r"^(?:мой|моя|моё|мои)\s+", "", clean_key, flags=re.IGNORECASE
            ).strip()
            if stripped_key and stripped_key != clean_key:
                cursor = self._conn.execute(
                    "SELECT value FROM user_facts WHERE key = ?",
                    (stripped_key,),
                )
                row = cursor.fetchone()
                if row is not None:
                    return str(row["value"])

            # Запасной поиск с префиксом "мой / моя" если факт сохранен с префиксом
            cursor = self._conn.execute(
                "SELECT value FROM user_facts WHERE key = ? OR key = ?",
                (f"мой {clean_key}", f"моя {clean_key}"),
            )
            row = cursor.fetchone()
            if row is not None:
                return str(row["value"])

            return None

    def list_facts(self, category: str | None = None) -> dict[str, str]:
        """Возвращает словарь фактов, опционально фильтруя по категории.

        Args:
            category: Категория для фильтрации или None для всех категорий.

        Returns:
            Словарь {ключ: значение}.
        """
        with self._lock:
            if category and category.strip():
                cursor = self._conn.execute(
                    "SELECT key, value FROM user_facts WHERE category = ? ORDER BY key ASC",
                    (category.strip(),),
                )
            else:
                cursor = self._conn.execute("SELECT key, value FROM user_facts ORDER BY key ASC")
            rows = cursor.fetchall()
            return {str(row["key"]): str(row["value"]) for row in rows}

    def delete_fact(self, key: str) -> bool:
        """Удаляет факт по ключу.

        Args:
            key: Ключ удаляемого факта.

        Returns:
            True, если факт был найден и удален, иначе False.
        """
        clean_key: str = key.strip()
        if not clean_key:
            return False

        with self._lock, transaction(self._conn):
            cursor = self._conn.execute(
                "DELETE FROM user_facts WHERE key = ?",
                (clean_key,),
            )
            return cursor.rowcount > 0

    def add_note(self, content: str, tags: list[str] | None = None) -> int:
        """Создает новую заметку.

        Args:
            content: Текст заметки.
            tags: Опциональный список строковых тегов.

        Returns:
            Идентификатор созданной заметки (id).

        Raises:
            ValueError: Если текст заметки пустой.
        """
        clean_content: str = content.strip()
        if not clean_content:
            raise ValueError("Содержимое заметки не может быть пустым.")

        clean_tags: list[str] = [t.strip() for t in tags if t and t.strip()] if tags else []
        tags_str: str = " ".join(clean_tags)
        now_iso: str = get_utc_now_iso()

        with self._lock, transaction(self._conn):
            cursor = self._conn.execute(
                """
                INSERT INTO notes (content, tags, is_done, created_at, updated_at)
                VALUES (?, ?, 0, ?, ?)
                """,
                (clean_content, tags_str, now_iso, now_iso),
            )
            return int(cursor.lastrowid or 0)

    @staticmethod
    def _sanitize_fts_query(query: str) -> str:
        """Формирует безопасную поисковую строку для запроса в FTS5."""
        tokens: list[str] = re.findall(r"[\w]+", query, flags=re.UNICODE)
        if not tokens:
            return ""
        return " ".join(f'"{token}"*' for token in tokens)

    @staticmethod
    def _row_to_note_dict(row: sqlite3.Row) -> dict[str, Any]:
        """Преобразует строку SQLite в структурированный словарь заметки."""
        raw_tags: str = str(row["tags"]) if row["tags"] else ""
        return {
            "id": int(row["id"]),
            "content": str(row["content"]),
            "tags": [t for t in raw_tags.split() if t],
            "tags_str": raw_tags,
            "is_done": bool(row["is_done"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def list_notes(
        self,
        only_active: bool = True,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        """Возвращает список заметок с фильтрацией по статусу и поисковому запросу.

        Args:
            only_active: Если True, возвращаются только невыполненные заметки (is_done=0).
            query: Текстовый поисковый запрос (использует FTS5 с откатом на LIKE).

        Returns:
            Список словарей с полями заметки.
        """
        clean_q: str = query.strip() if query else ""
        with self._lock:
            if clean_q:
                return self._search_notes(clean_q, only_active=only_active)
            return self._fetch_all_notes(only_active=only_active)

    def _fetch_all_notes(self, only_active: bool) -> list[dict[str, Any]]:
        """Извлекает заметки без поискового запроса."""
        sql: str = "SELECT id, content, tags, is_done, created_at, updated_at FROM notes"
        if only_active:
            sql += " WHERE is_done = 0"
        sql += " ORDER BY id DESC"

        cursor = self._conn.execute(sql)
        return [self._row_to_note_dict(row) for row in cursor.fetchall()]

    def _search_notes(self, query: str, only_active: bool) -> list[dict[str, Any]]:
        """Выполняет поиск заметок через FTS5 с откатом на LIKE."""
        fts_query: str = self._sanitize_fts_query(query)
        if fts_query:
            try:
                fts_sql: str = """
                    SELECT n.id, n.content, n.tags, n.is_done, n.created_at, n.updated_at
                    FROM notes n
                    JOIN notes_fts ON n.id = notes_fts.rowid
                    WHERE notes_fts MATCH ?
                """
                if only_active:
                    fts_sql += " AND n.is_done = 0"
                fts_sql += " ORDER BY n.id DESC"

                cursor = self._conn.execute(fts_sql, (fts_query,))
                rows = cursor.fetchall()
                if rows:
                    return [self._row_to_note_dict(row) for row in rows]
            except sqlite3.OperationalError:
                pass

        # Резервный поиск через LIKE при несовпадении или ошибке синтаксиса FTS
        like_sql: str = """
            SELECT id, content, tags, is_done, created_at, updated_at
            FROM notes
            WHERE (content LIKE ? OR tags LIKE ?)
        """
        if only_active:
            like_sql += " AND is_done = 0"
        like_sql += " ORDER BY id DESC"

        pattern: str = f"%{query}%"
        cursor = self._conn.execute(like_sql, (pattern, pattern))
        return [self._row_to_note_dict(row) for row in cursor.fetchall()]

    def complete_note(self, note_id: int) -> bool:
        """Отмечает заметку как выполненную (is_done=1).

        Args:
            note_id: Идентификатор заметки.

        Returns:
            True, если статус заметки обновлен, иначе False.
        """
        now_iso: str = get_utc_now_iso()
        with self._lock, transaction(self._conn):
            cursor = self._conn.execute(
                "UPDATE notes SET is_done = 1, updated_at = ? WHERE id = ?",
                (now_iso, note_id),
            )
            return cursor.rowcount > 0

    def delete_note(self, note_id: int) -> bool:
        """Удаляет заметку по идентификатору.

        Args:
            note_id: Идентификатор заметки.

        Returns:
            True, если заметка удалена, иначе False.
        """
        with self._lock, transaction(self._conn):
            cursor = self._conn.execute(
                "DELETE FROM notes WHERE id = ?",
                (note_id,),
            )
            return cursor.rowcount > 0

    def log_turn(
        self,
        role: str,
        text: str,
        intent: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        """Записывает диалоговый шаг в историю разговоров.

        Args:
            role: Роль участника диалога ('user', 'assistant', 'system').
            text: Текст сообщения.
            intent: Распознанное намерение команды пользователя.
            tool_calls: Список словарей вызванных инструментов или None.
        """
        now_iso: str = get_utc_now_iso()
        tool_calls_json: str | None = (
            json.dumps(tool_calls, ensure_ascii=False) if tool_calls is not None else None
        )

        with self._lock, transaction(self._conn):
            self._conn.execute(
                """
                INSERT INTO conversations (role, text, intent, tool_calls_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (role, text, intent, tool_calls_json, now_iso),
            )

    def get_recent_history(self, limit: int = 10) -> list[dict[str, Any]]:
        """Возвращает последние диалоговые шаги в хронологическом порядке.

        Args:
            limit: Максимальное количество последних записей.

        Returns:
            Список словарей ходов диалога от старых к новым.
        """
        if limit <= 0:
            return []

        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT id, role, text, intent, tool_calls_json, created_at
                FROM conversations
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            history: list[dict[str, Any]] = []

            for row in reversed(rows):
                tool_calls: list[dict[str, Any]] | None = None
                raw_json = row["tool_calls_json"]
                if raw_json:
                    try:
                        tool_calls = json.loads(str(raw_json))
                    except json.JSONDecodeError:
                        tool_calls = None

                history.append(
                    {
                        "id": int(row["id"]),
                        "role": str(row["role"]),
                        "text": str(row["text"]),
                        "intent": str(row["intent"]) if row["intent"] is not None else None,
                        "tool_calls": tool_calls,
                        "created_at": str(row["created_at"]),
                    }
                )
            return history

    def format_system_prompt_context(self, max_facts: int = 15) -> str:
        """Форматирует сохраненные факты о пользователе в markdown-блок для LLM.

        Args:
            max_facts: Максимальное количество фактов в блоке.

        Returns:
            Строка markdown или пустая строка, если фактов нет.
        """
        if max_facts <= 0:
            return ""

        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT key, value, category
                FROM user_facts
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (max_facts,),
            )
            rows = cursor.fetchall()
            if not rows:
                return ""

            lines: list[str] = ["### Известные факты о пользователе:"]
            for row in rows:
                cat: str = str(row["category"]) if row["category"] else "general"
                cat_label: str = f" ({cat})" if cat != "general" else ""
                lines.append(f"- {row['key']}{cat_label}: {row['value']}")
            return "\n".join(lines)

    @staticmethod
    def _sanitize_fts_or_query(query: str) -> str:
        """Формирует мягкий FTS5-запрос с оператором OR по токенам."""
        tokens: list[str] = re.findall(r"[\w]+", query, flags=re.UNICODE)
        filtered: list[str] = [tok for tok in tokens if len(tok) >= 2] or tokens
        if not filtered:
            return ""
        return " OR ".join(f'"{token}"*' for token in filtered)

    @staticmethod
    def _truncate_text(text: str, max_len: int) -> str:
        """Компактно обрезает длинную строку с многоточием для экономии токенов."""
        clean = " ".join(text.split())
        if len(clean) <= max_len:
            return clean
        return f"{clean[: max_len - 3].rstrip()}..."

    @staticmethod
    def _row_to_episode_dict(row: sqlite3.Row) -> dict[str, Any]:
        """Преобразует строку таблицы task_episodes в структурированный словарь."""
        steps: list[dict[str, Any]] = []
        raw_steps = row["steps_json"]
        if raw_steps:
            try:
                parsed = json.loads(str(raw_steps))
                if isinstance(parsed, list):
                    steps = [item for item in parsed if isinstance(item, dict)]
            except json.JSONDecodeError:
                steps = []

        return {
            "id": int(row["id"]),
            "goal": str(row["goal"]),
            "steps": steps,
            "outcome": str(row["outcome"]),
            "app_context": str(row["app_context"] or ""),
            "success": bool(row["success"]),
            "created_at": str(row["created_at"]),
        }

    def build_chat_messages(self, limit: int = 8) -> list[dict[str, str]]:
        """Формирует список недавних сообщений в формате OpenAI Chat Completions.

        Args:
            limit: Максимальное количество последних реплик user/assistant.

        Returns:
            Список словарей вида [{'role': 'user'|'assistant', 'content': '...'}].
        """
        if limit <= 0:
            return []

        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT role, text
                FROM conversations
                WHERE role IN ('user', 'assistant') AND TRIM(text) != ''
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            return [
                {"role": str(row["role"]), "content": str(row["text"])} for row in reversed(rows)
            ]

    def search_conversations(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Ищет релевантные сообщения в истории диалогов через FTS5 с откатом на LIKE.

        Args:
            query: Поисковый запрос по тексту диалогов.
            limit: Максимальное число возвращаемых записей.

        Returns:
            Список словарей найденных реплик.
        """
        clean_q = query.strip()
        if not clean_q or limit <= 0:
            return []

        with self._lock:
            fts_query = self._sanitize_fts_query(clean_q)
            if fts_query:
                try:
                    cursor = self._conn.execute(
                        """
                        SELECT c.id, c.role, c.text, c.intent, c.created_at
                        FROM conversations c
                        JOIN conversations_fts ON c.id = conversations_fts.rowid
                        WHERE conversations_fts MATCH ?
                        ORDER BY bm25(conversations_fts), c.id DESC
                        LIMIT ?
                        """,
                        (fts_query, limit),
                    )
                    rows = cursor.fetchall()
                    if rows:
                        return [
                            {
                                "id": int(r["id"]),
                                "role": str(r["role"]),
                                "text": str(r["text"]),
                                "intent": str(r["intent"]) if r["intent"] is not None else None,
                                "created_at": str(r["created_at"]),
                            }
                            for r in rows
                        ]
                except sqlite3.OperationalError:
                    pass

            cursor = self._conn.execute(
                """
                SELECT id, role, text, intent, created_at
                FROM conversations
                WHERE text LIKE ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (f"%{clean_q}%", limit),
            )
            return [
                {
                    "id": int(r["id"]),
                    "role": str(r["role"]),
                    "text": str(r["text"]),
                    "intent": str(r["intent"]) if r["intent"] is not None else None,
                    "created_at": str(r["created_at"]),
                }
                for r in cursor.fetchall()
            ]

    def save_episode(
        self,
        goal: str,
        steps: list[dict[str, Any]],
        outcome: str,
        success: bool,
        app_context: str = "",
    ) -> int:
        """Сохраняет эпизод выполнения задачи в эпизодическую память.

        Args:
            goal: Исходная цель пользователя.
            steps: Список выполненных шагов (словари с инструментами и результатами).
            outcome: Итоговый результат или сообщение об ошибке.
            success: Флаг успешного выполнения задачи.
            app_context: Контекст активного окна или приложения.

        Returns:
            Идентификатор сохраненного эпизода.

        Raises:
            ValueError: Если цель эпизода пустая.
        """
        clean_goal: str = goal.strip()
        if not clean_goal:
            raise ValueError("Цель эпизода не может быть пустой.")

        steps_json: str = json.dumps(steps, ensure_ascii=False)
        clean_outcome: str = outcome.strip()
        clean_context: str = (app_context or "").strip()
        success_int: int = 1 if success else 0
        now_iso: str = get_utc_now_iso()

        with self._lock, transaction(self._conn):
            cursor = self._conn.execute(
                """
                INSERT INTO task_episodes (
                    goal, steps_json, outcome, app_context, success, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (clean_goal, steps_json, clean_outcome, clean_context, success_int, now_iso),
            )
            return int(cursor.lastrowid or 0)

    def _query_episodes_fts(self, fts_query: str, limit: int) -> list[sqlite3.Row]:
        """Выполняет ранжированный поиск эпизодов через FTS5."""
        if not fts_query:
            return []
        try:
            cursor = self._conn.execute(
                """
                SELECT e.id, e.goal, e.steps_json, e.outcome, e.app_context, e.success, e.created_at
                FROM task_episodes e
                JOIN task_episodes_fts ON e.id = task_episodes_fts.rowid
                WHERE task_episodes_fts MATCH ?
                ORDER BY bm25(task_episodes_fts), e.success DESC, e.id DESC
                LIMIT ?
                """,
                (fts_query, limit),
            )
            return cursor.fetchall()
        except sqlite3.OperationalError:
            return []

    def recall_episodes(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        """Возвращает релевантные прошлые эпизоды выполнения задач.

        Args:
            query: Текст цели или контекста для поиска похожего опыта.
            limit: Максимальное количество эпизодов.

        Returns:
            Список словарей эпизодов с разобранным полем steps.
        """
        if limit <= 0:
            return []

        clean_q: str = query.strip()
        with self._lock:
            if not clean_q:
                cursor = self._conn.execute(
                    """
                    SELECT id, goal, steps_json, outcome, app_context, success, created_at
                    FROM task_episodes
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                return [self._row_to_episode_dict(row) for row in cursor.fetchall()]

            # Сначала ищем строго по всем ключевым словам, затем смягчаем до OR
            rows = self._query_episodes_fts(self._sanitize_fts_query(clean_q), limit)
            if not rows:
                or_query = self._sanitize_fts_or_query(clean_q)
                if " OR " in or_query:
                    rows = self._query_episodes_fts(or_query, limit)

            if rows:
                return [self._row_to_episode_dict(row) for row in rows]

            pattern: str = f"%{clean_q}%"
            cursor = self._conn.execute(
                """
                SELECT id, goal, steps_json, outcome, app_context, success, created_at
                FROM task_episodes
                WHERE goal LIKE ? OR outcome LIKE ? OR app_context LIKE ?
                ORDER BY success DESC, id DESC
                LIMIT ?
                """,
                (pattern, pattern, pattern, limit),
            )
            return [self._row_to_episode_dict(row) for row in cursor.fetchall()]

    def save_summary(self, summary: str, turn_range: str) -> int:
        """Сохраняет сжатую сводку диалога в таблицу memory_summaries."""
        clean_summary = summary.strip()
        if not clean_summary:
            raise ValueError("Текст сводки не может быть пустым.")

        now_iso = get_utc_now_iso()
        with self._lock, transaction(self._conn):
            cursor = self._conn.execute(
                """
                INSERT INTO memory_summaries (summary, turn_range, created_at)
                VALUES (?, ?, ?)
                """,
                (clean_summary, turn_range.strip(), now_iso),
            )
            return int(cursor.lastrowid or 0)

    def get_recent_summaries(self, limit: int = 3) -> list[dict[str, Any]]:
        """Возвращает последние сохраненные сводки диалогов."""
        if limit <= 0:
            return []
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT id, summary, turn_range, created_at
                FROM memory_summaries
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [
                {
                    "id": int(r["id"]),
                    "summary": str(r["summary"]),
                    "turn_range": str(r["turn_range"]),
                    "created_at": str(r["created_at"]),
                }
                for r in cursor.fetchall()
            ]

    def _format_active_notes_compact(self, max_notes: int = 5) -> str:
        """Форматирует активные заметки в компактный блок для контекста 4B-модели."""
        notes = self.list_notes(only_active=True)[:max_notes]
        if not notes:
            return ""
        lines: list[str] = ["### Активные заметки:"]
        for note in notes:
            snippet = self._truncate_text(str(note["content"]), 100)
            lines.append(f"- [#{note['id']}] {snippet}")
        return "\n".join(lines)

    def _format_episodes_compact(self, goal: str, episode_limit: int) -> str:
        """Форматирует похожие прошлые эпизоды в компактный блок для планировщика."""
        if not goal.strip() or episode_limit <= 0:
            return ""
        episodes = self.recall_episodes(goal, limit=episode_limit)
        if not episodes:
            return ""
        lines: list[str] = ["### Похожий прошлый опыт:"]
        for ep in episodes:
            status_tag = "Успех" if ep["success"] else "Ошибка"
            ep_goal = self._truncate_text(str(ep["goal"]), 80)
            ep_outcome = self._truncate_text(str(ep["outcome"]), 100)
            ctx = f" [{ep['app_context']}]" if ep["app_context"] else ""
            lines.append(
                f"- [{status_tag}]{ctx} {ep_goal} -> {ep_outcome} (шагов: {len(ep['steps'])})"
            )
        return "\n".join(lines)

    def build_planner_context(self, goal: str, episode_limit: int = 3) -> str:
        """Собирает компактный контекст для планировщика (факты + заметки + опыт).

        Оптимизирован под небольшой бюджет контекста локальной 4B-модели.

        Args:
            goal: Текущая цель пользователя для подбора релевантных эпизодов.
            episode_limit: Максимальное число прошлых эпизодов в контексте.

        Returns:
            Компактная markdown-строка или пустая строка, если данных нет.
        """
        sections: list[str] = []
        facts_block = self.format_system_prompt_context(max_facts=10)
        if facts_block:
            sections.append(facts_block)

        notes_block = self._format_active_notes_compact(max_notes=5)
        if notes_block:
            sections.append(notes_block)

        episodes_block = self._format_episodes_compact(goal, episode_limit)
        if episodes_block:
            sections.append(episodes_block)

        return "\n\n".join(sections)
