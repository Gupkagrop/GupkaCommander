"""Модуль синтеза речи (TTS) на базе локального сервера llama-server /tts (Qwen3-TTS).

> [!NOTE]
> **Назначение файла:** Реализация QwenTTSSynthesizer для высококачествоенного синтеза русской речи
> на GPU через HTTP API `POST /tts` локального `llama-server.exe` (:8081) с потоковым
> воспроизведением WAV через sounddevice, изоляцией сессий Barge-In и авто-откатом на Piper TTS.
"""

from __future__ import annotations

import base64
import io
import logging
import queue
import threading
import time
import wave
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import requests
import sounddevice as sd  # type: ignore[import-untyped]

from core.voice.voice_pipeline import SynthesizerProtocol

logger = logging.getLogger(__name__)


def _decode_wav_to_float32(wav_bytes: bytes) -> tuple[np.ndarray, int, int]:
    """Декодирует WAV-байты в массив float32 в диапазоне [-1.0, 1.0].

    Args:
        wav_bytes: Сырые байты WAV-файла.

    Returns:
        Кортеж (audio_float32, sample_rate, channels).
    """
    if not wav_bytes or len(wav_bytes) < 44:
        raise ValueError("Пустой или поврежденный WAV-буфер.")

    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        raw_frames = wf.readframes(wf.getnframes())

    if not raw_frames or sample_rate <= 0 or channels <= 0:
        raise ValueError("WAV не содержит аудиоданных.")

    if sample_width == 1:
        arr_u8 = np.frombuffer(raw_frames, dtype=np.uint8).astype(np.float32)
        audio = (arr_u8 - 128.0) / 128.0
    elif sample_width == 2:
        arr_i16 = np.frombuffer(raw_frames, dtype=np.int16).astype(np.float32)
        audio = arr_i16 / 32768.0
    elif sample_width == 4:
        arr_i32 = np.frombuffer(raw_frames, dtype=np.int32).astype(np.float32)
        audio = arr_i32 / 2147483648.0
    else:
        raise ValueError(f"Неподдерживаемая разрядность WAV: {sample_width * 8} бит.")

    if channels > 1:
        audio = audio.reshape(-1, channels)

    return audio.astype(np.float32), sample_rate, channels


class QwenTTSSynthesizer:
    """Синтезатор речи через HTTP API llama-server (/tts) с автоматическим откатом на Piper."""

    BACKEND_QWEN_TTS = "qwen3-tts"
    BACKEND_FALLBACK = "piper-fallback"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8081",
        lang: str = "ru",
        speaker_ref_b64: str | None = None,
        speaker_ref_text: str | None = None,
        speaker_ref_path: str | Path | None = None,
        timeout_seconds: float = 8.0,
        cooldown_seconds: float = 45.0,
        fallback_synthesizer: SynthesizerProtocol | None = None,
        output_stream_factory: Callable[..., Any] | None = None,
        session: requests.Session | None = None,
    ) -> None:
        """Инициализация клиента Qwen3-TTS.

        Args:
            base_url: Базовый URL сервера llama-server TTS (по умолчанию http://127.0.0.1:8081).
            lang: Язык синтеза (по умолчанию 'ru').
            speaker_ref_b64: Base64 эталонного аудио для клонирования голоса (опционально).
            speaker_ref_text: Текст эталонного аудио (опционально).
            speaker_ref_path: Путь к WAV-файлу эталонного голоса (если speaker_ref_b64 не задан).
            timeout_seconds: Таймаут HTTP-запроса к серверу /tts.
            cooldown_seconds: Время паузы (circuit breaker) после сбоя /tts перед повтором.
            fallback_synthesizer: Резервный синтезатор (по умолчанию ленивый PiperSynthesizer).
            output_stream_factory: Фабрика потока sounddevice.OutputStream (для тестов).
            session: HTTP-сессия requests.Session (для пулинга и моков).
        """
        self._base_url = base_url.rstrip("/")
        self._lang = lang
        self._speaker_ref_b64 = speaker_ref_b64
        self._speaker_ref_text = speaker_ref_text
        self._speaker_ref_path = Path(speaker_ref_path) if speaker_ref_path is not None else None
        self._timeout_seconds = max(0.5, float(timeout_seconds))
        self._cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._cooldown_until: float = 0.0
        self._circuit_lock: threading.Lock = threading.Lock()

        self._fallback_synthesizer: SynthesizerProtocol | None = fallback_synthesizer
        self._output_stream_factory: Callable[..., Any] | None = (
            output_stream_factory
            if output_stream_factory is not None
            else (sd.OutputStream if sd is not None else None)
        )
        self._session: requests.Session = session if session is not None else requests.Session()
        self._backend: str = self.BACKEND_QWEN_TTS

        self._is_speaking: bool = False
        self._session_id: int = 0
        self._stop_event: threading.Event = threading.Event()
        self._stream_lock: threading.Lock = threading.Lock()
        self._current_stream: Any | None = None
        self._playback_thread: threading.Thread | None = None
        self._producer_thread: threading.Thread | None = None
        self._sentence_queue: queue.Queue[str | None] | None = None
        self._fallback_lock: threading.Lock = threading.Lock()

    @property
    def base_url(self) -> str:
        """Базовый URL сервера Qwen3-TTS."""
        return self._base_url

    @property
    def backend(self) -> str:
        """Последний активный бэкенд синтеза ('qwen3-tts' или 'piper-fallback')."""
        return self._backend

    @property
    def is_in_cooldown(self) -> bool:
        """Возвращает True, если активен circuit breaker после сбоя Qwen3-TTS."""
        if self._cooldown_seconds <= 0.0:
            return False
        with self._circuit_lock:
            return time.monotonic() < self._cooldown_until

    def _speak_with_fallback(self, text: str) -> None:
        """Мгновенно озвучивает текст через резервный PiperSynthesizer."""
        self._backend = self.BACKEND_FALLBACK
        fallback = self._get_fallback()
        fallback.speak(text, blocking=True)

    def _get_fallback(self) -> SynthesizerProtocol:
        """Лениво инициализирует и возвращает резервный PiperSynthesizer."""
        with self._fallback_lock:
            if self._fallback_synthesizer is None:
                from core.voice.synthesizer import PiperSynthesizer

                self._fallback_synthesizer = PiperSynthesizer()
            return self._fallback_synthesizer

    def _resolve_speaker_b64(self) -> str | None:
        """Возвращает base64-кодированный референс голоса из памяти или файла."""
        if self._speaker_ref_b64:
            return self._speaker_ref_b64
        if self._speaker_ref_path is not None and self._speaker_ref_path.is_file():
            try:
                raw = self._speaker_ref_path.read_bytes()
                self._speaker_ref_b64 = base64.b64encode(raw).decode("ascii")
                return self._speaker_ref_b64
            except OSError as exc:
                logger.warning(
                    "Не удалось прочитать speaker_ref_path %s: %s", self._speaker_ref_path, exc
                )
        return None

    def _build_payload(self, text: str) -> dict[str, Any]:
        """Формирует JSON-тело для POST /tts."""
        payload: dict[str, Any] = {
            "input": text,
            "lang": self._lang,
        }
        speaker_b64 = self._resolve_speaker_b64()
        if speaker_b64:
            payload["speaker_ref_b64"] = speaker_b64
        if self._speaker_ref_text:
            payload["speaker_ref_text"] = self._speaker_ref_text
        return payload

    def _request_tts_wav(self, text: str) -> bytes:
        """Запрашивает синтез WAV у локального llama-server /tts."""
        endpoint = f"{self._base_url}/tts"
        payload = self._build_payload(text)
        response = self._session.post(
            endpoint,
            json=payload,
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        content = bytes(response.content)
        if not content:
            raise ValueError("Сервер /tts вернул пустой ответ.")
        return content

    def is_speaking(self) -> bool:
        """Проверяет, активно ли воспроизведение речи (основного или резервного бэкенда)."""
        with self._stream_lock:
            if self._is_speaking:
                return True
        if self._fallback_synthesizer is not None:
            return bool(self._fallback_synthesizer.is_speaking())
        return False

    def stop(self) -> None:
        """Мгновенная остановка воспроизведения и очереди генерации (Barge-In)."""
        with self._stream_lock:
            self._session_id += 1
            self._stop_event.set()
            self._is_speaking = False
            active_stream = self._current_stream
            self._current_stream = None

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

        if active_stream is not None:
            try:
                active_stream.abort()
            except Exception as exc:
                logger.debug("Ошибка при прерывании sounddevice stream: %s", exc)

        if self._fallback_synthesizer is not None:
            try:
                self._fallback_synthesizer.stop()
            except Exception as exc:
                logger.debug("Ошибка при остановке резервного синтезатора: %s", exc)

        playback = self._playback_thread
        if (
            playback is not None
            and playback.is_alive()
            and playback is not threading.current_thread()
        ):
            playback.join(timeout=0.3)
        self._playback_thread = None

        producer = self._producer_thread
        if (
            producer is not None
            and producer.is_alive()
            and producer is not threading.current_thread()
        ):
            producer.join(timeout=0.3)
        self._producer_thread = None

    def _begin_session(self) -> tuple[int, threading.Event]:
        """Создает изолированную сессию воспроизведения с локальным событием остановки."""
        session_stop = threading.Event()
        with self._stream_lock:
            self._session_id += 1
            self._stop_event = session_stop
            self._is_speaking = True
            return self._session_id, session_stop

    def _finish_session(self, session_id: int) -> None:
        """Сбрасывает состояние воспроизведения только если сессия все еще актуальна."""
        with self._stream_lock:
            if self._session_id == session_id:
                self._is_speaking = False
                self._current_stream = None

    def speak(self, text: str, blocking: bool = False) -> None:
        """Синтезирует и произносит заданный текст.

        Args:
            text: Текст для озвучивания.
            blocking: Ожидать ли окончания воспроизведения перед возвратом управления.
        """
        clean_text = text.strip() if text else ""
        if not clean_text:
            return

        if self.is_speaking():
            self.stop()

        session_id, session_stop = self._begin_session()

        if blocking:
            self._run_playback(clean_text, session_id=session_id, stop_event=session_stop)
            return

        self._playback_thread = threading.Thread(
            target=self._run_playback,
            args=(clean_text, session_id, session_stop),
            name="QwenTTSSynthesizerPlayback",
            daemon=True,
        )
        self._playback_thread.start()

    def speak_stream(
        self,
        sentence_iterator: Iterator[str],
        blocking: bool = False,
    ) -> None:
        """Последовательный потоковый синтез и воспроизведение предложений из итератора."""
        if self.is_speaking():
            self.stop()

        session_id, session_stop = self._begin_session()
        sentence_queue: queue.Queue[str | None] = queue.Queue()
        self._sentence_queue = sentence_queue
        producer_done = threading.Event()

        def _producer() -> None:
            try:
                for sentence in sentence_iterator:
                    if session_stop.is_set():
                        break
                    clean_sentence = sentence.strip() if sentence else ""
                    if clean_sentence:
                        sentence_queue.put(clean_sentence)
            except Exception as exc:
                logger.error("Ошибка чтения потока предложений для QwenTTS: %s", exc)
            finally:
                producer_done.set()
                sentence_queue.put(None)

        def _consumer() -> None:
            try:
                while not session_stop.is_set():
                    try:
                        item = sentence_queue.get(timeout=0.05)
                    except queue.Empty:
                        if producer_done.is_set() and sentence_queue.empty():
                            break
                        continue

                    if item is None or session_stop.is_set():
                        break

                    clean_text = item.strip()
                    if clean_text and not session_stop.is_set():
                        self._play_sentence(clean_text, stop_event=session_stop)
            finally:
                self._finish_session(session_id)

        self._producer_thread = threading.Thread(
            target=_producer,
            name="QwenTTSSynthesizerProducer",
            daemon=True,
        )
        self._playback_thread = threading.Thread(
            target=_consumer,
            name="QwenTTSSynthesizerConsumer",
            daemon=True,
        )
        self._producer_thread.start()
        self._playback_thread.start()

        if blocking:
            self._playback_thread.join()
            if self._producer_thread.is_alive():
                self._producer_thread.join(timeout=0.5)

    def _run_playback(
        self,
        text: str,
        session_id: int,
        stop_event: threading.Event,
    ) -> None:
        """Выполняет воспроизведение одиночного текста в рамках изолированной сессии."""
        try:
            self._play_sentence(text, stop_event=stop_event)
        finally:
            self._finish_session(session_id)

    def _play_sentence(self, text: str, stop_event: threading.Event) -> None:
        """Синтезирует через /tts, при сбое включает circuit breaker и откат на Piper."""
        if stop_event.is_set():
            return

        if self.is_in_cooldown:
            self._speak_with_fallback(text)
            return

        try:
            wav_bytes = self._request_tts_wav(text)
            if stop_event.is_set():
                return
            with self._circuit_lock:
                self._cooldown_until = 0.0
            audio, sample_rate, channels = _decode_wav_to_float32(wav_bytes)
            if self._output_stream_factory is None:
                raise RuntimeError("Аудиоустройство вывода (sounddevice) недоступно.")
            self._backend = self.BACKEND_QWEN_TTS
            self._play_pcm(audio, sample_rate, channels, stop_event)
        except Exception as exc:
            if stop_event.is_set():
                return
            if self._cooldown_seconds > 0.0:
                with self._circuit_lock:
                    self._cooldown_until = time.monotonic() + self._cooldown_seconds
            logger.warning(
                "Qwen3-TTS (%s) недоступен (%s). Откат на PiperSynthesizer (cooldown=%.1f с).",
                self._base_url,
                exc,
                self._cooldown_seconds,
            )
            self._speak_with_fallback(text)

    def _play_pcm(
        self,
        audio: np.ndarray,
        sample_rate: int,
        channels: int,
        stop_event: threading.Event,
    ) -> None:
        """Потоковая запись PCM в sounddevice.OutputStream с проверкой stop_event."""
        assert self._output_stream_factory is not None
        block_size = 1024

        with self._output_stream_factory(
            samplerate=sample_rate,
            channels=channels,
            dtype="float32",
        ) as stream:
            with self._stream_lock:
                if stop_event.is_set():
                    return
                self._current_stream = stream

            for idx in range(0, len(audio), block_size):
                if stop_event.is_set():
                    break
                block = audio[idx : idx + block_size]
                try:
                    stream.write(block)
                except Exception as exc:
                    logger.debug("Исключение при записи в OutputStream QwenTTS: %s", exc)
                    break
