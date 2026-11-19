"""
> [!NOTE]
> Тесты модуля core.voice.vad_detector: Проверка детектора голосовой активности (Silero VAD ONNX),
> энергетического fallback-режима, отслеживания состояний речи и сброса контекста.
"""

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

from core.voice.vad_detector import VADDetector


class TestVADDetector:
    """Набор тестов для детектора активности речи VADDetector."""

    def test_initialization_defaults(self) -> None:
        """Проверка значений параметров по умолчанию."""
        vad = VADDetector(model_path="non_existent.onnx")
        assert vad.sample_rate == 16000
        assert vad.chunk_size == 512
        assert vad.threshold == 0.5
        assert vad.silence_duration_ms == 500
        assert vad.energy_threshold == 0.015
        assert not vad.is_speech_active
        assert not vad.is_speech_start
        assert not vad.is_speech_end
        assert not vad.using_onnx

    def test_fallback_energy_silence(self) -> None:
        """Проверка детекции тишины в fallback-режиме."""
        vad = VADDetector(model_path="non_existent.onnx")
        silent_chunk = np.zeros(512, dtype=np.float32)

        is_speech = vad.process_chunk(silent_chunk)
        assert not is_speech
        assert vad.last_probability == 0.0
        assert not vad.is_speech_active
        assert not vad.is_speech_start
        assert not vad.is_speech_end

    def test_fallback_energy_speech(self) -> None:
        """Проверка детекции громкого сигнала (речи) в fallback-режиме."""
        vad = VADDetector(model_path="non_existent.onnx", energy_threshold=0.01)
        loud_chunk = np.ones(512, dtype=np.float32) * 0.1

        is_speech = vad.process_chunk(loud_chunk)
        assert is_speech
        assert vad.last_probability > 0.5
        assert vad.is_speech_active
        assert vad.is_speech_start
        assert not vad.is_speech_end

    def test_speech_state_transitions(self) -> None:
        """Проверка цепочки переходов: тишина -> начало речи -> пауза -> конец фразы."""
        vad = VADDetector(
            model_path="non_existent.onnx",
            silence_duration_ms=100,  # ~3 чанка по 32 мс
            energy_threshold=0.02,
        )

        loud_chunk = np.ones(512, dtype=np.float32) * 0.1
        silent_chunk = np.zeros(512, dtype=np.float32)

        # 1. Первый кадр речи -> speech_start=True, is_speech_active=True
        is_speech = vad.process_chunk(loud_chunk)
        assert is_speech
        assert vad.is_speech_active
        assert vad.is_speech_start
        assert not vad.is_speech_end

        # 2. Второй кадр речи -> speech_start=False, is_speech_active=True
        is_speech = vad.process_chunk(loud_chunk)
        assert is_speech
        assert vad.is_speech_active
        assert not vad.is_speech_start
        assert not vad.is_speech_end

        # 3. Первый кадр тишины (32 мс < 100 мс) -> речь продолжается
        vad.process_chunk(silent_chunk)
        assert vad.is_speech_active
        assert not vad.is_speech_start
        assert not vad.is_speech_end

        # 4. Второй кадр тишины (64 мс < 100 мс) -> речь еще продолжается
        vad.process_chunk(silent_chunk)
        assert vad.is_speech_active
        assert not vad.is_speech_end

        # 5. Третий кадр тишины (96 мс < 100 мс) -> речь еще продолжается
        vad.process_chunk(silent_chunk)
        assert vad.is_speech_active
        assert not vad.is_speech_end

        # 6. Четвертый кадр тишины (128 мс >= 100 мс) -> фиксация окончания фразы!
        vad.process_chunk(silent_chunk)
        assert not vad.is_speech_active
        assert vad.is_speech_end

        # 7. Последующий кадр тишины -> speech_end сбрасывается в False
        vad.process_chunk(silent_chunk)
        assert not vad.is_speech_active
        assert not vad.is_speech_end

    def test_reset_states(self) -> None:
        """Проверка полного сброса состояний детектора."""
        vad = VADDetector(model_path="non_existent.onnx", energy_threshold=0.01)
        loud_chunk = np.ones(512, dtype=np.float32) * 0.1

        vad.process_chunk(loud_chunk)
        assert vad.is_speech_active

        vad.reset_states()
        assert not vad.is_speech_active
        assert not vad.is_speech_start
        assert not vad.is_speech_end
        assert vad.last_probability == 0.0

    def test_input_normalization_int16(self) -> None:
        """Проверка автоматической конвертации целочисленных аудиосэмплов."""
        vad = VADDetector(model_path="non_existent.onnx", energy_threshold=0.01)
        int16_chunk = np.ones(512, dtype=np.int16) * 10000

        is_speech = vad.process_chunk(int16_chunk)
        assert is_speech
        assert vad.is_speech_active

    def test_onnx_inference_with_mock_session(self) -> None:
        """Проверка логики инференса через мок ONNX Runtime."""
        mock_session = MagicMock()
        mock_input_input = MagicMock()
        mock_input_input.name = "input"
        mock_input_state = MagicMock()
        mock_input_state.name = "state"
        mock_session.get_inputs.return_value = [mock_input_input, mock_input_state]

        # Возвращаем вероятность речи 0.85 и новый state
        mock_session.run.return_value = [
            np.array([[0.85]], dtype=np.float32),
            np.zeros((2, 1, 128), dtype=np.float32),
        ]

        vad = VADDetector(model_path="non_existent.onnx", threshold=0.5)
        vad._session = mock_session
        vad._input_names = ["input", "state"]
        vad._using_onnx = True

        chunk = np.zeros(512, dtype=np.float32)
        is_speech = vad.process_chunk(chunk)

        assert is_speech
        assert vad.is_speech_active
        assert vad.last_probability == np.float32(0.85)
        mock_session.run.assert_called_once()

    def test_onnx_inference_with_hc_mock_session(self) -> None:
        """Проверка работы с моделями Silero формата v4/v6 (тензоры h и c)."""
        mock_session = MagicMock()
        mock_input_input = MagicMock()
        mock_input_input.name = "input"
        mock_input_h = MagicMock()
        mock_input_h.name = "h"
        mock_input_c = MagicMock()
        mock_input_c.name = "c"
        mock_session.get_inputs.return_value = [
            mock_input_input,
            mock_input_h,
            mock_input_c,
        ]

        # Возвращаем вероятность речи 0.2 (тишина)
        mock_session.run.return_value = [
            np.array([0.2], dtype=np.float32),
            np.zeros((1, 1, 128), dtype=np.float32),
            np.zeros((1, 1, 128), dtype=np.float32),
        ]

        vad = VADDetector(model_path="non_existent.onnx", threshold=0.5)
        vad._session = mock_session
        vad._input_names = ["input", "h", "c"]
        vad._using_onnx = True

        chunk = np.zeros(512, dtype=np.float32)
        is_speech = vad.process_chunk(chunk)

        assert not is_speech
        assert not vad.is_speech_active
        assert vad.last_probability == np.float32(0.2)
        mock_session.run.assert_called_once()

    def test_real_silero_onnx_model_if_present(self) -> None:
        """Проверка реальной модели silero_vad.onnx в каталоге models (при наличии)."""
        model_file = Path("models/silero_vad.onnx")
        if not model_file.is_file():
            return

        vad = VADDetector(model_path=model_file)
        assert vad.using_onnx

        # Тишина должна давать нулевую/крайне низкую вероятность
        silent = np.zeros(512, dtype=np.float32)
        is_speech = vad.process_chunk(silent)
        assert not is_speech
        assert vad.last_probability < 0.1
