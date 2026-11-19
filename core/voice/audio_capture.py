"""
> [!NOTE]
> Модуль core.voice.audio_capture: Потоковый захват звука с микрофона в реальном времени.
> Реализует кольцевой буфер и нормализацию PCM 16 kHz в float32 [-1.0, 1.0].
"""

import queue
import threading
from typing import Any

import numpy as np
import sounddevice as sd  # type: ignore[import-untyped]


class AudioCapture:
    """
    Класс потокового захвата аудиосигнала с микрофона.
    Захватывает 16 kHz 16-bit mono PCM и конвертирует в float32 [-1.0, 1.0].
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        chunk_size: int = 512,
        channels: int = 1,
        device: int | str | None = None,
        max_queue_size: int = 200,
    ) -> None:
        """
        Инициализирует параметры захвата аудиопотока.

        Args:
            sample_rate: Частота дискретизации в Гц (по умолчанию 16000).
            chunk_size: Размер блока в сэмплах (по умолчанию 512 = 32 мс при 16 кГц).
            channels: Количество аудиоканалов (по умолчанию 1 — моно).
            device: Идентификатор или имя аудиоустройства.
            max_queue_size: Максимальный размер очереди блоков.
        """
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.channels = channels
        self.device = device
        self.max_queue_size = max_queue_size

        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=max_queue_size)
        self._stream: sd.InputStream | None = None
        self._lock: threading.Lock = threading.Lock()
        self._is_running: bool = False

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: Any,
        status: sd.CallbackFlags,
    ) -> None:
        """
        Колбэк звукового драйвера sounddevice для обработки входящих сэмплов.
        """
        if not self._is_running:
            return

        chunk = indata[:, 0] if indata.ndim > 1 else indata
        normalized = self._normalize_chunk(chunk)
        self._enqueue_chunk(normalized)

    def _normalize_chunk(self, chunk: np.ndarray) -> np.ndarray:
        """
        Приводит входной блок к одномерному массиву float32 в диапазоне [-1.0, 1.0].
        """
        if np.issubdtype(chunk.dtype, np.integer):
            max_val = float(np.iinfo(chunk.dtype).max)
            return (chunk.astype(np.float32) / max_val).flatten()

        if chunk.dtype != np.float32:
            return chunk.astype(np.float32).flatten()

        return chunk.copy().flatten()

    def _enqueue_chunk(self, chunk: np.ndarray) -> None:
        """
        Помещает аудиокадр в очередь с вытеснением старейших данных при переполнении.
        """
        try:
            self._queue.put_nowait(chunk)
            return
        except queue.Full:
            pass

        try:
            self._queue.get_nowait()
        except queue.Empty:
            pass

        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            pass

    def start(self) -> None:
        """
        Запускает захват аудиопотока с микрофона.
        """
        with self._lock:
            if self._is_running:
                return

            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                blocksize=self.chunk_size,
                dtype="float32",
                callback=self._audio_callback,
                device=self.device,
            )
            self._stream.start()
            self._is_running = True

    def stop(self) -> None:
        """
        Останавливает аудиопоток и освобождает системные ресурсы.
        """
        with self._lock:
            if not self._is_running:
                return

            self._is_running = False
            if self._stream is None:
                return

            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None

    def read_chunk(self, timeout: float | None = None) -> np.ndarray | None:
        """
        Извлекает нормализованный аудиокадр из очереди.

        Args:
            timeout: Таймаут ожидания в секундах. Если None — неблокирующий опрос.

        Returns:
            Одномерный массив сэмплов float32 или None при пустой очереди/таймауте.
        """
        if timeout is None:
            try:
                return self._queue.get_nowait()
            except queue.Empty:
                return None

        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def clear(self) -> None:
        """
        Очищает очередь накопленных аудиокадров.
        """
        with self._lock:
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break

    def is_active(self) -> bool:
        """
        Проверяет активность захвата аудиопотока.
        """
        with self._lock:
            if not self._is_running or self._stream is None:
                return False
            return bool(self._stream.active)

    @property
    def queue_size(self) -> int:
        """
        Возвращает текущее количество кадров в очереди.
        """
        return self._queue.qsize()

    def __enter__(self) -> "AudioCapture":
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        self.stop()
