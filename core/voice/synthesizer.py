"""Модуль синтеза речи (TTS) на базе Piper TTS ONNX и Windows SAPI5.

> [!NOTE]
> **Назначение файла:** Реализация PiperSynthesizer для синтеза речи на CPU с потоковым выводом
> через sounddevice.OutputStream, поддержкой мгновенного прерывания (Barge-In) и
> многоуровневым резервированием (Piper ONNX -> Windows SAPI5 -> Mock).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd  # type: ignore[import-untyped]
import win32com.client  # type: ignore[import-untyped]

try:
    import pythoncom  # type: ignore[import-untyped]
except ImportError:
    pythoncom = None

try:
    import piper
except ImportError:
    piper = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


@contextmanager
def _com_initialized() -> Iterator[None]:
    """Контекстный менеджер инициализации COM-библиотеки в фоновом потоке."""
    initialized = False
    if pythoncom is not None:
        try:
            pythoncom.CoInitialize()
            initialized = True
        except Exception as exc:
            logger.debug("Ошибка pythoncom.CoInitialize(): %s", exc)
    try:
        yield
    finally:
        if initialized and pythoncom is not None:
            try:
                pythoncom.CoUninitialize()
            except Exception as exc:
                logger.debug("Ошибка pythoncom.CoUninitialize(): %s", exc)


class PiperSynthesizer:
    """Синтезатор речи с потоковым воспроизведением PCM и поддержкой прерывания (Barge-In)."""

    BACKEND_PIPER = "piper"
    BACKEND_SAPI = "sapi"
    BACKEND_MOCK = "mock"

    DEFAULT_MODEL_PATH = Path("models/piper/ru_RU-dmitri-medium.onnx")
    DEFAULT_CONFIG_PATH = Path("models/piper/ru_RU-dmitri-medium.onnx.json")

    def __init__(
        self,
        model_path: str | Path | None = None,
        config_path: str | Path | None = None,
        sample_rate: int = 22050,
        voice_factory: Callable[..., Any] | None = None,
        output_stream_factory: Callable[..., Any] | None = None,
        sapi_factory: Callable[..., Any] | None = None,
        use_sapi_fallback: bool = True,
    ) -> None:
        """Инициализация синтезатора речи.

        Args:
            model_path: Путь к файлу ONNX модели Piper TTS.
            config_path: Путь к конфигурационному json-файлу модели Piper.
            sample_rate: Частота дискретизации аудио (по умолчанию 22050 Гц).
            voice_factory: Фабрика загрузки PiperVoice (для внедрения моков в тестах).
            output_stream_factory: Фабрика потока sounddevice.OutputStream.
            sapi_factory: Фабрика COM-объекта SAPI.SpVoice для Windows.
            use_sapi_fallback: Разрешить ли резервный переход на SAPI5 при отсутствии Piper.
        """
        self._model_path: Path | None = (
            Path(model_path) if model_path is not None else self._resolve_default_model_path()
        )
        self._config_path: Path | None = (
            Path(config_path)
            if config_path is not None
            else self._resolve_default_config_path(self._model_path)
        )
        self._sample_rate: int = sample_rate

        self._voice_factory: Callable[..., Any] | None = voice_factory
        self._output_stream_factory: Callable[..., Any] = (
            output_stream_factory
            if output_stream_factory is not None
            else (sd.OutputStream if sd is not None else None)  # type: ignore[assignment]
        )
        self._sapi_factory: Callable[..., Any] | None = sapi_factory
        self._use_sapi_fallback: bool = use_sapi_fallback

        self._backend: str | None = None
        self._voice: Any | None = None
        self._sapi_voice: Any | None = None
        self._is_initialized: bool = False

        self._is_speaking: bool = False
        self._stop_event: threading.Event = threading.Event()
        self._stream_lock: threading.Lock = threading.Lock()
        self._current_stream: Any | None = None
        self._playback_thread: threading.Thread | None = None
        self._producer_thread: threading.Thread | None = None
        self._sentence_queue: queue.Queue[str | None] | None = None
        self._init_lock: threading.Lock = threading.Lock()

    @classmethod
    def _resolve_default_model_path(cls) -> Path | None:
        """Проверяет путь models/piper/ru_RU-dmitri-medium.onnx по умолчанию."""
        candidates = [
            cls.DEFAULT_MODEL_PATH,
            Path.cwd() / cls.DEFAULT_MODEL_PATH,
            Path(__file__).resolve().parent.parent.parent / cls.DEFAULT_MODEL_PATH,
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        return cls.DEFAULT_MODEL_PATH

    @classmethod
    def _resolve_default_config_path(cls, model_path: Path | None) -> Path | None:
        """Проверяет путь к конфигурационному файлу Piper TTS по умолчанию."""
        if model_path is not None:
            candidate_json = Path(f"{model_path}.json")
            if candidate_json.is_file():
                return candidate_json.resolve()
        candidates = [
            cls.DEFAULT_CONFIG_PATH,
            Path.cwd() / cls.DEFAULT_CONFIG_PATH,
            Path(__file__).resolve().parent.parent.parent / cls.DEFAULT_CONFIG_PATH,
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        return cls.DEFAULT_CONFIG_PATH

    @property
    def model_path(self) -> Path | None:
        """Путь к файлу ONNX модели Piper TTS."""
        return self._model_path

    @property
    def config_path(self) -> Path | None:
        """Путь к файлу конфигурации Piper TTS."""
        return self._config_path

    @property
    def is_initialized(self) -> bool:
        """Флаг инициализации бэкенда синтеза."""
        with self._init_lock:
            return self._is_initialized

    @property
    def backend(self) -> str | None:
        """Активный бэкенд синтеза ('piper', 'sapi' или 'mock')."""
        return self._backend

    def initialize(self) -> bool:
        """Ленивая или явная инициализация бэкенда синтеза речи.

        Returns:
            bool: True при успешной инициализации любого из доступных бэкендов.
        """
        with self._init_lock:
            if self._is_initialized:
                return True

            # 1. Попытка инициализации Piper ONNX
            if self._try_init_piper():
                self._is_initialized = True
                return True

            # 2. Попытка инициализации Windows SAPI5
            if self._use_sapi_fallback and self._try_init_sapi():
                self._is_initialized = True
                return True

            # 3. Резервный режим (Mock)
            self._backend = self.BACKEND_MOCK
            self._is_initialized = True
            logger.info("PiperSynthesizer инициализирован в режиме заглушки (mock).")
            return True

    def _try_init_piper(self) -> bool:
        """Попытка загрузки ONNX модели Piper."""
        has_factory = self._voice_factory is not None
        has_model_file = self._model_path is not None and self._model_path.is_file()

        if not has_factory and not has_model_file:
            return False

        try:
            factory = self._voice_factory or (piper.PiperVoice.load if piper is not None else None)
            if factory is None:
                return False

            model_arg = str(self._model_path) if self._model_path else "dummy.onnx"
            config_arg = str(self._config_path) if self._config_path else None

            self._voice = factory(model_arg, config_path=config_arg, use_cuda=False)
            self._backend = self.BACKEND_PIPER
            logger.info("Piper TTS успешно загружен с бэкендом ONNX CPU.")
            return True
        except Exception as exc:
            logger.warning("Не удалось загрузить модель Piper TTS: %s", exc)
            self._voice = None
            return False

    def _try_init_sapi(self) -> bool:
        """Попытка инициализации Windows SAPI5."""
        try:
            if self._sapi_factory is not None:
                self._sapi_voice = self._sapi_factory()
            else:
                self._sapi_voice = win32com.client.Dispatch("SAPI.SpVoice")

            if self._sapi_voice is not None:
                self._backend = self.BACKEND_SAPI
                logger.info("PiperSynthesizer переключен на Windows SAPI5.")
                return True
        except Exception as exc:
            logger.warning("Windows SAPI5 недоступен: %s", exc)
            self._sapi_voice = None

        return False

    def is_speaking(self) -> bool:
        """Проверяет, активно ли воспроизведение речи в данный момент."""
        if not self._is_speaking:
            return False

        if self._backend == self.BACKEND_SAPI and self._sapi_voice is not None:
            try:
                status = getattr(self._sapi_voice, "Status", None)
                if status is not None and getattr(status, "RunningState", 1) == 1:
                    self._is_speaking = False
                    return False
            except Exception:
                pass

        return self._is_speaking

    def stop(self) -> None:
        """Мгновенная остановка воспроизведения и генерации речи (Barge-In)."""
        self._stop_event.set()

        # 0. Аварийный сброс очереди предложений потокового синтеза
        if self._sentence_queue is not None:
            try:
                while not self._sentence_queue.empty():
                    self._sentence_queue.get_nowait()
            except Exception:
                pass
            try:
                self._sentence_queue.put_nowait(None)
            except Exception:
                pass

        # 1. Аварийный сброс активного аудиопотока sounddevice
        with self._stream_lock:
            if self._current_stream is not None:
                try:
                    self._current_stream.abort()
                except Exception as exc:
                    logger.debug("Ошибка при прерывании sounddevice stream: %s", exc)
                self._current_stream = None

        # 2. Аварийный сброс буфера SAPI
        if self._backend == self.BACKEND_SAPI and self._sapi_voice is not None:
            try:
                # Флаг 2: SVSFPurgeBeforeSpeak (очистка очереди и мгновенная тишина)
                self._sapi_voice.Speak("", 2)
            except Exception as exc:
                logger.debug("Ошибка при сбросе очереди SAPI: %s", exc)

        self._is_speaking = False

        # Ожидаем завершения потока воспроизведения без взаимной блокировки
        t = self._playback_thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=0.3)
        self._playback_thread = None

        # Ожидаем завершения потока продюсера очереди
        p = self._producer_thread
        if p is not None and p.is_alive() and p is not threading.current_thread():
            p.join(timeout=0.3)
        self._producer_thread = None

    def speak(self, text: str, blocking: bool = False) -> None:
        """Произносит заданный текст.

        Args:
            text: Текст для озвучивания.
            blocking: Ожидать ли окончания воспроизведения перед возвратом управления.
        """
        clean_text = text.strip() if text else ""
        if not clean_text:
            return

        # Если уже что-то воспроизводится — прерываем предыдущую фразу
        if self.is_speaking():
            self.stop()

        if not self.is_initialized:
            self.initialize()

        self._stop_event.clear()

        if blocking:
            self._run_playback(clean_text)
            return

        self._playback_thread = threading.Thread(
            target=self._run_playback,
            args=(clean_text,),
            name="PiperSynthesizerPlayback",
            daemon=True,
        )
        self._playback_thread.start()

    def speak_stream(
        self,
        sentence_iterator: Iterator[str],
        blocking: bool = False,
    ) -> None:
        """Последовательный потоковый синтез и воспроизведение предложений из итератора.

        Args:
            sentence_iterator: Итератор законченных предложений.
            blocking: Ожидать ли полного воспроизведения всех предложений перед возвратом.
        """
        # Если уже что-то воспроизводится — прерываем предыдущую речь
        if self.is_speaking():
            self.stop()

        if not self.is_initialized:
            self.initialize()

        self._stop_event.clear()
        sentence_queue: queue.Queue[str | None] = queue.Queue()
        self._sentence_queue = sentence_queue
        producer_done = threading.Event()

        def _producer() -> None:
            try:
                for sentence in sentence_iterator:
                    if self._stop_event.is_set():
                        break
                    clean_sentence = sentence.strip() if sentence else ""
                    if clean_sentence:
                        sentence_queue.put(clean_sentence)
            except Exception as exc:
                logger.error("Ошибка при чтении из потока предложений: %s", exc)
            finally:
                producer_done.set()
                sentence_queue.put(None)

        def _consumer() -> None:
            self._is_speaking = True
            try:
                while not self._stop_event.is_set():
                    try:
                        item = sentence_queue.get(timeout=0.05)
                    except queue.Empty:
                        if producer_done.is_set() and sentence_queue.empty():
                            break
                        continue

                    if item is None or self._stop_event.is_set():
                        break

                    clean_text = item.strip()
                    if clean_text and not self._stop_event.is_set():
                        try:
                            self._play_sentence(clean_text)
                        except Exception as exc:
                            logger.error(
                                "Ошибка при воспроизведении предложения (%s): %s",
                                self._backend,
                                exc,
                            )
            finally:
                self._is_speaking = False
                with self._stream_lock:
                    self._current_stream = None

        self._producer_thread = threading.Thread(
            target=_producer,
            name="PiperSynthesizerProducer",
            daemon=True,
        )
        self._playback_thread = threading.Thread(
            target=_consumer,
            name="PiperSynthesizerConsumer",
            daemon=True,
        )

        self._producer_thread.start()
        self._playback_thread.start()

        if blocking:
            self._playback_thread.join()
            if self._producer_thread.is_alive():
                self._producer_thread.join(timeout=0.5)

    def _play_sentence(self, text: str) -> None:
        """Синтез и вывод одного предложения в активный бэкенд."""
        if self._backend == self.BACKEND_PIPER:
            self._play_piper(text)
        elif self._backend == self.BACKEND_SAPI:
            self._play_sapi(text)
        else:
            self._play_mock(text)

    def _run_playback(self, text: str) -> None:
        """Потоковая процедура озвучивания одиночного фрагмента."""
        self._is_speaking = True
        try:
            self._play_sentence(text)
        except Exception as exc:
            logger.error("Ошибка при воспроизведении речи (%s): %s", self._backend, exc)
        finally:
            self._is_speaking = False
            with self._stream_lock:
                self._current_stream = None

    def _play_piper(self, text: str) -> None:
        """Потоковый синтез и вывод через sounddevice.OutputStream."""
        assert self._voice is not None
        if self._output_stream_factory is None:
            logger.warning("Аудиоустройство недоступно. Переход на mock.")
            self._play_mock(text)
            return

        sample_rate = getattr(self._voice, "sample_rate", self._sample_rate)
        chunk_iter = self._voice.synthesize(text)

        with self._output_stream_factory(
            samplerate=sample_rate, channels=1, dtype="float32"
        ) as stream:
            with self._stream_lock:
                self._current_stream = stream

            for chunk in chunk_iter:
                if self._stop_event.is_set():
                    break

                audio_data = self._extract_float32_audio(chunk)
                if audio_data is None or audio_data.size == 0:
                    continue

                if self._stream_audio_blocks(stream, audio_data):
                    break

    def _extract_float32_audio(self, chunk: Any) -> np.ndarray | None:
        """Извлекает и нормализует аудиоданные из чанка Piper."""
        if hasattr(chunk, "audio_float_array") and chunk.audio_float_array is not None:
            return np.asarray(chunk.audio_float_array, dtype=np.float32)

        if isinstance(chunk, np.ndarray):
            if np.issubdtype(chunk.dtype, np.integer):
                max_val = float(np.iinfo(chunk.dtype).max)
                return (chunk.astype(np.float32) / max_val).astype(np.float32)
            return chunk.astype(np.float32)

        return None

    def _stream_audio_blocks(self, stream: Any, audio_data: np.ndarray) -> bool:
        """Потоковая запись фрагментов в stream с регулярной проверкой прерывания."""
        block_size = 1024
        total_len = len(audio_data)

        for i in range(0, total_len, block_size):
            if self._stop_event.is_set():
                return True
            block = audio_data[i : i + block_size]
            try:
                stream.write(block)
            except Exception as exc:
                logger.debug("Исключение при stream.write: %s", exc)
                return True

        return False

    def _play_sapi(self, text: str) -> None:
        """Воспроизведение через Windows SAPI5."""
        assert self._sapi_voice is not None
        with _com_initialized():
            # Флаг 1: SVSFlagsAsync (асинхронное воспроизведение)
            self._sapi_voice.Speak(text, 1)

            # Ожидаем завершения фразы или сигнала прерывания (Barge-In)
            while not self._stop_event.is_set():
                status = getattr(self._sapi_voice, "Status", None)
                if status is not None and getattr(status, "RunningState", 1) == 1:
                    break
                time.sleep(0.02)

            if self._stop_event.is_set():
                try:
                    self._sapi_voice.Speak("", 2)
                except Exception:
                    pass

    def _play_mock(self, text: str) -> None:
        """Эмуляция синтеза речи для тестов и сред без аудиоустройств."""
        duration = min(0.02 * len(text), 0.3)
        step = 0.02
        elapsed = 0.0

        while elapsed < duration and not self._stop_event.is_set():
            time.sleep(step)
            elapsed += step
