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

    def test_all_eight_tools_present(self) -> None:
        """Проверка наличия ровно 8 требуемых инструментов."""
        expected_names = {
            "open_application",
            "open_url",
            "search_web",
            "run_command",
            "window_control",
            "click_screen",
            "type_text",
            "inspect_screen",
        }
        actual_names = {t["function"]["name"] for t in SYSTEM_TOOLS}
        assert actual_names == expected_names
        assert len(SYSTEM_TOOLS) == 8

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
            assert called_kwargs["json"]["model"] == "qwen2.5-vl-3b-instruct"

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

    def test_engine_respond_phrases(self) -> None:
        """Проверка текстовых ответов резервного движка."""
        engine = FallbackRuleEngine()
        assert "telegram" in engine.respond("открой telegram")
        assert "youtube" in engine.respond("найди в youtube rock")
        assert "Привет" in engine.respond("добрый день")
        assert "недоступен" in engine.respond("какой смысл жизни?")
