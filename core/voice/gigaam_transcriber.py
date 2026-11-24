"""Модуль распознавания речи (STT) на базе GigaAM v3 ONNX (INT8 CPU).

> [!NOTE]
> **Назначение файла:** Реализация GigaAMOnnxTranscriber для точного распознавания русской и
> IT-терминологической речи без PyTorch (только onnxruntime + NumPy STFT/Mel + жадный CTC-декодер
> + нормализация брендов) с мгновенным авто-откатом на FasterWhisperTranscriber.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from core.paths import get_models_dir
from core.voice.voice_pipeline import TranscriberProtocol

try:
    import onnxruntime as ort  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    ort = None

logger = logging.getLogger(__name__)

GIGAAM_SAMPLE_RATE = 16000
GIGAAM_N_FFT = 400
GIGAAM_WIN_LENGTH = 400
GIGAAM_HOP_LENGTH = 160
GIGAAM_N_MELS = 64
GIGAAM_FMIN = 0.0
GIGAAM_FMAX = 8000.0

DEFAULT_MODEL_FILENAME = "v3_e2e_ctc.int8.onnx"
DEFAULT_VOCAB_FILENAME = "v3_e2e_ctc_vocab.txt"

_BRAND_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(?<!\w)анти\s*гравити(?!\w)"), "Antigravity"),
    (re.compile(r"(?i)(?<!\w)ютуб(?!\w)"), "YouTube"),
    (re.compile(r"(?i)(?<!\w)гугл\s+хром(?!\w)"), "Google Chrome"),
    (re.compile(r"(?i)(?<!\w)хром(?!\w)"), "Chrome"),
    (re.compile(r"(?i)(?<!\w)гит\s*хаб(?!\w)"), "GitHub"),
)


def normalize_brand_terms(text: str) -> str:
    """Нормализует частые русскоязычные транслитерации IT-брендов в латиницу."""
    if not text:
        return ""
    result = text
    for pattern, replacement in _BRAND_REPLACEMENTS:
        result = pattern.sub(replacement, result)
    return result


def _hz_to_mel_slaney(freq_hz: np.ndarray | float) -> np.ndarray:
    """Переводит частоту (Гц) в шкалу мел (формула Slaney/librosa)."""
    f_min = 0.0
    f_sp = 200.0 / 3.0
    min_log_hz = 1000.0
    min_log_mel = (min_log_hz - f_min) / f_sp
    logstep = np.log(6.4) / 27.0

    freqs = np.asarray(freq_hz, dtype=np.float64)
    mels = (freqs - f_min) / f_sp
    high_mask = freqs >= min_log_hz
    if np.any(high_mask):
        mels = np.where(
            high_mask,
            min_log_mel + np.log(np.maximum(freqs, min_log_hz) / min_log_hz) / logstep,
            mels,
        )
    return mels


def _mel_to_hz_slaney(mels: np.ndarray) -> np.ndarray:
    """Переводит мел-шкалу обратно в герцы (формула Slaney/librosa)."""
    f_min = 0.0
    f_sp = 200.0 / 3.0
    min_log_hz = 1000.0
    min_log_mel = (min_log_hz - f_min) / f_sp
    logstep = np.log(6.4) / 27.0

    freqs = f_min + f_sp * mels
    high_mask = mels >= min_log_mel
    if np.any(high_mask):
        freqs = np.where(
            high_mask,
            min_log_hz * np.exp(logstep * (mels - min_log_mel)),
            freqs,
        )
    return freqs


def build_mel_filterbank(
    sample_rate: int = GIGAAM_SAMPLE_RATE,
    n_fft: int = GIGAAM_N_FFT,
    n_mels: int = GIGAAM_N_MELS,
    fmin: float = GIGAAM_FMIN,
    fmax: float = GIGAAM_FMAX,
) -> np.ndarray:
    """Создает матрицу треугольных мел-фильтров формы (n_mels, n_fft // 2 + 1) на чистом NumPy."""
    n_freqs = n_fft // 2 + 1
    fft_freqs = np.linspace(0.0, float(sample_rate) / 2.0, n_freqs, dtype=np.float64)

    mel_min = float(_hz_to_mel_slaney(fmin))
    mel_max = float(_hz_to_mel_slaney(fmax))
    mel_points = np.linspace(mel_min, mel_max, n_mels + 2, dtype=np.float64)
    hz_points = _mel_to_hz_slaney(mel_points)

    f_diff = np.diff(hz_points)
    ramps = hz_points[:, None] - fft_freqs[None, :]

    weights = np.zeros((n_mels, n_freqs), dtype=np.float64)
    for idx in range(n_mels):
        lower = -ramps[idx] / max(f_diff[idx], 1e-12)
        upper = ramps[idx + 2] / max(f_diff[idx + 1], 1e-12)
        weights[idx] = np.maximum(0.0, np.minimum(lower, upper))

    # Slaney-нормализация площади каждого треугольного фильтра
    enorm = 2.0 / np.maximum(hz_points[2 : n_mels + 2] - hz_points[:n_mels], 1e-12)
    weights *= enorm[:, None]
    return weights.astype(np.float32)


def compute_log_mel_spectrogram(
    audio: np.ndarray,
    mel_filterbank: np.ndarray | None = None,
    *,
    n_fft: int = GIGAAM_N_FFT,
    win_length: int = GIGAAM_WIN_LENGTH,
    hop_length: int = GIGAAM_HOP_LENGTH,
    n_mels: int = GIGAAM_N_MELS,
) -> np.ndarray:
    """Вычисляет log-mel спектрограмму формы (n_mels, num_frames) на чистом NumPy."""
    if audio.size == 0:
        return np.zeros((n_mels, 0), dtype=np.float32)

    pad = n_fft // 2
    padded = np.pad(
        audio.astype(np.float32), (pad, pad), mode="reflect" if audio.size > pad else "constant"
    )
    if padded.size < win_length:
        padded = np.pad(padded, (0, win_length - padded.size), mode="constant")

    num_frames = 1 + (padded.size - win_length) // hop_length
    strides = (padded.strides[0] * hop_length, padded.strides[0])
    frames = np.lib.stride_tricks.as_strided(
        padded,
        shape=(num_frames, win_length),
        strides=strides,
    )

    window = np.hanning(win_length + 1)[:-1].astype(np.float32)
    windowed = frames * window[None, :]
    spectrum = np.fft.rfft(windowed, n=n_fft, axis=1)
    power_spec = (spectrum.real**2 + spectrum.imag**2).astype(np.float32)

    fb = (
        mel_filterbank
        if mel_filterbank is not None
        else build_mel_filterbank(n_fft=n_fft, n_mels=n_mels)
    )
    mel_energy = np.matmul(fb, power_spec.T)
    log_mel: np.ndarray = np.log(np.maximum(mel_energy, 1e-6)).astype(np.float32)
    return log_mel


def load_ctc_vocab(vocab_path: Path) -> tuple[list[str], int]:
    """Загружает словарь токенов GigaAM CTC и определяет индекс blank-токена.

    Поддерживает форматы:
    - Построчный список токенов (`токен` на каждой строке, индекс = номер строки).
    - Формат `<token> <id>` (например, `<blk> 1024`).
    """
    raw_lines = vocab_path.read_text(encoding="utf-8").splitlines()
    non_empty = [line for line in raw_lines if line != ""]
    if not non_empty:
        raise ValueError(f"Файл словаря CTC пуст: {vocab_path}")

    indexed_pattern = re.compile(r"^(.*?)[\t ]+(\d+)$")
    parsed_pairs: list[tuple[str, int]] = []
    all_indexed = True
    for line in non_empty:
        match = indexed_pattern.match(line)
        if not match:
            all_indexed = False
            break
        parsed_pairs.append((match.group(1), int(match.group(2))))

    if all_indexed and parsed_pairs:
        max_id = max(idx for _, idx in parsed_pairs)
        tokens = [""] * (max_id + 1)
        for token, idx in parsed_pairs:
            tokens[idx] = token
    else:
        tokens = list(raw_lines)

    blank_id = len(tokens) - 1
    for idx, tok in enumerate(tokens):
        if tok in {"<blk>", "<blank>", "<eps>"}:
            blank_id = idx
            break

    return tokens, blank_id


def greedy_ctc_decode(
    logits: np.ndarray,
    vocab: Sequence[str],
    blank_id: int,
) -> str:
    """Выполняет жадный CTC-декодинг логитов модели GigaAM."""
    if logits.size == 0 or not vocab:
        return ""

    arr = np.asarray(logits)
    if arr.ndim == 3:
        arr = arr[0]
    if arr.ndim != 2:
        raise ValueError(f"Неожиданная размерность логитов CTC: {arr.shape}")

    vocab_size = len(vocab)
    if arr.shape[0] == vocab_size and arr.shape[1] != vocab_size:
        arr = arr.T

    token_ids = np.argmax(arr, axis=-1)
    ignored_tokens = {"<blk>", "<blank>", "<eps>", "<unk>", "<pad>", "<s>", "</s>"}

    pieces: list[str] = []
    prev_id = -1
    for raw_id in token_ids.tolist():
        tid = int(raw_id)
        if tid == prev_id:
            continue
        prev_id = tid
        if tid == blank_id or tid < 0 or tid >= vocab_size:
            continue
        token = vocab[tid]
        if token in ignored_tokens:
            continue
        pieces.append(token)

    text = "".join(pieces).replace("\u2581", " ")
    return re.sub(r"\s+", " ", text).strip()


class GigaAMOnnxTranscriber:
    """Транскрибатор речи на базе GigaAM v3 INT8 ONNX с авто-откатом на Faster-Whisper."""

    DEFAULT_MODEL_PATH = Path("models/gigaam") / DEFAULT_MODEL_FILENAME
    DEFAULT_VOCAB_PATH = Path("models/gigaam") / DEFAULT_VOCAB_FILENAME

    def __init__(
        self,
        model_path: str | Path | None = None,
        vocab_path: str | Path | None = None,
        providers: Sequence[str] = ("CPUExecutionProvider",),
        normalize_brands: bool = True,
        session_factory: Callable[..., Any] | None = None,
        fallback_transcriber: TranscriberProtocol | None = None,
    ) -> None:
        """Инициализация транскрибатора GigaAM ONNX.

        Args:
            model_path: Путь к ONNX-файлу `v3_e2e_ctc.int8.onnx`.
            vocab_path: Путь к файлу словаря `v3_e2e_ctc_vocab.txt`.
            providers: Предпочтительные ONNX Runtime провайдеры.
            normalize_brands: Нормализовать ли IT-бренды (Antigravity, YouTube, Chrome, GitHub).
            session_factory: Фабрика ort.InferenceSession (для моков в тестах).
            fallback_transcriber: Резервный транскрибатор (по умолчанию FasterWhisperTranscriber).
        """
        self._model_path: Path = (
            Path(model_path)
            if model_path is not None
            else self._resolve_default_path(DEFAULT_MODEL_FILENAME)
        )
        self._vocab_path: Path = (
            Path(vocab_path)
            if vocab_path is not None
            else self._resolve_default_path(DEFAULT_VOCAB_FILENAME)
        )
        self._providers: list[str] = list(providers)
        self._normalize_brands: bool = normalize_brands
        self._session_factory: Callable[..., Any] | None = session_factory
        self._fallback_transcriber: TranscriberProtocol | None = fallback_transcriber

        self._session: Any | None = None
        self._vocab: list[str] = []
        self._blank_id: int = 0
        self._mel_fb: np.ndarray | None = None
        self._is_fallback: bool = False
        self._lock: threading.Lock = threading.Lock()

    @classmethod
    def _resolve_default_path(cls, filename: str) -> Path:
        """Находит путь к файлу GigaAM в каталоге моделей или корне проекта."""
        candidates = [
            get_models_dir() / "gigaam" / filename,
            Path("models") / "gigaam" / filename,
            Path(__file__).resolve().parent.parent.parent / "models" / "gigaam" / filename,
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        return candidates[0]

    @property
    def model_path(self) -> Path:
        """Путь к файлу ONNX модели GigaAM."""
        return self._model_path

    @property
    def vocab_path(self) -> Path:
        """Путь к файлу словаря CTC."""
        return self._vocab_path

    @property
    def is_initialized(self) -> bool:
        """Возвращает True, если ONNX-сессия GigaAM загружена."""
        with self._lock:
            return self._session is not None

    @property
    def is_fallback(self) -> bool:
        """Возвращает True, если активен резервный режим Faster-Whisper."""
        with self._lock:
            return self._is_fallback

    def _get_fallback(self) -> TranscriberProtocol:
        """Лениво создает или возвращает резервный FasterWhisperTranscriber."""
        if self._fallback_transcriber is None:
            from core.voice.transcriber import FasterWhisperTranscriber

            self._fallback_transcriber = FasterWhisperTranscriber()
        return self._fallback_transcriber

    def initialize(self) -> bool:
        """Загружает ONNX-модель GigaAM и словарь CTC; при отсутствии переходит в fallback."""
        with self._lock:
            if self._session is not None:
                return True
            if self._is_fallback:
                return False

            if not self._vocab_path.is_file():
                logger.warning(
                    "Словарь GigaAM не найден (%s). Откат на Faster-Whisper.", self._vocab_path
                )
                self._is_fallback = True
                return False

            if self._session_factory is None and (not self._model_path.is_file() or ort is None):
                logger.warning(
                    "Модель GigaAM ONNX или onnxruntime недоступны. Откат на Faster-Whisper."
                )
                self._is_fallback = True
                return False

            return self._load_session_safe()

    def _load_session_safe(self) -> bool:
        """Безопасная инициализация InferenceSession и словаря под блокировкой."""
        try:
            vocab, blank_id = load_ctc_vocab(self._vocab_path)
            factory = self._session_factory or (ort.InferenceSession if ort is not None else None)
            if factory is None:
                raise RuntimeError("onnxruntime.InferenceSession недоступен.")

            self._session = factory(str(self._model_path), providers=self._providers)
            self._vocab = vocab
            self._blank_id = blank_id
            self._mel_fb = build_mel_filterbank()
            self._is_fallback = False
            logger.info("GigaAM v3 ONNX успешно инициализирован (%s).", self._model_path.name)
            return True
        except Exception as exc:
            logger.warning("Ошибка загрузки GigaAM v3 ONNX (%s). Откат на Faster-Whisper.", exc)
            self._session = None
            self._is_fallback = True
            return False

    def _preprocess_audio(self, audio: np.ndarray) -> np.ndarray:
        """Нормализует входной аудио-массив в 1D float32."""
        arr = np.asarray(audio)
        if arr.ndim > 1:
            arr = arr.flatten()
        if arr.size == 0:
            return np.empty(0, dtype=np.float32)
        if np.issubdtype(arr.dtype, np.integer):
            max_val = float(np.iinfo(arr.dtype).max)
            return (arr.astype(np.float32) / max_val).astype(np.float32)
        if arr.dtype != np.float32:
            return arr.astype(np.float32)
        return arr

    def _prepare_onnx_inputs(self, log_mel: np.ndarray) -> dict[str, np.ndarray]:
        """Формирует словарь входных тензоров с учетом сигнатуры ONNX-графа."""
        feat_name = "features"
        len_name = "feature_lengths"
        features = log_mel[None, :, :].astype(np.float32)

        if self._session is not None and hasattr(self._session, "get_inputs"):
            try:
                inputs = list(self._session.get_inputs())
                if len(inputs) >= 1 and getattr(inputs[0], "name", None):
                    feat_name = str(inputs[0].name)
                    shape = getattr(inputs[0], "shape", None)
                    if (
                        isinstance(shape, (list, tuple))
                        and len(shape) == 3
                        and shape[2] == GIGAAM_N_MELS
                    ):
                        features = log_mel.T[None, :, :].astype(np.float32)
                if len(inputs) >= 2 and getattr(inputs[1], "name", None):
                    len_name = str(inputs[1].name)
            except Exception:
                pass

        lengths = np.array([log_mel.shape[1]], dtype=np.int64)
        return {feat_name: features, len_name: lengths}

    def transcribe(
        self,
        audio: np.ndarray,
        language: str = "ru",
        beam_size: int = 1,
    ) -> str:
        """Распознает речь из 1D аудио-массива (16 кГц) через GigaAM ONNX или Whisper fallback."""
        if audio is None or audio.size == 0:
            return ""

        if not self.is_initialized and not self.is_fallback:
            self.initialize()

        if self.is_fallback or self._session is None:
            raw_fb = self._get_fallback().transcribe(audio, language=language, beam_size=beam_size)
            return normalize_brand_terms(raw_fb) if self._normalize_brands else raw_fb

        processed = self._preprocess_audio(audio)
        if processed.size == 0:
            return ""

        try:
            log_mel = compute_log_mel_spectrogram(processed, mel_filterbank=self._mel_fb)
            if log_mel.shape[1] == 0:
                return ""
            inputs = self._prepare_onnx_inputs(log_mel)
            outputs = self._session.run(None, inputs)
            text = greedy_ctc_decode(outputs[0], self._vocab, self._blank_id)
            return normalize_brand_terms(text) if self._normalize_brands else text
        except Exception as exc:
            logger.error("Сбой инференса GigaAM ONNX (%s). Переключение на Faster-Whisper.", exc)
            with self._lock:
                self._is_fallback = True
            raw_fb = self._get_fallback().transcribe(audio, language=language, beam_size=beam_size)
            return normalize_brand_terms(raw_fb) if self._normalize_brands else raw_fb
