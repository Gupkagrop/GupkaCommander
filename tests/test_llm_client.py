"""Модульные тесты для LocalLLMClient и FallbackRuleEngine (core.brain.llm_client).

Проверяют:
1. Определение всех 8 системных инструментов (Function Calling schema).
2. Работу клиента по OpenAI-совместимому API (онлайн и с мультимодальным входом).
3. Поведение встроенного резервного движка (FallbackRuleEngine) при недоступности сервера.
4. Парсинг вызовов инструментов и обработку ошибок сети.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from core.brain.llm_client import SYSTEM_TOOLS, FallbackRuleEngine, LocalLLMClient


@pytest.fixture
def client() -> LocalLLMClient:
    """Фикстура создания клиента локального LLM с малым таймаутом."""
    return LocalLLMClient(timeout_seconds=0.1)


class TestSystemToolsDefinitions:
    """Тестирование спецификации системных инструментов."""

    def test_all_system_tools_present(self) -> None:
        """Проверка наличия всех 16 системных инструментов, включая UI Automation и память."""
        expected_names = {
            "open_application",
            "open_url",
            "search_web",
            "run_command",
            "window_control",
            "click_screen",
            "type_text",
            "inspect_screen",
            "click_ui_element",
            "type_into_ui_element",
            "inspect_ui_elements",
            "store_user_fact",
            "retrieve_user_facts",
            "add_user_note",
            "list_user_notes",
            "delete_user_note",
        }
        actual_names = {t["function"]["name"] for t in SYSTEM_TOOLS}
        assert actual_names == expected_names
        assert len(SYSTEM_TOOLS) == 16

    # Алиас для обратной совместимости
    test_all_eight_tools_present = test_all_system_tools_present

    def test_tools_schema_validity(self) -> None:
        """Проверка структуры каждого инструмента по формату OpenAI Tools."""
        for tool in SYSTEM_TOOLS:
            assert tool["type"] == "function"
            fn = tool["function"]
            assert "name" in fn and isinstance(fn["name"], str)
            assert "description" in fn and isinstance(fn["description"], str)
            params = fn["parameters"]
            assert params["type"] == "object"
            assert "properties" in params


class TestLocalLLMClientConfig:
    """Проверка конфигурации и инициализации клиента."""

    def test_default_api_base(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Проверка дефолтного адреса API (http://127.0.0.1:8080/v1)."""
        monkeypatch.delenv("LOCAL_LLM_API_BASE", raising=False)
        cli = LocalLLMClient()
        assert cli.api_base == "http://127.0.0.1:8080/v1"

    def test_custom_api_base_and_strip_slash(self) -> None:
        """Проверка пользовательского URL с обрезкой завершающего слэша."""
        cli = LocalLLMClient(api_base="http://localhost:5000/v1/")
        assert cli.api_base == "http://localhost:5000/v1"

    def test_env_var_api_base(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Проверка считывания адреса из переменной окружения."""
        monkeypatch.setenv("LOCAL_LLM_API_BASE", "http://192.168.1.100:8000/v1")
        cli = LocalLLMClient()
        assert cli.api_base == "http://192.168.1.100:8000/v1"

    def test_health_check_online(self, client: LocalLLMClient) -> None:
        """Проверка health-check при доступном сервере."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch.object(client.session, "get", return_value=mock_resp):
            assert client.is_healthy() is True

    def test_health_check_offline(self, client: LocalLLMClient) -> None:
        """Проверка health-check при недоступном сервере."""
        with patch.object(
            client.session,
            "get",
            side_effect=requests.ConnectionError("Connection refused"),
        ):
            assert client.is_healthy() is False


class TestGenerateResponse:
    """Тестирование генерации текстовых ответов."""

    def test_generate_response_online(self, client: LocalLLMClient) -> None:
        """Проверка успешной генерации ответа онлайн-моделью."""
        fake_data = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Привет! Чем я могу помочь?",
                    }
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fake_data

        with patch.object(client.session, "post", return_value=mock_resp) as mock_post:
            res = client.generate_response("Привет")
            assert res == "Привет! Чем я могу помочь?"
            mock_post.assert_called_once()
            called_kwargs = mock_post.call_args[1]
            assert called_kwargs["json"]["model"] == "qwen3-vl-4b-instruct"

    def test_generate_response_offline_fallback(self, client: LocalLLMClient) -> None:
        """Проверка перехода на резервный движок при ошибке соединения."""
        with patch.object(
            client.session,
            "post",
            side_effect=requests.exceptions.ConnectionError("Offline"),
        ):
            res = client.generate_response("привет")
            assert "Привет" in res or "GupkaCommander" in res

    def test_generate_response_command_offline(self, client: LocalLLMClient) -> None:
        """Проверка ответа на команду при недоступном сервере."""
        with patch.object(
            client.session,
            "post",
            side_effect=requests.exceptions.Timeout("Timeout"),
        ):
            res = client.generate_response("открой telegram")
            assert "telegram" in res.lower()


class TestPlanActions:
    """Тестирование планирования действий (Tool Calling)."""

    def test_plan_actions_online_tool_calls(self, client: LocalLLMClient) -> None:
        """Проверка парсинга tool_calls при ответе сервера."""
        fake_data = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "open_application",
                                    "arguments": json.dumps({"app_name": "notepad"}),
                                },
                            }
                        ],
                    }
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fake_data

        with patch.object(client.session, "post", return_value=mock_resp):
            actions = client.plan_actions("открой блокнот")
            assert len(actions) == 1
            assert actions[0]["tool"] == "open_application"
            assert actions[0]["parameters"]["app_name"] == "notepad"

    def test_plan_actions_with_image_b64(self, client: LocalLLMClient) -> None:
        """Проверка мультимодального запроса с изображением."""
        fake_data = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_2",
                                "type": "function",
                                "function": {
                                    "name": "click_screen",
                                    "arguments": {"x": 100, "y": 200, "button": "left"},
                                },
                            }
                        ],
                    }
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fake_data

        with patch.object(client.session, "post", return_value=mock_resp) as mock_post:
            actions = client.plan_actions(
                "кликни по синей кнопке", image_b64="iVBORw0KGgoAAAANSUhEUg=="
            )
            assert len(actions) == 1
            assert actions[0]["tool"] == "click_screen"
            assert actions[0]["parameters"]["x"] == 100
            called_kwargs = mock_post.call_args[1]
            user_msg = called_kwargs["json"]["messages"][1]["content"]
            assert isinstance(user_msg, list)
            assert user_msg[1]["type"] == "image_url"

    @pytest.mark.parametrize(
        ("prompt", "expected_tool", "param_key", "param_val"),
        [
            ("открой telegram", "open_application", "app_name", "telegram"),
            ("найди в youtube rock", "search_web", "query", "rock"),
            ("сделай громче", "window_control", "action", "volume_up"),
            ("сверни окно", "window_control", "action", "minimize"),
            ("запусти powershell ls", "run_command", "command", "ls"),
            ("открой ютуб", "open_url", "url", "https://www.youtube.com"),
            ("что на экране", "inspect_screen", None, None),
        ],
    )
    def test_plan_actions_offline_fallback(
        self,
        client: LocalLLMClient,
        prompt: str,
        expected_tool: str,
        param_key: str | None,
        param_val: str | None,
    ) -> None:
        """Проверка детерминированного резервного планирования при отключенном LLM."""
        with patch.object(
            client.session,
            "post",
            side_effect=requests.exceptions.ConnectionError("Offline"),
        ):
            actions = client.plan_actions(prompt)
            assert len(actions) == 1
            act = actions[0]
            assert act["tool"] == expected_tool
            if param_key is not None:
                assert act["parameters"].get(param_key) == param_val

    def test_plan_actions_cognitive_offline_returns_empty(
        self,
        client: LocalLLMClient,
    ) -> None:
        """Свободный запрос при отключенном LLM не создает ложных действий."""
        with patch.object(
            client.session,
            "post",
            side_effect=requests.exceptions.ConnectionError("Offline"),
        ):
            actions = client.plan_actions("расскажи шутку про программистов")
            assert actions == []


class TestFallbackRuleEngineDirect:
    """Тестирование резервного движка правил напрямую."""

    def test_engine_plan_all_types(self) -> None:
        """Проверка формирования планов для всех типов команд."""
        engine = FallbackRuleEngine()

        plan_app = engine.plan("открой telegram")
        assert plan_app[0]["tool"] == "open_application"

        plan_search = engine.plan("найди в youtube rock")
        assert plan_search[0]["tool"] == "search_web"
        assert plan_search[0]["parameters"]["engine"] == "youtube"

        plan_vol_down = engine.plan("сделай тише")
        assert plan_vol_down[0]["parameters"]["action"] == "volume_down"

        plan_vol_mute = engine.plan("выключи звук")
        assert plan_vol_mute[0]["parameters"]["action"] == "mute"

        plan_vision_click = engine.plan("кликни на иконку")
        assert plan_vision_click[0]["tool"] == "click_screen"

        plan_vision_type = engine.plan("введи текст привет")
        assert plan_vision_type[0]["tool"] == "type_text"

        plan_uia_click = engine.plan("нажми кнопку Сохранить в окне Блокнот")
        assert plan_uia_click[0]["tool"] == "click_ui_element"
        assert plan_uia_click[0]["parameters"]["element_name"] == "Сохранить"
        assert plan_uia_click[0]["parameters"]["window_title"] == "Блокнот"

        plan_uia_type = engine.plan("введи в поле Поиск текст Привет мир")
        assert plan_uia_type[0]["tool"] == "type_into_ui_element"
        assert plan_uia_type[0]["parameters"]["element_name"] == "Поиск"
        assert plan_uia_type[0]["parameters"]["text"] == "Привет мир"

        plan_uia_toggle = engine.plan("поставь галочку Запомнить меня")
        assert plan_uia_toggle[0]["tool"] == "click_ui_element"
        assert plan_uia_toggle[0]["parameters"]["control_type"] == "CheckBox"

    def test_engine_respond_phrases(self) -> None:
        """Проверка текстовых ответов резервного движка."""
        engine = FallbackRuleEngine()
        assert "telegram" in engine.respond("открой telegram")
        assert "youtube" in engine.respond("найди в youtube rock")
        assert "Привет" in engine.respond("добрый день")
        assert "недоступен" in engine.respond("какой смысл жизни?")


class TestLocalLLMClientStreaming:
    """Тестирование потоковой генерации через Server-Sent Events (SSE)."""

    def test_generate_response_stream_success_sse(self, client: LocalLLMClient) -> None:
        """Проверка успешного парсинга потоковых чанков SSE и маркера [DONE]."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_lines.return_value = [
            'data: {"choices": [{"delta": {"content": "Привет, "}}]}',
            "",
            'data: {"choices": [{"delta": {"content": "Денис!"}}]}',
            "data: [DONE]",
        ]

        with patch.object(client.session, "post", return_value=mock_resp) as mock_post:
            chunks = list(client.generate_response_stream("Привет"))
            assert chunks == ["Привет, ", "Денис!"]
            mock_post.assert_called_once()
            _, kwargs = mock_post.call_args
            assert kwargs.get("stream") is True
            assert kwargs["json"]["stream"] is True

    def test_generate_response_stream_fallback_on_network_error(
        self, client: LocalLLMClient
    ) -> None:
        """Проверка перехода на FallbackRuleEngine при сетевой ошибке запроса."""
        with patch.object(
            client.session,
            "post",
            side_effect=requests.RequestException("Connection refused"),
        ):
            chunks = list(client.generate_response_stream("привет"))
            assert len(chunks) > 0
            full_text = "".join(chunks)
            assert "Привет" in full_text

    def test_generate_response_stream_fallback_on_status_500(self, client: LocalLLMClient) -> None:
        """Проверка перехода на FallbackRuleEngine при HTTP 500 от сервера."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch.object(client.session, "post", return_value=mock_resp):
            chunks = list(client.generate_response_stream("открой telegram"))
            assert len(chunks) > 0
            full_text = "".join(chunks)
            assert "telegram" in full_text

    def test_generate_response_stream_empty_input(self, client: LocalLLMClient) -> None:
        """Проверка передачи пустого запроса (возврат пустого итератора)."""
        chunks = list(client.generate_response_stream("   "))
        assert chunks == []
