"""Тесты для модуля синтеза речи PiperSynthesizer и механизма Barge-In."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import numpy as np

from core.voice.synthesizer import PiperSynthesizer


class MockAudioChunk:
    """Мок чанка Piper TTS с аудиомассивом float32."""

    def __init__(self, data: np.ndarray) -> None:
        self.audio_float_array = data


class MockOutputStream:
    """Мок потока sounddevice.OutputStream с контекстным менеджером и отслеживанием abort."""

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


def test_synthesizer_initial_state() -> None:
    """Проверка начального состояния до инициализации."""
    synth = PiperSynthesizer(sample_rate=16000)
    assert not synth.is_initialized
    assert synth.backend is None
    assert not synth.is_speaking()


def test_synthesizer_init_with_piper_voice_factory() -> None:
    """Проверка успешной инициализации с бэкендом Piper."""
    mock_voice = MagicMock()
    mock_voice.sample_rate = 22050
    mock_factory = MagicMock(return_value=mock_voice)

    synth = PiperSynthesizer(voice_factory=mock_factory)
    success = synth.initialize()

    assert success is True
    assert synth.is_initialized
    assert synth.backend == PiperSynthesizer.BACKEND_PIPER
    mock_factory.assert_called_once()


def test_synthesizer_piper_streaming_playback() -> None:
    """Проверка потокового воспроизведения через OutputStream с бэкендом Piper."""
    audio_sample = np.ones(3000, dtype=np.float32) * 0.5
    mock_chunk = MockAudioChunk(audio_sample)

    mock_voice = MagicMock()
    mock_voice.sample_rate = 22050
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

    # Блокирующее воспроизведение
    synth.speak("Тестовое сообщение", blocking=True)

    assert len(created_streams) == 1
    stream = created_streams[0]
    assert stream.closed
    assert len(stream.written_blocks) > 0

    # Проверяем, что блоки были записаны порциями (по 1024 сэмпла)
    total_samples = sum(len(b) for b in stream.written_blocks)
    assert total_samples == 3000
    assert not synth.is_speaking()


def test_synthesizer_barge_in_stops_piper_stream() -> None:
    """Проверка мгновенного прерывания (Barge-In) при воспроизведении Piper."""
    # Создаем большой объем данных для синтеза
    mock_chunks = [MockAudioChunk(np.ones(10000, dtype=np.float32) * 0.1) for _ in range(5)]

    def slow_synthesize(text: str) -> Any:
        for ch in mock_chunks:
            time.sleep(0.05)
            yield ch

    mock_voice = MagicMock()
    mock_voice.sample_rate = 22050
    mock_voice.synthesize = slow_synthesize

    created_streams: list[MockOutputStream] = []

    def stream_factory(*args: Any, **kwargs: Any) -> MockOutputStream:
        stream = MockOutputStream(*args, **kwargs)
        created_streams.append(stream)
        return stream

    synth = PiperSynthesizer(
        voice_factory=lambda *a, **kw: mock_voice,
        output_stream_factory=stream_factory,
    )

    synth.speak("Длинный синтезируемый ответ", blocking=False)
    time.sleep(0.02)
    assert synth.is_speaking()

    # Вызываем Barge-In прерывание
    synth.stop()

    assert not synth.is_speaking()
    assert len(created_streams) == 1
    assert created_streams[0].aborted


def test_synthesizer_sapi_fallback_and_stop() -> None:
    """Проверка переключения на Windows SAPI5 и Barge-In через SAPI."""
    mock_sapi = MagicMock()
    # Эмулируем статус говорящего
    mock_status = MagicMock()
    mock_status.RunningState = 2  # SRSEIsSpeaking
    mock_sapi.Status = mock_status

    synth = PiperSynthesizer(
        use_sapi_fallback=True,
        sapi_factory=lambda: mock_sapi,
    )
    synth.initialize()

    assert synth.backend == PiperSynthesizer.BACKEND_SAPI

    # Запускаем воспроизведение
    synth.speak("Голосовое уведомление через SAPI", blocking=False)
    time.sleep(0.02)

    mock_sapi.Speak.assert_called_with("Голосовое уведомление через SAPI", 1)

    # Вызываем Barge-In
    synth.stop()

    # Проверяем вызов очистки очереди SAPI (флаг 2 = SVSFPurgeBeforeSpeak)
    mock_sapi.Speak.assert_called_with("", 2)
    assert not synth.is_speaking()


def test_synthesizer_mock_fallback_when_sapi_disabled() -> None:
    """Проверка перехода в mock режим, если SAPI отключен или недоступен."""
    synth = PiperSynthesizer(
        use_sapi_fallback=False,
    )
    synth.initialize()

    assert synth.backend == PiperSynthesizer.BACKEND_MOCK
    assert not synth.is_speaking()

    # В mock-режиме speak выполняется корректно
    synth.speak("Привет в mock-режиме", blocking=True)
    assert not synth.is_speaking()


def test_synthesizer_empty_text_ignored() -> None:
    """Проверка игнорирования пустых строк или пробелов."""
    mock_voice = MagicMock()
    mock_factory = MagicMock(return_value=mock_voice)

    synth = PiperSynthesizer(voice_factory=mock_factory)
    synth.speak("")
    synth.speak("   ")

    mock_factory.assert_not_called()
    assert not synth.is_speaking()


def test_synthesizer_interrupts_previous_phrase_on_new_speak() -> None:
    """Проверка автоматического прерывания предыдущей фразы при новом вызове speak()."""
    mock_voice = MagicMock()
    mock_voice.sample_rate = 22050

    def slow_synth(text: str) -> Any:
        time.sleep(0.1)
        yield MockAudioChunk(np.ones(1000, dtype=np.float32))

    mock_voice.synthesize = slow_synth

    synth = PiperSynthesizer(
        voice_factory=lambda *a, **kw: mock_voice,
        output_stream_factory=lambda *a, **kw: MockOutputStream(*a, **kw),
    )

    synth.speak("Первая фраза", blocking=False)
    time.sleep(0.02)
    assert synth.is_speaking()

    # Запуск второй фразы должен прервать первую
    synth.speak("Вторая фраза", blocking=False)
    time.sleep(0.02)
    assert synth.is_speaking()

    synth.stop()
    assert not synth.is_speaking()


def test_synthesizer_speak_stream_sequential() -> None:
    """Проверка последовательного потокового воспроизведения предложений из итератора."""
    mock_voice = MagicMock()
    mock_voice.sample_rate = 22050
    synthesized_texts: list[str] = []

    def mock_synth(text: str) -> Any:
        synthesized_texts.append(text)
        yield MockAudioChunk(np.ones(1024, dtype=np.float32))

    mock_voice.synthesize = mock_synth

    created_streams: list[MockOutputStream] = []

    def stream_factory(*args: Any, **kwargs: Any) -> MockOutputStream:
        stream = MockOutputStream(*args, **kwargs)
        created_streams.append(stream)
        return stream

    synth = PiperSynthesizer(
        voice_factory=lambda *a, **kw: mock_voice,
        output_stream_factory=stream_factory,
    )

    sentences = ["Первое предложение.", "Второе предложение.", "Третье предложение."]
    synth.speak_stream(iter(sentences), blocking=True)

    assert synthesized_texts == sentences
    assert len(created_streams) == 3
    for s in created_streams:
        assert s.closed
        assert len(s.written_blocks) == 1
    assert not synth.is_speaking()


def test_synthesizer_speak_stream_barge_in_clears_queue_and_aborts() -> None:
    """Проверка мгновенной очистки очереди и прерывания sounddevice при Barge-In в speak_stream."""
    mock_voice = MagicMock()
    mock_voice.sample_rate = 22050
    synthesized_texts: list[str] = []

    def slow_synth(text: str) -> Any:
        synthesized_texts.append(text)
        for _ in range(5):
            time.sleep(0.04)
            yield MockAudioChunk(np.ones(2048, dtype=np.float32))

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

    sentences = [
        "Очень длинное первое предложение.",
        "Второе предложение, которое не должно воспроизводиться.",
        "Третье предложение в очереди.",
    ]

    synth.speak_stream(iter(sentences), blocking=False)
    time.sleep(0.03)
    assert synth.is_speaking()

    # Barge-In: прерываем воспроизведение
    synth.stop()

    assert not synth.is_speaking()
    # Первое предложение начало воспроизводиться и было прервано
    assert len(created_streams) == 1
    assert created_streams[0].aborted

    # Второе и третье предложения не должны были пойти в синтез
    assert "Второе предложение, которое не должно воспроизводиться." not in synthesized_texts
    assert "Третье предложение в очереди." not in synthesized_texts


def test_synthesizer_speak_stream_empty_iterator() -> None:
    """Проверка корректной обработки пустого итератора предложений."""
    mock_voice = MagicMock()
    synth = PiperSynthesizer(voice_factory=lambda *a, **kw: mock_voice)

    synth.speak_stream(iter([]), blocking=True)
    assert not synth.is_speaking()
    mock_voice.synthesize.assert_not_called()
