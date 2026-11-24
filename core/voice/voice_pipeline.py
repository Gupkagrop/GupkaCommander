"""
> [!NOTE]
> Модуль core.voice.voice_pipeline: Оркестратор голосового конвейера GupkaCommander.
> Координирует захват звука, VAD, распознавание речи (STT), синтез (TTS) и Barge-In.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np

logger = logging.getLogger(__name__)


class PipelineState(StrEnum):
    """Состояния голосового конвейера."""

    IDLE = "IDLE"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"


@runtime_checkable
class AudioCaptureProtocol(Protocol):
    """Протокол для захвата аудио."""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def read_chunk(self, timeout: float | None = None) -> np.ndarray | None: ...
    def clear(self) -> None: ...
    def is_active(self) -> bool: ...
    def mute(self) -> None: ...
    def unmute(self) -> None: ...


@runtime_checkable
class VADDetectorProtocol(Protocol):
    """Протокол для детектора активности речи."""

    def process_chunk(self, chunk: np.ndarray) -> bool: ...
    def reset_states(self) -> None: ...
    @property
    def is_speech_active(self) -> bool: ...


@runtime_checkable
class TranscriberProtocol(Protocol):
    """Протокол для распознавания речи (STT)."""

    def transcribe(self, audio: np.ndarray, language: str = "ru", beam_size: int = 1) -> str: ...


@runtime_checkable
class SynthesizerProtocol(Protocol):
    """Протокол для синтезатора речи (TTS)."""

    def speak(self, text: str, blocking: bool = False) -> None: ...
    def stop(self) -> None: ...
    def is_speaking(self) -> bool: ...


class VoicePipeline:
    """Голосовой конвейер GupkaCommander с поддержкой Barge-In и VAD-активации."""

    def __init__(
        self,
        audio_capture: AudioCaptureProtocol | None = None,
        vad_detector: VADDetectorProtocol | None = None,
        transcriber: TranscriberProtocol | None = None,
        synthesizer: SynthesizerProtocol | None = None,
        silence_duration_ms: int = 500,
        sample_rate: int = 16000,
        chunk_size: int = 512,
        on_state_changed: Callable[[PipelineState], None] | None = None,
        on_speech_start: Callable[[], None] | None = None,
        on_transcription: Callable[[str], None] | None = None,
        on_speech_end: Callable[[], None] | None = None,
        on_audio_level: Callable[[float], None] | None = None,
        echo_guard: bool = False,
        echo_tail_seconds: float = 0.45,
        stt_engine: str = "faster-whisper",
        tts_engine: str = "piper",
        tts_base_url: str = "http://127.0.0.1:8081",
        settings_path: Path | None = None,
    ) -> None:
        """Инициализация голосового конвейера с внедрением зависимостей."""
        self._audio_capture = audio_capture
        self._vad_detector = vad_detector
        self._transcriber = transcriber
        self._synthesizer = synthesizer
        self._injected_transcriber: bool = transcriber is not None
        self._injected_synthesizer: bool = synthesizer is not None

        self.silence_duration_ms = silence_duration_ms
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self._echo_guard = echo_guard
        self._echo_tail_seconds = max(0.0, echo_tail_seconds)
        self._echo_guard_until: float = 0.0
        self._stt_engine = stt_engine
        self._tts_engine = tts_engine
        self._tts_base_url = tts_base_url
        self._settings_path = settings_path

        self.on_state_changed = on_state_changed
        self.on_speech_start = on_speech_start
        self.on_transcription = on_transcription
        self.on_speech_end = on_speech_end
        self.on_audio_level = on_audio_level

        self._state: PipelineState = PipelineState.IDLE
        self._state_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._loop_thread: threading.Thread | None = None
        self._is_muted: bool = False

        # Расчет количества чанков тишины для завершения фразы
        chunk_duration_sec = self.chunk_size / float(self.sample_rate)
        self._silence_chunks_threshold = max(
            1, int((self.silence_duration_ms / 1000.0) / chunk_duration_sec)
        )

    @property
    def audio_capture(self) -> AudioCaptureProtocol:
        """Ленивая инициализация AudioCapture при отсутствии внешней зависимости."""
        if self._audio_capture is None:
            from core.voice.audio_capture import AudioCapture

            self._audio_capture = AudioCapture()
        return self._audio_capture

    @property
    def vad_detector(self) -> VADDetectorProtocol:
        """Ленивая инициализация VADDetector при отсутствии внешней зависимости."""
        if self._vad_detector is None:
            from core.voice.vad_detector import VADDetector

            self._vad_detector = VADDetector()
        return self._vad_detector

    @classmethod
    def from_settings(
        cls,
        settings_path: Path | None = None,
        **kwargs: Any,
    ) -> VoicePipeline:
        """Создает VoicePipeline, считывая выбранные движки STT/TTS из settings.json."""
        stt_eng, tts_eng, tts_url = cls._read_engine_config(settings_path)
        kwargs.setdefault("stt_engine", stt_eng)
        kwargs.setdefault("tts_engine", tts_eng)
        kwargs.setdefault("tts_base_url", tts_url)
        kwargs.setdefault("settings_path", settings_path)
        return cls(**kwargs)

    @staticmethod
    def _read_engine_config(settings_path: Path | None = None) -> tuple[str, str, str]:
        """Считывает (stt_engine, tts_engine, tts_base_url) из settings.json."""
        from core.paths import get_config_path

        path = settings_path if settings_path is not None else get_config_path("settings.json")
        stt_engine = "faster-whisper"
        tts_engine = "piper"
        tts_base_url = "http://127.0.0.1:8081"
        if not path.is_file():
            return stt_engine, tts_engine, tts_base_url

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            models_cfg = data.get("models", {})
            if isinstance(models_cfg, dict):
                stt_cfg = models_cfg.get("stt", {})
                if isinstance(stt_cfg, dict) and stt_cfg.get("engine"):
                    stt_engine = str(stt_cfg["engine"])
                tts_cfg = models_cfg.get("tts", {})
                if isinstance(tts_cfg, dict):
                    if tts_cfg.get("engine"):
                        tts_engine = str(tts_cfg["engine"])
                    if tts_cfg.get("port"):
                        tts_base_url = f"http://127.0.0.1:{int(tts_cfg['port'])}"
        except Exception as exc:
            logger.debug("Ошибка чтения конфигурации голосовых движков из %s: %s", path, exc)

        return stt_engine, tts_engine, tts_base_url

    def _resolve_stt_engine(self) -> str:
        """Возвращает нормализованный идентификатор STT-движка."""
        if self._stt_engine == "auto":
            stt_eng, _, _ = self._read_engine_config(self._settings_path)
            return stt_eng.strip().lower()
        return self._stt_engine.strip().lower()

    def _resolve_tts_engine(self) -> str:
        """Возвращает нормализованный идентификатор TTS-движка."""
        if self._tts_engine == "auto":
            _, tts_eng, tts_url = self._read_engine_config(self._settings_path)
            self._tts_base_url = tts_url
            return tts_eng.strip().lower()
        return self._tts_engine.strip().lower()

    @property
    def transcriber(self) -> TranscriberProtocol:
        """Ленивая инициализация STT-движка (GigaAM v3 ONNX или Faster-Whisper)."""
        if self._transcriber is None:
            engine = self._resolve_stt_engine()
            if engine in {"gigaam", "gigaam-v3", "gigaam_v3", "gigaam-v3-onnx"}:
                from core.voice.gigaam_transcriber import GigaAMOnnxTranscriber

                self._transcriber = GigaAMOnnxTranscriber()
            else:
                from core.voice.transcriber import FasterWhisperTranscriber

                self._transcriber = FasterWhisperTranscriber()
        return self._transcriber

    @property
    def synthesizer(self) -> SynthesizerProtocol:
        """Ленивая инициализация TTS-движка (Qwen3-TTS с откатом на Piper или Piper)."""
        if self._synthesizer is None:
            engine = self._resolve_tts_engine()
            if engine in {"qwen3-tts", "qwen-tts", "qwen_tts"}:
                from core.voice.qwen_tts_synthesizer import QwenTTSSynthesizer

                self._synthesizer = QwenTTSSynthesizer(base_url=self._tts_base_url)
            else:
                from core.voice.synthesizer import PiperSynthesizer

                self._synthesizer = PiperSynthesizer()
        return self._synthesizer

    def reload_engines_from_settings(self) -> None:
        """Перечитывает движки STT/TTS из settings.json и сбрасывает ленивые инстансы."""
        new_stt, new_tts, new_tts_url = self._read_engine_config(self._settings_path)
        old_synth: SynthesizerProtocol | None = None

        with self._state_lock:
            stt_changed = new_stt.strip().lower() != self._stt_engine.strip().lower()
            tts_changed = (
                new_tts.strip().lower() != self._tts_engine.strip().lower()
                or new_tts_url != self._tts_base_url
            )

            self._stt_engine = new_stt
            self._tts_engine = new_tts
            self._tts_base_url = new_tts_url

            if stt_changed and not self._injected_transcriber:
                self._transcriber = None
            if tts_changed:
                old_synth = self._synthesizer
                if not self._injected_synthesizer:
                    self._synthesizer = None

        if old_synth is not None:
            try:
                old_synth.stop()
            except Exception as exc:
                logger.debug("Ошибка остановки прежнего синтезатора при смене настроек: %s", exc)

    @property
    def state(self) -> PipelineState:
        """Текущее состояние конвейера."""
        with self._state_lock:
            return self._state

    def _set_state(self, new_state: PipelineState) -> None:
        """Потокобезопасная смена состояния с вызовом обратного вызова."""
        callback_to_run: Callable[[PipelineState], None] | None = None
        with self._state_lock:
            if self._state == new_state:
                return
            self._state = new_state
            callback_to_run = self.on_state_changed

        if callback_to_run is not None:
            self._safe_invoke(callback_to_run, new_state)

    def _safe_invoke(self, fn: Callable[..., None], *args: object) -> None:
        """Безопасный вызов пользовательского хука с перехватом исключений."""
        try:
            fn(*args)
        except Exception as exc:
            logger.exception("Ошибка при выполнении обратного вызова %s: %s", fn, exc)

    def start(self) -> None:
        """Запуск конвейера в фоновом потоке-демоне."""
        with self._state_lock:
            if self._loop_thread is not None and self._loop_thread.is_alive():
                logger.warning("Голосовой конвейер уже запущен.")
                return

            self._stop_event.clear()
            self.audio_capture.start()
            self._set_state(PipelineState.IDLE)

            self._loop_thread = threading.Thread(
                target=self._pipeline_loop,
                name="VoicePipelineThread",
                daemon=True,
            )
            self._loop_thread.start()
            logger.info("Голосовой конвейер успешно запущен.")

    def stop(self) -> None:
        """Остановка конвейера и освобождение ресурсов."""
        self._stop_event.set()

        try:
            self.synthesizer.stop()
        except Exception as exc:
            logger.warning("Ошибка при остановке синтезатора: %s", exc)

        try:
            self.audio_capture.stop()
        except Exception as exc:
            logger.warning("Ошибка при остановке захвата аудио: %s", exc)

        if self._loop_thread is not None and self._loop_thread.is_alive():
            self._loop_thread.join(timeout=2.0)
            self._loop_thread = None

        self._set_state(PipelineState.IDLE)
        logger.info("Голосовой конвейер остановлен.")

    def is_running(self) -> bool:
        """Проверка активности фонового потока конвейера."""
        return (
            self._loop_thread is not None
            and self._loop_thread.is_alive()
            and not self._stop_event.is_set()
        )

    def set_muted(self, muted: bool) -> None:
        """
        Устанавливает состояние отключения микрофона (Mute).

        Args:
            muted: True, если микрофон отключен.
        """
        with self._state_lock:
            self._is_muted = muted
            capture = self._audio_capture
            if capture is not None:
                if hasattr(capture, "is_muted"):
                    capture.is_muted = muted
                if muted:
                    if hasattr(capture, "mute"):
                        capture.mute()
                    if hasattr(capture, "clear"):
                        capture.clear()
                else:
                    if hasattr(capture, "unmute"):
                        capture.unmute()

            if muted:
                try:
                    self.synthesizer.stop()
                except Exception as exc:
                    logger.debug("Ошибка остановки синтезатора при mute: %s", exc)
                if self._state == PipelineState.LISTENING:
                    self._set_state(PipelineState.IDLE)
                if self.on_audio_level is not None:
                    self._safe_invoke(self.on_audio_level, 0.0)
        logger.info("Состояние микрофона в VoicePipeline: muted=%s", muted)

    def is_muted(self) -> bool:
        """Проверяет, отключен ли микрофон."""
        with self._state_lock:
            return self._is_muted

    def speak(self, text: str, blocking: bool = False) -> None:
        """Воспроизведение речи через PiperSynthesizer."""
        if self._echo_guard:
            self._echo_guard_until = max(
                self._echo_guard_until,
                time.monotonic() + self._echo_tail_seconds,
            )
        self._set_state(PipelineState.SPEAKING)
        try:
            self.synthesizer.speak(text, blocking=blocking)
        finally:
            if self._echo_guard:
                self._echo_guard_until = max(
                    self._echo_guard_until,
                    time.monotonic() + self._echo_tail_seconds,
                )
            if blocking or not self.synthesizer.is_speaking():
                self._set_state(PipelineState.IDLE)

    def _is_echo_suppressed(self) -> bool:
        """Проверяет, подавлен ли вход микрофона из-за речи ассистента и хвоста эха."""
        if not self._echo_guard:
            return False

        now = time.monotonic()
        synth = self._synthesizer
        is_speaking = False
        if synth is not None:
            try:
                is_speaking = bool(synth.is_speaking())
            except Exception:
                is_speaking = False

        if is_speaking or self.state == PipelineState.SPEAKING:
            self._echo_guard_until = max(
                self._echo_guard_until,
                now + self._echo_tail_seconds,
            )
            return True

        return now < self._echo_guard_until

    def _handle_barge_in_if_speaking(self) -> None:
        """Немедленное прерывание воспроизведения речи при начале речи пользователя."""
        if not self.synthesizer.is_speaking():
            return
        logger.info("Barge-In: речь обнаружена во время синтеза. Прерываем синтезатор.")
        try:
            self.synthesizer.stop()
        except Exception as exc:
            logger.warning("Ошибка при вызове stop() у синтезатора: %s", exc)

    def _check_speech_end(
        self,
        is_speech_chunk: bool,
        silence_counter: int,
    ) -> tuple[bool, int]:
        """Определение завершения речи по VAD или порогу пауз."""
        vad_active = getattr(self.vad_detector, "is_speech_active", None)
        if vad_active is not None:
            # Если детектор явно сообщает о завершении активности речи
            return not vad_active, silence_counter

        # Резервный расчет по количеству чанков тишины
        if not is_speech_chunk:
            silence_counter += 1
        else:
            silence_counter = 0

        is_ended = silence_counter >= self._silence_chunks_threshold
        return is_ended, silence_counter

    def _process_audio_buffer(self, buffer: list[np.ndarray]) -> None:
        """Конкатенация аудиобуфера, отправка в STT и вызов хука транскрипции."""
        if not buffer:
            self._set_state(PipelineState.IDLE)
            return

        self._set_state(PipelineState.PROCESSING)
        try:
            audio_array = np.concatenate(buffer, axis=0).astype(np.float32)
            transcription = self.transcriber.transcribe(audio_array)
            if self.on_transcription is not None:
                self._safe_invoke(self.on_transcription, transcription)
        except Exception as exc:
            logger.exception("Ошибка при распознавании речи: %s", exc)
        finally:
            with self._state_lock:
                if self._state == PipelineState.PROCESSING:
                    self._state = PipelineState.IDLE
                    if self.on_state_changed is not None:
                        self._safe_invoke(self.on_state_changed, PipelineState.IDLE)

    def _pipeline_loop(self) -> None:
        """Главный цикл фонового потока конвейера."""
        speech_chunks: list[np.ndarray] = []
        is_speech_active = False
        silence_counter = 0

        while not self._stop_event.is_set():
            # Если находились в состоянии SPEAKING, но синтез завершился
            if self.state == PipelineState.SPEAKING and not self.synthesizer.is_speaking():
                self._set_state(PipelineState.IDLE)

            chunk = self.audio_capture.read_chunk(timeout=0.05)
            if chunk is None:
                if self.on_audio_level is not None and self._is_muted:
                    self._safe_invoke(self.on_audio_level, 0.0)
                continue

            with self._state_lock:
                muted = self._is_muted

            if muted or self._is_echo_suppressed():
                if self.on_audio_level is not None:
                    self._safe_invoke(self.on_audio_level, 0.0)
                if is_speech_active:
                    is_speech_active = False
                    speech_chunks.clear()
                    silence_counter = 0
                    try:
                        self.vad_detector.reset_states()
                    except Exception:
                        pass
                    if self.state == PipelineState.LISTENING:
                        self._set_state(PipelineState.IDLE)
                continue

            if self.on_audio_level is not None:
                try:
                    if len(chunk) > 0:
                        rms = float(np.sqrt(np.mean(np.square(chunk.astype(np.float32)))))
                        level = min(1.0, max(0.0, rms))
                    else:
                        level = 0.0
                    self._safe_invoke(self.on_audio_level, level)
                except Exception as exc:
                    logger.debug("Ошибка вычисления RMS уровня: %s", exc)

            is_speech = bool(self.vad_detector.process_chunk(chunk))

            if is_speech:
                self._handle_barge_in_if_speaking()
                if not is_speech_active:
                    is_speech_active = True
                    self._set_state(PipelineState.LISTENING)
                    if self.on_speech_start is not None:
                        self._safe_invoke(self.on_speech_start)

            if is_speech_active:
                speech_ended, silence_counter = self._check_speech_end(is_speech, silence_counter)
                if not speech_ended or is_speech:
                    speech_chunks.append(chunk)

                if speech_ended:
                    is_speech_active = False
                    silence_counter = 0
                    if self.on_speech_end is not None:
                        self._safe_invoke(self.on_speech_end)

                    current_buffer = list(speech_chunks)
                    speech_chunks.clear()
                    try:
                        self.vad_detector.reset_states()
                    except Exception as exc:
                        logger.warning("Ошибка reset_states() у VAD: %s", exc)

                    self._process_audio_buffer(current_buffer)
