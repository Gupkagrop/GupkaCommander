"""Модуль распознавания речи (STT) на базе Faster-Whisper.

> [!NOTE]
> **Назначение файла:** Реализация FasterWhisperTranscriber для распознавания речи на CPU
> с использованием CTranslate2 INT8, ленивой загрузки и fallback-режима.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

import numpy as np

try:
    import faster_whisper  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    faster_whisper = None

logger = logging.getLogger(__name__)


class FasterWhisperTranscriber:
    """Транскрибатор речи на базе Faster-Whisper (INT8 CPU) с поддержкой ленивой инициализации."""

    DEFAULT_DOWNLOAD_ROOT = "models/whisper"

    def __init__(
        self,
        model_size_or_path: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
        cpu_threads: int = 4,
        download_root: str | None = "models/whisper",
        local_files_only: bool = False,
        model_factory: Callable[..., Any] | None = None,
        fallback_text: str = "",
    ) -> None:
        """Инициализация конфигурации транскрибатора.

        Args:
            model_size_or_path: Размер или путь к модели Whisper (например, 'base', 'small').
            device: Вычислительное устройство (по умолчанию 'cpu').
            compute_type: Тип квантования ('int8' для CPU).
            cpu_threads: Число потоков CPU для вычислений.
            download_root: Каталог для кэширования загружаемых весов (по умолчанию models/whisper).
            local_files_only: Использовать только локально сохраненные веса без загрузки.
            model_factory: Фабрика создания модели (для внедрения моков и тестирования).
            fallback_text: Текст по умолчанию при работе в резервном (fallback) режиме.
        """
        self._model_size_or_path: str = model_size_or_path
        self._device: str = device
        self._compute_type: str = compute_type
        self._cpu_threads: int = cpu_threads
        if download_root is None:
            from core.paths import get_models_dir

            self._download_root: str | None = str(get_models_dir() / "whisper")
        elif download_root == "models/whisper":
            from core.paths import get_models_dir, is_bundled

            if is_bundled():
                self._download_root = str(get_models_dir() / "whisper")
            else:
                self._download_root = download_root
        else:
            self._download_root = download_root
        self._local_files_only: bool = local_files_only
        self._model_factory: Callable[..., Any] | None = (
            model_factory
            if model_factory is not None
            else (faster_whisper.WhisperModel if faster_whisper is not None else None)
        )
        self._fallback_text: str = fallback_text

        self._model: Any | None = None
        self._is_fallback: bool = False
        self._lock: threading.Lock = threading.Lock()

    @property
    def download_root(self) -> str | None:
        """Каталог для кэширования загружаемых весов моделей."""
        return self._download_root

    @property
    def is_initialized(self) -> bool:
        """Флаг успешной инициализации модели."""
        with self._lock:
            return self._model is not None

    @property
    def is_fallback(self) -> bool:
        """Флаг активации резервного режима (fallback/mock)."""
        with self._lock:
            return self._is_fallback

    @property
    def model_size_or_path(self) -> str:
        """Наименование или путь используемой модели."""
        return self._model_size_or_path

    @property
    def device(self) -> str:
        """Устройство инференса."""
        return self._device

    @property
    def compute_type(self) -> str:
        """Тип квантования модели."""
        return self._compute_type

    def initialize(self) -> bool:
        """Ленивая или явная инициализация модели Faster-Whisper.

        Returns:
            bool: True при успешной загрузке, False при переходе в режим fallback.
        """
        with self._lock:
            if self._model is not None:
                return True
            if self._is_fallback and self._model_factory is None:
                return False

            if self._model_factory is None:
                logger.warning("Библиотека faster_whisper не найдена. Переход в режим fallback.")
                self._is_fallback = True
                return False

            return self._load_model_safe()

    def _load_model_safe(self) -> bool:
        """Безопасная загрузка модели внутри блокировки."""
        assert self._model_factory is not None
        try:
            logger.info(
                "Загрузка модели Faster-Whisper '%s' (device=%s, compute_type=%s)...",
                self._model_size_or_path,
                self._device,
                self._compute_type,
            )
            kwargs: dict[str, Any] = {
                "device": self._device,
                "compute_type": self._compute_type,
                "cpu_threads": self._cpu_threads,
                "local_files_only": self._local_files_only,
            }
            if self._download_root is not None:
                kwargs["download_root"] = self._download_root

            self._model = self._model_factory(self._model_size_or_path, **kwargs)
            self._is_fallback = False
            logger.info("Модель Faster-Whisper успешно инициализирована.")
            return True
        except Exception as exc:
            logger.warning(
                "Не удалось загрузить Faster-Whisper '%s': %s. Активирован fallback-режим.",
                self._model_size_or_path,
                exc,
            )
            self._model = None
            self._is_fallback = True
            return False

    def transcribe(
        self,
        audio: np.ndarray,
        language: str = "ru",
        beam_size: int = 1,
    ) -> str:
        """Транскрибация аудио-массива в текст.

        Args:
            audio: 1D аудиоданные (float32, 16 кГц).
            language: Языковой код ISO (по умолчанию 'ru').
            beam_size: Размер пучка декодирования (1 для жадного поиска).

        Returns:
            str: Очищенная текстовая транскрипция.
        """
        if audio is None or audio.size == 0:
            return ""

        if not self.is_initialized and not self.is_fallback:
            self.initialize()

        if self.is_fallback or self._model is None:
            logger.debug("Транскрибация в fallback-режиме: '%s'", self._fallback_text)
            return self._fallback_text

        processed_audio = self._preprocess_audio(audio)
        if processed_audio.size == 0:
            return ""

        return self._run_inference(processed_audio, language=language, beam_size=beam_size)

    def _preprocess_audio(self, audio: np.ndarray) -> np.ndarray:
        """Нормализация входного аудио в одномерный float32 массив."""
        if audio.ndim > 1:
            audio = audio.flatten()

        if audio.size == 0:
            return audio

        if np.issubdtype(audio.dtype, np.integer):
            max_val = float(np.iinfo(audio.dtype).max)
            return (audio.astype(np.float32) / max_val).astype(np.float32)

        if audio.dtype != np.float32:
            return audio.astype(np.float32)

        return audio

    def _run_inference(self, audio: np.ndarray, language: str, beam_size: int) -> str:
        """Запуск инференса модели Whisper."""
        if self._model is None:
            return self._fallback_text

        try:
            segments, _ = self._model.transcribe(
                audio,
                language=language,
                beam_size=beam_size,
                vad_filter=False,
            )
            text_parts = [segment.text.strip() for segment in segments if segment.text]
            return " ".join(text_parts).strip()
        except Exception as exc:
            logger.error("Ошибка при инференсе Faster-Whisper: %s", exc)
            return self._fallback_text
