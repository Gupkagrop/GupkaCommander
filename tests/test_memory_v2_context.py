"""Тесты схемы памяти v2, эпизодического поиска и многоходового контекста Qwen3.5."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.brain.llm_client import (
    QWEN35_MODEL_NAME,
    LocalLLMClient,
    ThinkTagFilter,
    strip_thinking_tags,
)
from core.memory.db import (
    SCHEMA_V1_SQL,
    SCHEMA_VERSION,
    get_utc_now_iso,
    init_database,
    migrate_schema_v2,
)
from core.memory.memory_manager import MemoryManager


@pytest.fixture
def mgr() -> Iterator[MemoryManager]:
    """Фикстура изолированного менеджера памяти v2 в оперативной памяти."""
    instance = MemoryManager(db_path=":memory:")
    yield instance
    instance.close()


class TestSchemaV2Migration:
    """Тестирование миграции базы данных SQLite до схемы v2."""

    def test_fresh_db_has_v2_tables_and_version(self) -> None:
        """Свежая БД содержит все таблицы v2, FTS5 и user_version=2."""
        conn = init_database(":memory:")
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
            names = {str(r["name"]) for r in rows}
            assert {
                "user_facts",
                "notes",
                "notes_fts",
                "conversations",
                "conversations_fts",
                "task_episodes",
                "task_episodes_fts",
                "memory_summaries",
            }.issubset(names)

            version = int(conn.execute("PRAGMA user_version;").fetchone()[0])
            assert version == SCHEMA_VERSION
        finally:
            conn.close()

    def test_migration_from_v1_preserves_data_and_backfills_fts(self, tmp_path: Path) -> None:
        """Миграция существующей БД v1 сохраняет данные и индексирует старые диалоги."""
        db_file = tmp_path / "legacy_v1.db"
        now_iso = get_utc_now_iso()

        legacy_conn = sqlite3.connect(str(db_file))
        legacy_conn.executescript(SCHEMA_V1_SQL)
        legacy_conn.execute(
            "INSERT INTO user_facts (key, value, category, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("имя", "Денис", "profile", now_iso, now_iso),
        )
        legacy_conn.execute(
            "INSERT INTO conversations (role, text, intent, tool_calls_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("user", "Настрой проект GupkaCommander на Python", "ai_cognitive", None, now_iso),
        )
        legacy_conn.commit()
        legacy_conn.close()

        # Открываем через init_database (запускает миграцию v2) и повторно для идемпотентности
        migrated_conn = init_database(db_file)
        migrate_schema_v2(migrated_conn)
        migrated_conn.close()

        with MemoryManager(db_path=db_file) as mm:
            assert mm.get_fact("имя") == "Денис"
            hits = mm.search_conversations("GupkaCommander")
            assert len(hits) == 1
            assert "Python" in hits[0]["text"]

    def test_memory_manager_migrates_injected_v1_connection(self) -> None:
        """MemoryManager автоматически мигрирует переданное DI-подключение v1."""
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_V1_SQL)
        now_iso = get_utc_now_iso()
        conn.execute(
            "INSERT INTO conversations (role, text, intent, tool_calls_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("assistant", "Уведомление от системы безопасности", None, None, now_iso),
        )
        conn.commit()

        with MemoryManager(connection=conn) as mm:
            hits = mm.search_conversations("безопасности")
            assert len(hits) == 1
            ep_id = mm.save_episode(
                goal="Проверить журнал",
                steps=[{"tool": "run_command", "ok": True}],
                outcome="Готово",
                success=True,
            )
            assert ep_id > 0


class TestConversationsFtsAndChatMessages:
    """Тестирование FTS5 по диалогам, сводок и сборки истории сообщений."""

    def test_conversations_fts_sync_insert_update_delete(self, mgr: MemoryManager) -> None:
        """Триггеры conversations_fts корректно синхронизируют вставку, обновление и удаление."""
        mgr.log_turn("user", "Запусти браузер Chrome и открой почту")
        assert len(mgr.search_conversations("Chrome")) == 1

        mgr._conn.execute(
            "UPDATE conversations SET text = ? WHERE id = 1",
            ("Запусти браузер Edge и открой календарь",),
        )
        mgr._conn.commit()
        assert len(mgr.search_conversations("Chrome")) == 0
        assert len(mgr.search_conversations("Edge")) == 1

        mgr._conn.execute("DELETE FROM conversations WHERE id = 1")
        mgr._conn.commit()
        assert len(mgr.search_conversations("Edge")) == 0

    def test_build_chat_messages_filters_and_orders(self, mgr: MemoryManager) -> None:
        """build_chat_messages возвращает только user/assistant в хронологическом порядке."""
        mgr.log_turn("system", "Системная отладка")
        mgr.log_turn("user", "   ")
        mgr.log_turn("user", "Привет!")
        mgr.log_turn("assistant", "Привет, чем помочь?")
        mgr.log_turn("user", "Открой блокнот")
        mgr.log_turn("assistant", "Открываю Блокнот.")

        assert mgr.build_chat_messages(limit=0) == []
        recent_two = mgr.build_chat_messages(limit=2)
        assert recent_two == [
            {"role": "user", "content": "Открой блокнот"},
            {"role": "assistant", "content": "Открываю Блокнот."},
        ]

        all_valid = mgr.build_chat_messages(limit=10)
        assert len(all_valid) == 4
        assert all_valid[0]["content"] == "Привет!"

    def test_memory_summaries_crud(self, mgr: MemoryManager) -> None:
        """Проверка сохранения и чтения сжатых сводок диалога."""
        with pytest.raises(ValueError, match="Текст сводки не может быть пустым"):
            mgr.save_summary("   ", "1-10")

        sid = mgr.save_summary("Пользователь настроил окружение Python.", "1-12")
        assert sid > 0
        summaries = mgr.get_recent_summaries(limit=3)
        assert len(summaries) == 1
        assert summaries[0]["turn_range"] == "1-12"
        assert "Python" in summaries[0]["summary"]
        assert mgr.get_recent_summaries(limit=0) == []


class TestEpisodicMemoryAndPlannerContext:
    """Тестирование эпизодической памяти и компактного контекста планировщика."""

    def test_save_and_recall_episodes(self, mgr: MemoryManager) -> None:
        """Сохранение и поиск похожих эпизодов через FTS5 (включая мягкий OR-поиск)."""
        with pytest.raises(ValueError, match="Цель эпизода не может быть пустой"):
            mgr.save_episode("   ", [], "Ничего", False)

        ep1 = mgr.save_episode(
            goal="Отправить сообщение в Telegram коллеге",
            steps=[
                {"tool": "open_application", "app": "telegram"},
                {"tool": "type_text", "text": "Привет"},
            ],
            outcome="Сообщение успешно отправлено",
            success=True,
            app_context="Telegram Desktop",
        )
        ep2 = mgr.save_episode(
            goal="Собрать проект PyInstaller в exe",
            steps=[{"tool": "run_command", "cmd": "uv run pyinstaller"}],
            outcome="Сборка завершена без ошибок",
            success=True,
            app_context="PowerShell",
        )
        assert ep2 > ep1

        # Точный поиск
        tg_hits = mgr.recall_episodes("Telegram")
        assert len(tg_hits) == 1
        assert tg_hits[0]["id"] == ep1
        assert tg_hits[0]["success"] is True
        assert len(tg_hits[0]["steps"]) == 2

        # Мягкий OR-поиск при наличии лишних слов в новом запросе
        or_hits = mgr.recall_episodes("Переслать сообщение Telegram сейчас")
        assert len(or_hits) >= 1
        assert or_hits[0]["id"] == ep1

        # Пустой запрос возвращает последние эпизоды
        latest = mgr.recall_episodes("", limit=1)
        assert len(latest) == 1
        assert latest[0]["id"] == ep2

        assert mgr.recall_episodes("Telegram", limit=0) == []

    def test_build_planner_context_compact_and_complete(self, mgr: MemoryManager) -> None:
        """build_planner_context объединяет факты, активные заметки и релевантный опыт."""
        assert mgr.build_planner_context("любая цель") == ""

        mgr.set_fact("редактор", "VS Code", category="tools")
        done_id = mgr.add_note("Старая выполненная задача")
        mgr.complete_note(done_id)
        mgr.add_note("Проверить очень длинный текст " + ("детали " * 30))

        mgr.save_episode(
            goal="Открыть настройки VS Code",
            steps=[{"tool": "open_application", "app": "code"}],
            outcome="Окно настроек открыто",
            success=True,
            app_context="VS Code",
        )

        ctx = mgr.build_planner_context("настройки VS Code", episode_limit=2)
        assert "### Известные факты о пользователе:" in ctx
        assert "редактор (tools): VS Code" in ctx
        assert "### Активные заметки:" in ctx
        assert "Старая выполненная задача" not in ctx
        assert "..." in ctx
        assert "### Похожий прошлый опыт:" in ctx
        assert "[Успех] [VS Code]" in ctx


class TestThinkTagFilterAndLLMContext:
    """Тестирование фильтрации <think>-тегов, модели Qwen3.5 и многоходовых запросов."""

    @pytest.mark.parametrize(
        "chunks",
        [
            ["<think>секрет</think>Готово!"],
            ["<th", "ink>внутри ", "мысли</th", "ink>\n\nГотово!"],
            ["<THINK>верхний регистр</THINK>   Готово!"],
            ["<", "t", "h", "i", "n", "k", ">по", "бук", "вам<", "/t", "hink>", "Готово!"],
        ],
    )
    def test_think_tag_filter_split_chunks(self, chunks: list[str]) -> None:
        """ThinkTagFilter вырезает <think>...</think> даже при дроблении на произвольные чанки."""
        tag_filter = ThinkTagFilter()
        result_parts = [tag_filter.feed(chunk) for chunk in chunks]
        result_parts.append(tag_filter.flush())
        assert "".join(result_parts) == "Готово!"

    def test_think_tag_filter_preserves_normal_text_and_less_than(self) -> None:
        """Обычные токены без тега не буферизуются, а знак `<` в математике сохраняется."""
        tag_filter = ThinkTagFilter()
        assert tag_filter.feed("Первая ") == "Первая "
        assert tag_filter.feed("2 < 3 ") == "2 < 3 "
        assert tag_filter.feed("и 4 <") == "и 4 "
        assert tag_filter.feed(" 5") == "< 5"
        assert tag_filter.feed(" а еще <") == " а еще "
        assert tag_filter.flush() == "<"

    def test_think_tag_filter_discards_unclosed_think_block(self) -> None:
        """Незакрытый блок <think> не утекает в ответ при flush()."""
        assert strip_thinking_tags("Ответ<think>незакрытая мысль") == "Ответ"
        assert strip_thinking_tags("Привет <think>раз</think> и <think>два</think> мир") == (
            "Привет  и  мир"
        )

    def test_qwen35_model_config_and_thinking_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Клиент поддерживает LOCAL_LLM_MODEL=qwen3.5-4b и флаг enable_thinking."""
        monkeypatch.setenv("LOCAL_LLM_MODEL", QWEN35_MODEL_NAME)
        cli = LocalLLMClient(timeout_seconds=0.5)
        assert cli.model_name == "qwen3.5-4b"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "<think>скрытая логика</think>\nПривет, Денис!",
                        "reasoning_content": "дополнительные размышления",
                    }
                }
            ]
        }

        with patch.object(cli.session, "post", return_value=mock_resp) as mock_post:
            reply = cli.generate_response(
                "Привет",
                history=[
                    {"role": "user", "content": "Меня зовут Денис"},
                    {"role": "assistant", "content": "Запомнил, Денис!"},
                ],
            )
            assert reply == "Привет, Денис!"
            sent_payload = mock_post.call_args.kwargs["json"]
            assert sent_payload["model"] == "qwen3.5-4b"
            assert sent_payload["chat_template_kwargs"] == {"enable_thinking": False}
            assert [m["role"] for m in sent_payload["messages"]] == [
                "system",
                "user",
                "assistant",
                "user",
            ]

            # Если enable_thinking=True, принудительное отключение не отправляется
            cli.generate_response("Привет", enable_thinking=True)
            sent_thinking_payload = mock_post.call_args.kwargs["json"]
            assert "chat_template_kwargs" not in sent_thinking_payload

    def test_streaming_strips_split_think_tags_and_reasoning_content(self) -> None:
        """Потоковый SSE-парсер игнорирует reasoning_content и режет дробный <think>."""
        cli = LocalLLMClient(model_name=QWEN35_MODEL_NAME, timeout_seconds=0.5)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_lines.return_value = [
            'data: {"choices": [{"delta": {"reasoning_content": "внутренний монолог"}}]}',
            'data: {"choices": [{"delta": {"content": "<th"}}]}',
            'data: {"choices": [{"delta": {"content": "ink>проверка</th"}}]}',
            'data: {"choices": [{"delta": {"content": "ink>\\n\\nГотово, "}}]}',
            'data: {"choices": [{"delta": {"content": "командир!"}}]}',
            "data: [DONE]",
        ]

        history = [
            {"role": "user", "content": "Статус?"},
            {"role": "assistant", "content": "Все системы в норме."},
        ]
        with patch.object(cli.session, "post", return_value=mock_resp) as mock_post:
            chunks = list(cli.generate_response_stream("Продолжай", history=history))
            assert "".join(chunks) == "Готово, командир!"
            sent_payload = mock_post.call_args.kwargs["json"]
            assert len(sent_payload["messages"]) == 4
            assert sent_payload["chat_template_kwargs"] == {"enable_thinking": False}

    def test_plan_actions_with_history_and_image(self) -> None:
        """plan_actions корректно вставляет историю диалога перед текущим запросом."""
        cli = LocalLLMClient(model_name=QWEN35_MODEL_NAME, timeout_seconds=0.5)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "open_application",
                                    "arguments": '{"app_name": "notepad"}',
                                }
                            }
                        ]
                    }
                }
            ]
        }

        history: list[dict[str, Any]] = [
            {"role": "user", "content": "Какой у меня редактор?"},
            {"role": "assistant", "content": "Блокнот."},
            {"role": "system", "content": "не должен попасть в историю"},
        ]
        with patch.object(cli.session, "post", return_value=mock_resp) as mock_post:
            actions = cli.plan_actions("Открой его", image_b64="AAAA", history=history)
            assert actions == [{"tool": "open_application", "parameters": {"app_name": "notepad"}}]
            msgs = mock_post.call_args.kwargs["json"]["messages"]
            assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
            assert isinstance(msgs[-1]["content"], list)
