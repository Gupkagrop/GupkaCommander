"""Модульные тесты для потокового взаимодействия LLM и синтезатора речи (Streaming TTS).

> [!NOTE]
> **Назначение файла:** Проверка потоковой генерации токенов через LocalLLMClient (SSE-стриминг,
> отказоустойчивость FallbackRuleEngine) и потокового синтеза PiperSynthesizer.speak_stream
> (последовательное воспроизведение предложений, механизм Barge-In stop).
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import requests

from core.brain.llm_client import LocalLLMClient
from core.voice.synthesizer import PiperSynthesizer


class MockAudioChunk:
    """Мок аудио-чанка Piper TTS с аудиоданными float32."""

    def __init__(self, data: np.ndarray) -> None:
        self.audio_float_array = data


class MockOutputStream:
    """Мок выходного аудиопотока sounddevice.OutputStream с поддержкой abort."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.written_blocks: list[np.ndarray] = []
        self.aborted = False
        self.closed = False
        self.started = False

    def __enter__(self) -> MockOutputStream:
        self.started = True
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.closed = True

    def write(self, data: np.ndarray) -> None:
        if self.aborted:
            raise RuntimeError("Stream was aborted")
        self.written_blocks.append(np.copy(data))

    def abort(self) -> None:
        self.aborted = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def llm_client() -> LocalLLMClient:
    """Фикстура для инициализации клиента LocalLLMClient."""
    return LocalLLMClient(
        api_base="http://127.0.0.1:8080/v1",
        model_name="Qwen2.5-VL-3B-Instruct",
        timeout_seconds=2.0,
    )


class TestLocalLLMClientStreamingTTS:
    """Тестирование потоковой генерации токенов в LocalLLMClient."""

    def test_llm_stream_sse_parsing_success(self, llm_client: LocalLLMClient) -> None:
        """Проверка корректного парсинга потоковых чанков SSE и маркера [DONE]."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_lines.return_value = [
            ": keepalive",
            'data: {"choices": [{"delta": {"content": "Первая "}}]}',
            "",
            'data: {"choices": [{"delta": {"content": "фраза. "}}]}',
            'data: {"choices": [{"delta": {"content": "Вторая часть!"}}]}',
            "data: [DONE]",
        ]

        with patch.object(llm_client.session, "post", return_value=mock_resp) as mock_post:
            tokens = list(llm_client.generate_response_stream("Тестовый запрос"))

            assert tokens == ["Первая ", "фраза. ", "Вторая часть!"]
            mock_post.assert_called_once()
            _, kwargs = mock_post.call_args
            assert kwargs.get("stream") is True
            assert kwargs["json"]["stream"] is True

    def test_llm_stream_custom_system_prompt(self, llm_client: LocalLLMClient) -> None:
        """Проверка передачи пользовательского системного промпта в теле запроса."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_lines.return_value = [
            'data: {"choices": [{"delta": {"content": "Ответ"}}]}',
            "data: [DONE]",
        ]

        custom_prompt = "Ты специализированный голосовой ассистент."
        with patch.object(llm_client.session, "post", return_value=mock_resp) as mock_post:
            list(llm_client.generate_response_stream("Привет", system_prompt=custom_prompt))
            _, kwargs = mock_post.call_args
            messages = kwargs["json"]["messages"]
            assert messages[0]["content"] == custom_prompt

    def test_llm_stream_empty_or_whitespace_input(self, llm_client: LocalLLMClient) -> None:
        """Проверка передачи пустого ввода: генератор завершается без сетевых запросов."""
        with patch.object(llm_client.session, "post") as mock_post:
            tokens_empty = list(llm_client.generate_response_stream(""))
            tokens_spaces = list(llm_client.generate_response_stream("    \n\t  "))

            assert tokens_empty == []
            assert tokens_spaces == []
            mock_post.assert_not_called()

    def test_llm_stream_fallback_on_request_exception(self, llm_client: LocalLLMClient) -> None:
        """Проверка перехода на FallbackRuleEngine при ошибке сети/таймаута."""
        with patch.object(
            llm_client.session,
            "post",
            side_effect=requests.RequestException("Connection timeout"),
        ):
            tokens = list(llm_client.generate_response_stream("какая погода?"))

            assert len(tokens) > 0
            full_text = "".join(tokens)
            assert any(word in full_text.lower() for word in ["погода", "интернет", "проверить"])

    def test_llm_stream_fallback_on_http_error(self, llm_client: LocalLLMClient) -> None:
        """Проверка перехода на FallbackRuleEngine при коде ответа 503 Service Unavailable."""
        mock_resp = MagicMock()
        mock_resp.status_code = 503

        with patch.object(llm_client.session, "post", return_value=mock_resp):
            tokens = list(llm_client.generate_response_stream("открой блокнот"))

            assert len(tokens) > 0
            full_text = "".join(tokens)
            assert any(term in full_text.lower() for term in ["блокнот", "notepad", "выполняю"])

    def test_llm_stream_fallback_when_stream_is_empty(self, llm_client: LocalLLMClient) -> None:
        """Проверка перехода на fallback, если сервер вернул 200, но поток без токенов."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_lines.return_value = [": empty stream", "data: [DONE]"]

        with patch.object(llm_client.session, "post", return_value=mock_resp):
            tokens = list(llm_client.generate_response_stream("привет"))

            assert len(tokens) > 0
            full_text = "".join(tokens)
            assert "Привет" in full_text

    def test_llm_stream_malformed_json_resilient(self, llm_client: LocalLLMClient) -> None:
        """Проверка устойчивости к битым JSON-строкам в потоке SSE."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_lines.return_value = [
            'data: {"choices": [{"delta": {"content": "Токен 1 "}}]}',
            "data: {broken json invalid",
            'data: {"choices": [{"delta": {"content": "Токен 2."}}]}',
            "data: [DONE]",
        ]

        with patch.object(llm_client.session, "post", return_value=mock_resp):
            tokens = list(llm_client.generate_response_stream("запрос"))
            assert tokens == ["Токен 1 ", "Токен 2."]


class TestPiperSynthesizerStreamingTTS:
    """Тестирование потокового воспроизведения в PiperSynthesizer.speak_stream."""

    def test_speak_stream_sequential_playback(self) -> None:
        """Проверка последовательного синтеза и воспроизведения нескольких предложений."""
        mock_voice = MagicMock()
        mock_voice.sample_rate = 22050

        audio_block = np.ones(1000, dtype=np.float32) * 0.1
        mock_chunk = MockAudioChunk(audio_block)
        mock_voice.synthesize.return_value = [mock_chunk]

        created_streams: list[MockOutputStream] = []

        def stream_factory(*args: Any, **kwargs: Any) -> MockOutputStream:
            stream = MockOutputStream(*args, **kwargs)
            created_streams.append(stream)
            return stream

        synth = PiperSynthesizer(
            voice_factory=lambda *a, **kw: mock_voice,
            output_stream_factory=stream_factory,
        )

        sentences = ["Раз предложение.", "Два предложение.", "Три предложение."]
        synth.speak_stream(iter(sentences), blocking=True)

        assert mock_voice.synthesize.call_count == 3
        synthesized_texts = [call[0][0] for call in mock_voice.synthesize.call_args_list]
        assert synthesized_texts == sentences
        assert len(created_streams) == 3
        assert all(stream.closed for stream in created_streams)

    def test_speak_stream_empty_iterator(self) -> None:
        """Проверка передачи пустого итератора: завершается без ошибок и вызовов синтеза."""
        mock_voice = MagicMock()
        mock_voice.sample_rate = 22050
        synth = PiperSynthesizer(voice_factory=lambda *a, **kw: mock_voice)

        synth.speak_stream(iter([]), blocking=True)
        mock_voice.synthesize.assert_not_called()

    def test_speak_stream_barge_in_stop_clears_queue(self) -> None:
        """Проверка механизма Barge-In: вызов stop() мгновенно очищает очередь и глушит поток."""
        mock_voice = MagicMock()
        mock_voice.sample_rate = 22050
        synthesized_texts: list[str] = []

        def slow_synth(text: str) -> Any:
            synthesized_texts.append(text)
            for _ in range(5):
                time.sleep(0.03)
                yield MockAudioChunk(np.ones(1024, dtype=np.float32))

        mock_voice.synthesize = slow_synth

        created_streams: list[MockOutputStream] = []

        def stream_factory(*args: Any, **kwargs: Any) -> MockOutputStream:
            stream = MockOutputStream(*args, **kwargs)
            created_streams.append(stream)
            return stream

        synth = PiperSynthesizer(
            voice_factory=lambda *a, **kw: mock_voice,
            output_stream_factory=stream_factory,
        )

        sentences = [f"Длинное предложение номер {i}." for i in range(10)]
        synth.speak_stream(iter(sentences), blocking=False)

        # Даем запуститься первому предложению
        time.sleep(0.04)
        assert synth.is_speaking() is True

        # Срабатывает Barge-In прерывание
        synth.stop()

        # Очередь должна быть очищена
        time.sleep(0.05)
        assert synth.is_speaking() is False
        # Синтез не должен был обработать все 10 предложений
        assert len(synthesized_texts) < 10
        assert len(created_streams) >= 1
        assert created_streams[0].aborted is True

    def test_speak_stream_auto_initialization(self) -> None:
        """Проверка автоматической инициализации при вызове speak_stream."""
        mock_voice = MagicMock()
        mock_voice.sample_rate = 22050
        mock_voice.synthesize.return_value = [MockAudioChunk(np.zeros(100, dtype=np.float32))]

        synth = PiperSynthesizer(voice_factory=lambda *a, **kw: mock_voice)
        assert synth.is_initialized is False

        synth.speak_stream(iter(["Привет!"]), blocking=True)
        assert synth.is_initialized is True
        assert synth.backend == PiperSynthesizer.BACKEND_PIPER

    def test_speak_stream_preempts_ongoing_speech(self) -> None:
        """Проверка прерывания предыдущей активной речи перед запуском нового потока."""
        mock_voice = MagicMock()
        mock_voice.sample_rate = 22050
        mock_voice.synthesize.return_value = [MockAudioChunk(np.zeros(100, dtype=np.float32))]

        synth = PiperSynthesizer(voice_factory=lambda *a, **kw: mock_voice)
        synth.initialize()

        with (
            patch.object(synth, "is_speaking", return_value=True),
            patch.object(synth, "stop") as mock_stop,
        ):
            synth.speak_stream(iter(["Новое предложение."]), blocking=True)
            mock_stop.assert_called_once()
