"""Тесты для модуля распознавания речи FasterWhisperTranscriber."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import numpy as np

from core.voice.transcriber import FasterWhisperTranscriber


class DummySegment:
    """Заглушка сегмента распознавания Faster-Whisper."""

    def __init__(self, text: str) -> None:
        self.text = text


def test_transcriber_initial_properties() -> None:
    """Проверка начальных свойств до инициализации."""
    transcriber = FasterWhisperTranscriber(
        model_size_or_path="small",
        device="cpu",
        compute_type="int8",
        cpu_threads=2,
        fallback_text="[fallback]",
    )

    assert not transcriber.is_initialized
    assert not transcriber.is_fallback
    assert transcriber.model_size_or_path == "small"
    assert transcriber.device == "cpu"
    assert transcriber.compute_type == "int8"


def test_transcriber_lazy_initialization_explicit() -> None:
    """Проверка явной инициализации через initialize()."""
    mock_model = MagicMock()
    mock_factory = MagicMock(return_value=mock_model)

    transcriber = FasterWhisperTranscriber(
        model_size_or_path="tiny",
        model_factory=mock_factory,
    )

    assert not transcriber.is_initialized
    result = transcriber.initialize()

    assert result is True
    assert transcriber.is_initialized
    assert not transcriber.is_fallback
    mock_factory.assert_called_once_with(
        "tiny",
        device="cpu",
        compute_type="int8",
        cpu_threads=4,
        local_files_only=False,
    )

    # Повторный вызов initialize не должен перезагружать модель
    assert transcriber.initialize() is True
    assert mock_factory.call_count == 1


def test_transcriber_lazy_initialization_on_first_transcribe() -> None:
    """Проверка ленивой инициализации при первом вызове transcribe()."""
    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([DummySegment("Привет мир")], MagicMock())
    mock_factory = MagicMock(return_value=mock_model)

    transcriber = FasterWhisperTranscriber(model_factory=mock_factory)

    assert not transcriber.is_initialized

    audio = np.zeros(16000, dtype=np.float32)
    text = transcriber.transcribe(audio)

    assert transcriber.is_initialized
    assert text == "Привет мир"
    mock_factory.assert_called_once()
    mock_model.transcribe.assert_called_once()


def test_transcriber_transcribe_multiple_segments() -> None:
    """Проверка корректной конкатенации и очистки нескольких сегментов."""
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (
        [
            DummySegment(" Открой "),
            DummySegment(" браузер "),
            DummySegment(" пожалуйста. "),
        ],
        MagicMock(),
    )

    transcriber = FasterWhisperTranscriber(model_factory=lambda *a, **kw: mock_model)
    transcriber.initialize()

    audio = np.ones(8000, dtype=np.float32) * 0.1
    text = transcriber.transcribe(audio, language="ru", beam_size=2)

    assert text == "Открой браузер пожалуйста."
    args, kwargs = mock_model.transcribe.call_args
    assert kwargs["language"] == "ru"
    assert kwargs["beam_size"] == 2
    assert kwargs["vad_filter"] is False


def test_transcriber_empty_audio() -> None:
    """Проверка обработки пустого или нулевого аудиосигнала."""
    mock_factory = MagicMock()
    transcriber = FasterWhisperTranscriber(model_factory=mock_factory)

    assert transcriber.transcribe(np.array([], dtype=np.float32)) == ""
    assert transcriber.transcribe(None) == ""  # type: ignore[arg-type]
    mock_factory.assert_not_called()


def test_transcriber_audio_preprocessing_types() -> None:
    """Проверка нормализации размерностей и форматов (int16, 2D, float64)."""
    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([DummySegment("тест")], MagicMock())

    transcriber = FasterWhisperTranscriber(model_factory=lambda *a, **kw: mock_model)
    transcriber.initialize()

    # 1. Проверка 2D int16 PCM данных
    int16_audio = np.array([[16384], [-16384], [0]], dtype=np.int16)
    transcriber.transcribe(int16_audio)

    passed_audio = mock_model.transcribe.call_args[0][0]
    assert passed_audio.ndim == 1
    assert passed_audio.dtype == np.float32
    assert np.isclose(passed_audio[0], 16384.0 / 32767.0, atol=1e-3)

    # 2. Проверка float64
    float64_audio = np.array([0.5, -0.5], dtype=np.float64)
    transcriber.transcribe(float64_audio)
    passed_audio_2 = mock_model.transcribe.call_args[0][0]
    assert passed_audio_2.dtype == np.float32


def test_transcriber_initialization_failure_fallback() -> None:
    """Проверка перехода в fallback при ошибке загрузки весов модели."""

    def failing_factory(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Ошибка загрузки весов HuggingFace")

    transcriber = FasterWhisperTranscriber(
        model_factory=failing_factory,
        fallback_text="[резервный ответ]",
    )

    success = transcriber.initialize()
    assert success is False
    assert transcriber.is_fallback
    assert not transcriber.is_initialized

    # При вызове transcribe возвращается fallback_text
    audio = np.zeros(16000, dtype=np.float32)
    assert transcriber.transcribe(audio) == "[резервный ответ]"


def test_transcriber_runtime_inference_error_fallback() -> None:
    """Проверка перехвата ошибки времени инференса Whisper."""
    mock_model = MagicMock()
    mock_model.transcribe.side_effect = RuntimeError("Сбой CTranslate2")

    transcriber = FasterWhisperTranscriber(
        model_factory=lambda *a, **kw: mock_model,
        fallback_text="[ошибка]",
    )
    transcriber.initialize()

    audio = np.zeros(16000, dtype=np.float32)
    result = transcriber.transcribe(audio)
    assert result == "[ошибка]"
