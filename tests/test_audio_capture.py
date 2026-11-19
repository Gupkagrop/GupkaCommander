"""
> [!NOTE]
> Тесты модуля core.voice.audio_capture: Проверка захвата аудио,
> потокобезопасного кольцевого буфера, нормализации PCM и управления жизненным циклом.
"""

from unittest.mock import MagicMock, patch

import numpy as np

from core.voice.audio_capture import AudioCapture


class TestAudioCapture:
    """Набор тестов для компонента AudioCapture."""

    def test_initialization_defaults(self) -> None:
        """Проверка параметров инициализации по умолчанию."""
        cap = AudioCapture()
        assert cap.sample_rate == 16000
        assert cap.chunk_size == 512
        assert cap.channels == 1
        assert cap.device is None
        assert cap.max_queue_size == 200
        assert cap.queue_size == 0
        assert not cap.is_active()

    def test_initialization_custom(self) -> None:
        """Проверка пользовательских параметров инициализации."""
        cap = AudioCapture(
            sample_rate=8000,
            chunk_size=256,
            channels=2,
            device=3,
            max_queue_size=50,
        )
        assert cap.sample_rate == 8000
        assert cap.chunk_size == 256
        assert cap.channels == 2
        assert cap.device == 3
        assert cap.max_queue_size == 50

    @patch("sounddevice.InputStream")
    def test_start_and_stop_lifecycle(self, mock_stream_cls: MagicMock) -> None:
        """Проверка запуска и корректной остановки аудиопотока."""
        mock_stream = MagicMock()
        mock_stream.active = True
        mock_stream_cls.return_value = mock_stream

        cap = AudioCapture(sample_rate=16000, chunk_size=512)
        assert not cap.is_active()

        cap.start()
        assert cap.is_active()
        mock_stream_cls.assert_called_once()
        mock_stream.start.assert_called_once()

        # Повторный вызов start() идемпотентен
        cap.start()
        assert mock_stream_cls.call_count == 1

        cap.stop()
        assert not cap.is_active()
        mock_stream.stop.assert_called_once()
        mock_stream.close.assert_called_once()

        # Повторный stop() безопасен
        cap.stop()

    @patch("sounddevice.InputStream")
    def test_context_manager(self, mock_stream_cls: MagicMock) -> None:
        """Проверка работы менеджера контекста (with)."""
        mock_stream = MagicMock()
        mock_stream.active = True
        mock_stream_cls.return_value = mock_stream

        with AudioCapture() as cap:
            assert cap.is_active()

        assert not cap.is_active()
        mock_stream.stop.assert_called_once()

    def test_normalize_float32_1d(self) -> None:
        """Проверка нормализации одномерного float32 массива."""
        cap = AudioCapture()
        raw = np.array([0.1, -0.5, 0.9], dtype=np.float32)
        norm = cap._normalize_chunk(raw)
        assert norm.dtype == np.float32
        assert norm.shape == (3,)
        np.testing.assert_allclose(norm, raw)

    def test_normalize_2d_input(self) -> None:
        """Проверка нормализации многомерного массива (срез по первому каналу)."""
        cap = AudioCapture()
        raw = np.array([[0.1, 0.9], [-0.5, -0.8]], dtype=np.float32)
        norm = cap._normalize_chunk(raw[:, 0])
        assert norm.ndim == 1
        assert len(norm) == 2
        assert norm[0] == np.float32(0.1)

    def test_normalize_int16(self) -> None:
        """Проверка нормализации int16 сэмплов в float32 диапазон [-1.0, 1.0]."""
        cap = AudioCapture()
        raw = np.array([32767, -32768, 0], dtype=np.int16)
        norm = cap._normalize_chunk(raw)
        assert norm.dtype == np.float32
        np.testing.assert_allclose(norm[0], 1.0, atol=1e-4)
        np.testing.assert_allclose(norm[1], -1.0, atol=1e-4)
        assert norm[2] == 0.0

    def test_audio_callback_enqueue_and_read(self) -> None:
        """Проверка доставки кадров через аудиоколбэк и последующего чтения."""
        cap = AudioCapture(max_queue_size=10)
        cap._is_running = True

        data = np.ones((512, 1), dtype=np.float32) * 0.25
        cap._audio_callback(data, 512, None, MagicMock())

        assert cap.queue_size == 1
        chunk = cap.read_chunk(timeout=0.1)
        assert chunk is not None
        assert len(chunk) == 512
        assert chunk.dtype == np.float32
        np.testing.assert_allclose(chunk[0], 0.25)
        assert cap.queue_size == 0

    def test_read_chunk_timeout_and_empty(self) -> None:
        """Проверка неблокирующего чтения и таймаута при пустой очереди."""
        cap = AudioCapture()
        assert cap.read_chunk(timeout=None) is None
        assert cap.read_chunk(timeout=0.01) is None

    def test_ring_buffer_overflow_drop_oldest(self) -> None:
        """Проверка вытеснения старых кадров при переполнении очереди."""
        cap = AudioCapture(max_queue_size=2)
        cap._is_running = True

        chunk1 = np.ones(512, dtype=np.float32) * 1.0
        chunk2 = np.ones(512, dtype=np.float32) * 2.0
        chunk3 = np.ones(512, dtype=np.float32) * 3.0

        cap._enqueue_chunk(chunk1)
        cap._enqueue_chunk(chunk2)
        assert cap.queue_size == 2

        # Третий кадр должен вытеснить chunk1
        cap._enqueue_chunk(chunk3)
        assert cap.queue_size == 2

        first_out = cap.read_chunk()
        assert first_out is not None
        np.testing.assert_allclose(first_out[0], 2.0)

        second_out = cap.read_chunk()
        assert second_out is not None
        np.testing.assert_allclose(second_out[0], 3.0)

    def test_clear_queue(self) -> None:
        """Проверка полной очистки очереди кадров."""
        cap = AudioCapture()
        cap._is_running = True
        cap._enqueue_chunk(np.zeros(512, dtype=np.float32))
        cap._enqueue_chunk(np.zeros(512, dtype=np.float32))
        assert cap.queue_size == 2

        cap.clear()
        assert cap.queue_size == 0
        assert cap.read_chunk() is None

    def test_audio_callback_ignored_when_stopped(self) -> None:
        """Проверка игнорирования колбэков при остановленном объекте."""
        cap = AudioCapture()
        cap._is_running = False

        data = np.ones((512, 1), dtype=np.float32)
        cap._audio_callback(data, 512, None, MagicMock())
        assert cap.queue_size == 0

    def test_mute_unmute_lifecycle(self) -> None:
        """Проверка отключения и включения записи звука (Mute/Unmute)."""
        cap = AudioCapture()
        cap._is_running = True
        assert not cap.is_muted

        # При активном микрофоне чанки попадают в очередь
        data = np.ones((512, 1), dtype=np.float32)
        cap._audio_callback(data, 512, None, MagicMock())
        assert cap.queue_size == 1

        # Mute должен очистить очередь и игнорировать новые чанки
        cap.mute()
        assert cap.is_muted
        assert cap.queue_size == 0

        cap._audio_callback(data, 512, None, MagicMock())
        assert cap.queue_size == 0

        # Unmute возобновляет запись в очередь
        cap.unmute()
        assert not cap.is_muted
        cap._audio_callback(data, 512, None, MagicMock())
        assert cap.queue_size == 1
