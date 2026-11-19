"""Сквозные интеграционные тесты для Вектора 2: Streaming LLM-to-TTS контура.

> [!NOTE]
> **Назначение файла:** Проверка сквозной цепочки потоковой генерации токенов (LocalLLMClient),
> буферизации предложений (SentenceAccumulator), потокового воспроизведения (PiperSynthesizer)
> и аварийного прерывания (FailSafeMonitor) в GupkaCommanderApp.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import requests
from PySide6.QtWidgets import QApplication

from core.brain.llm_client import LocalLLMClient
from core.failsafe import FailSafeMonitor
from core.voice.sentence_streamer import SentenceAccumulator
from core.voice.synthesizer import PiperSynthesizer
from core.voice.voice_pipeline import VoicePipeline
from main import GupkaCommanderApp


class MockAudioChunk:
    """Мок чанка синтезированного звука Piper."""

    def __init__(self, data: np.ndarray) -> None:
        self.audio_float_array = data


class MockOutputStream:
    """Мок аудиопотока sounddevice для верификации воспроизведения и прерывания."""

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
            raise RuntimeError("Stream aborted")
        self.written_blocks.append(np.copy(data))

    def abort(self) -> None:
        self.aborted = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def qapp() -> QApplication:
    """Синглтон QApplication для headless тестирования Qt компонентов."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def failsafe_monitor() -> FailSafeMonitor:
    """Изолированный монитор FailSafe."""
    return FailSafeMonitor(
        keyboard_factory=lambda *a, **kw: MagicMock(),
        mouse_factory=lambda *a, **kw: MagicMock(),
    )


class TestVector2EndToEndStreaming:
    """Сквозное тестирование потокового конвейера Vector 2."""

    def test_vector2_e2e_token_accumulator_to_synthesizer_pipeline(self) -> None:
        """Проверка сквозной передачи: LLM SSE -> SentenceAccumulator -> PiperSynthesizer."""
        client = LocalLLMClient(api_base="http://127.0.0.1:8080/v1")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_lines.return_value = [
            'data: {"choices": [{"delta": {"content": "Погода в "}}]}',
            'data: {"choices": [{"delta": {"content": "г. Москве "}}]}',
            'data: {"choices": [{"delta": {"content": "отличная, "}}]}',
            'data: {"choices": [{"delta": {"content": "температура 21.5 "}}]}',
            'data: {"choices": [{"delta": {"content": "градуса. "}}]}',
            'data: {"choices": [{"delta": {"content": "Ожидается "}}]}',
            'data: {"choices": [{"delta": {"content": "солнце!"}}]}',
            "data: [DONE]",
        ]

        mock_voice = MagicMock()
        mock_voice.sample_rate = 22050
        mock_voice.synthesize.return_value = [MockAudioChunk(np.ones(1024, dtype=np.float32) * 0.1)]

        created_streams: list[MockOutputStream] = []

        def stream_factory(*args: Any, **kwargs: Any) -> MockOutputStream:
            stream = MockOutputStream(*args, **kwargs)
            created_streams.append(stream)
            return stream

        synth = PiperSynthesizer(
            voice_factory=lambda *a, **kw: mock_voice,
            output_stream_factory=stream_factory,
        )

        with patch.object(client.session, "post", return_value=mock_resp):
            accumulator = SentenceAccumulator()
            sentences: list[str] = []

            for token in client.generate_response_stream("Погода"):
                for sentence in accumulator.feed(token):
                    sentences.append(sentence)
            tail = accumulator.flush()
            if tail:
                sentences.append(tail)

            assert sentences == [
                "Погода в г. Москве отличная, температура 21.5 градуса.",
                "Ожидается солнце!",
            ]

            synth.speak_stream(iter(sentences), blocking=True)

            assert mock_voice.synthesize.call_count == 2
            synthesized_calls = [c[0][0] for c in mock_voice.synthesize.call_args_list]
            assert synthesized_calls == sentences
            assert len(created_streams) == 2

    def test_vector2_app_ai_cognitive_live_streaming(
        self, qapp: QApplication, failsafe_monitor: FailSafeMonitor
    ) -> None:
        """Проверка работы потоковой генерации и вывода в GupkaCommanderApp."""
        mock_llm = MagicMock(spec=LocalLLMClient)
        mock_llm.is_healthy.return_value = True
        mock_llm.plan_actions.return_value = []
        tokens = ["Я — ", "локальный ", "ассистент. ", "Готов ", "к работе!"]
        mock_llm.generate_response_stream.return_value = iter(tokens)

        spoken_sentences: list[str] = []

        class MockSynthesizer:
            """Мок синтезатора с асинхронным чтением очереди предложений."""

            def __init__(self) -> None:
                self.is_speaking_val = False
                self.thread: threading.Thread | None = None

            def is_speaking(self) -> bool:
                return self.is_speaking_val

            def speak_stream(self, sentence_iter: Iterator[str], blocking: bool = False) -> None:
                def _worker() -> None:
                    self.is_speaking_val = True
                    try:
                        for s in sentence_iter:
                            spoken_sentences.append(s)
                    finally:
                        self.is_speaking_val = False

                self.thread = threading.Thread(target=_worker, daemon=True)
                self.thread.start()
                if blocking:
                    self.thread.join()

            def stop(self) -> None:
                self.is_speaking_val = False

        mock_synth = MockSynthesizer()
        mock_pipeline = MagicMock(spec=VoicePipeline)
        mock_pipeline.synthesizer = mock_synth

        app = GupkaCommanderApp(
            app=qapp,
            llm_client=mock_llm,
            voice_pipeline=mock_pipeline,
            failsafe_monitor=failsafe_monitor,
            enable_voice=True,
            enable_hotkey=False,
            enable_tray=False,
        )

        response = app.execute_command("Расскажи о себе")
        if mock_synth.thread is not None:
            mock_synth.thread.join(timeout=1.0)

        assert response == "Я — локальный ассистент. Готов к работе!"
        assert spoken_sentences == ["Я — локальный ассистент.", "Готов к работе!"]

        # Проверяем, что в карточке чата сохранился финальный текст
        drawer = app.spotlight_bar.chat_drawer
        assert drawer is not None
        assert len(drawer._cards) > 0
        last_card = drawer._cards[-1]
        assert "Я — локальный ассистент. Готов к работе!" in last_card.get_text()
        assert last_card.get_status() == "done"
        app.shutdown()

    def test_vector2_failsafe_interruption_during_streaming(self, qapp: QApplication) -> None:
        """Проверка аварийного прерывания потока через FailSafe: глушение звука и статус."""
        mock_failsafe = MagicMock(spec=FailSafeMonitor)
        # На первых двух токенах прерывания нет, на третьем срабатывает FailSafe
        interrupted_sequence = [False, False, True, True]
        mock_failsafe.is_interrupted.side_effect = interrupted_sequence

        mock_llm = MagicMock(spec=LocalLLMClient)
        mock_llm.is_healthy.return_value = True
        mock_llm.plan_actions.return_value = []
        mock_llm.generate_response_stream.return_value = iter(
            ["Токен1 ", "Токен2 ", "Токен3 ", "Токен4."]
        )

        mock_synth = MagicMock()
        mock_pipeline = MagicMock(spec=VoicePipeline)
        mock_pipeline.synthesizer = mock_synth

        app = GupkaCommanderApp(
            app=qapp,
            llm_client=mock_llm,
            voice_pipeline=mock_pipeline,
            failsafe_monitor=mock_failsafe,
            enable_voice=True,
            enable_hotkey=False,
            enable_tray=False,
        )

        result = app.execute_command("Расскажи длинную историю")

        # При InterruptedError execute_command ловит его и возвращает сообщение об отмене
        assert "прервана" in result.lower() or "failsafe" in result.lower()
        mock_synth.stop.assert_called()

        drawer = app.spotlight_bar.chat_drawer
        assert drawer is not None
        assert len(drawer._cards) >= 2
        # Карточка потокового ответа должна быть переведена в статус ошибки
        error_cards = [c for c in drawer._cards if c.get_status() == "error"]
        assert len(error_cards) >= 1
        app.shutdown()

    def test_vector2_fallback_streaming_when_llm_offline(
        self, qapp: QApplication, failsafe_monitor: FailSafeMonitor
    ) -> None:
        """Проверка потоковой работы через FallbackRuleEngine при падении сервера LLM."""
        real_client = LocalLLMClient(api_base="http://127.0.0.1:9999/v1", timeout_seconds=0.1)

        spoken: list[str] = []

        class MockSynth:
            def __init__(self) -> None:
                self.thread: threading.Thread | None = None

            def is_speaking(self) -> bool:
                return False

            def speak_stream(self, sentence_iter: Iterator[str], blocking: bool = False) -> None:
                def _worker() -> None:
                    for item in sentence_iter:
                        spoken.append(item)

                self.thread = threading.Thread(target=_worker, daemon=True)
                self.thread.start()
                if blocking:
                    self.thread.join()

            def stop(self) -> None:
                pass

        mock_synth_instance = MockSynth()
        mock_pipeline = MagicMock(spec=VoicePipeline)
        mock_pipeline.synthesizer = mock_synth_instance

        with patch.object(
            real_client.session,
            "post",
            side_effect=requests.RequestException("Server down"),
        ):
            app = GupkaCommanderApp(
                app=qapp,
                llm_client=real_client,
                voice_pipeline=mock_pipeline,
                failsafe_monitor=failsafe_monitor,
                enable_voice=True,
                enable_hotkey=False,
                enable_tray=False,
            )

            result = app.execute_command("привет")
            if mock_synth_instance.thread is not None:
                mock_synth_instance.thread.join(timeout=1.0)

            assert len(result) > 0
            assert "Привет" in result
            assert len(spoken) > 0
            app.shutdown()
