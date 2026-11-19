"""
> [!NOTE]
> Модуль core.voice.vad_detector: Детектор активности речи (Voice Activity Detection).
> Интегрирует ONNX-модель Silero VAD v5 на CPU с адаптивным fallback на энергетический анализ.
"""

import logging
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

try:
    import onnxruntime as ort  # type: ignore[import-untyped]

    _HAS_ORT = True
except ImportError:
    ort = None
    _HAS_ORT = False


class VADDetector:
    """
    Детектор голосовой активности на базе нейросети Silero VAD (ONNX)
    с непрерывным отслеживанием состояний речи и энергетическим fallback.
    """

    def __init__(
        self,
        model_path: str | Path | None = None,
        sample_rate: int = 16000,
        chunk_size: int = 512,
        threshold: float = 0.5,
        silence_duration_ms: int = 500,
        energy_threshold: float = 0.015,
    ) -> None:
        """
        Инициализирует параметры VAD детектора.

        Args:
            model_path: Путь к файлу silero_vad.onnx (опционально).
            sample_rate: Частота дискретизации аудио (16000 или 8000 Гц).
            chunk_size: Ожидаемый размер аудиокадра (по умолчанию 512 сэмплов).
            threshold: Порог вероятности речи для Silero VAD (0.0..1.0).
            silence_duration_ms: Длительность тишины в мс для фиксации окончания фразы.
            energy_threshold: Порог RMS для резервного энергетического детектора.
        """
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.threshold = threshold
        self.silence_duration_ms = silence_duration_ms
        self.energy_threshold = energy_threshold

        self._model_path = self._resolve_model_path(model_path)
        self._session: Any = None
        self._input_names: list[str] = []
        self._using_onnx: bool = False

        self._context_size: int = 64 if sample_rate == 16000 else 32
        self._context: np.ndarray = np.zeros((1, self._context_size), dtype=np.float32)
        self._state: np.ndarray = np.zeros((2, 1, 128), dtype=np.float32)
        self._h: np.ndarray = np.zeros((1, 1, 128), dtype=np.float32)
        self._c: np.ndarray = np.zeros((1, 1, 128), dtype=np.float32)

        self._speech_active: bool = False
        self._speech_start: bool = False
        self._speech_end: bool = False
        self._silence_ms: float = 0.0
        self._last_probability: float = 0.0

        self._init_session()

    def _resolve_model_path(self, model_path: str | Path | None) -> Path | None:
        """
        Ищет путь к предобученной ONNX модели Silero VAD.
        Проверяет сначала путь models/silero_vad.onnx в корне проекта перед обращением к fallback.
        """
        if model_path is not None:
            candidate = Path(model_path)
            if candidate.is_file():
                return candidate.resolve()
            return None

        default_paths = [
            Path("models/silero_vad.onnx"),
            Path.cwd() / "models" / "silero_vad.onnx",
            Path(__file__).resolve().parent.parent.parent / "models" / "silero_vad.onnx",
        ]
        for path in default_paths:
            if path.is_file():
                return path.resolve()

        return None

    def _try_init_torch_hub(self) -> bool:
        """
        Попытка загрузки модели Silero VAD через torch.hub при наличии torch.
        """
        try:
            import torch  # type: ignore[import-not-found]

            model, _ = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                onnx=True,
                trust_repo=True,
            )
            if model is not None:
                logger.info("Silero VAD успешно загружен через torch.hub.")
                self._using_onnx = True
                return True
        except Exception as exc:
            logger.debug("Загрузка через torch.hub не удалась: %s", exc)

        return False

    def _init_session(self) -> None:
        """
        Инициализирует сессию ONNX Runtime с провайдером CPU или fallback.
        """
        if _HAS_ORT and self._model_path is not None and ort is not None:
            try:
                opts = ort.SessionOptions()
                opts.inter_op_num_threads = 1
                opts.intra_op_num_threads = 1
                opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

                self._session = ort.InferenceSession(
                    str(self._model_path),
                    sess_options=opts,
                    providers=["CPUExecutionProvider"],
                )
                self._input_names = [i.name for i in self._session.get_inputs()]
                self._using_onnx = True
                logger.info("Silero VAD ONNX успешно загружен: %s", self._model_path)
                return
            except Exception as exc:
                logger.warning("Ошибка загрузки Silero VAD ONNX: %s. Проверка fallback.", exc)

        # Fallback 1: попытка загрузки через torch.hub
        if self._try_init_torch_hub():
            return

        # Fallback 2: энергетический анализ (RMS)
        self._using_onnx = False

    def _compute_energy_speech(self, chunk: np.ndarray) -> tuple[float, bool]:
        """
        Энергетический расчет вероятности речи (RMS) для резервного режима.
        """
        if len(chunk) == 0:
            return 0.0, False

        rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
        prob = min(1.0, rms / (self.energy_threshold * 2.0))
        is_speech = rms >= self.energy_threshold
        return prob, is_speech

    def _run_onnx(self, chunk: np.ndarray) -> float:
        """
        Выполняет инференс Silero VAD через ONNX Runtime.
        """
        chunk_2d = chunk.reshape(1, -1).astype(np.float32)
        x = np.concatenate([self._context, chunk_2d], axis=1)

        if "state" in self._input_names:
            feed: dict[str, Any] = {"input": x, "state": self._state}
            if "sr" in self._input_names:
                feed["sr"] = np.array(self.sample_rate, dtype=np.int64)
            outputs = self._session.run(None, feed)
            prob = float(outputs[0].flatten()[0])
            self._state = outputs[1]
            self._context = x[:, -self._context_size :].copy()
            return prob

        feed = {"input": x, "h": self._h, "c": self._c}
        if "sr" in self._input_names:
            feed["sr"] = np.array(self.sample_rate, dtype=np.int64)
        outputs = self._session.run(None, feed)
        prob = float(outputs[0].flatten()[0])
        self._h = outputs[1]
        self._c = outputs[2]
        self._context = x[:, -self._context_size :].copy()
        return prob

    def process_chunk(self, chunk: np.ndarray) -> bool:
        """
        Обрабатывает аудиокадр и обновляет состояния активности речи.

        Args:
            chunk: Одномерный массив аудиосэмплов float32.

        Returns:
            True, если текущий аудиокадр содержит речь.
        """
        chunk_float = self._normalize_input(chunk)
        prob, is_speech = self._evaluate_chunk(chunk_float)
        self._last_probability = prob

        self._update_speech_state(is_speech, len(chunk_float))
        return is_speech

    def _normalize_input(self, chunk: np.ndarray) -> np.ndarray:
        """
        Преобразует входной массив к одномерному float32 в [-1.0, 1.0].
        """
        flat = chunk.flatten()
        if np.issubdtype(flat.dtype, np.integer):
            max_val = float(np.iinfo(flat.dtype).max)
            return flat.astype(np.float32) / max_val
        return flat.astype(np.float32)

    def _evaluate_chunk(self, chunk: np.ndarray) -> tuple[float, bool]:
        """
        Оценивает вероятность наличия речи в кадре через ONNX или fallback.
        """
        if self._using_onnx and self._session is not None:
            try:
                prob = self._run_onnx(chunk)
                return prob, (prob >= self.threshold)
            except Exception as exc:
                logger.debug("Ошибка ONNX VAD инференса: %s. Переключение на fallback.", exc)

        prob, is_speech = self._compute_energy_speech(chunk)
        return prob, is_speech

    def _update_speech_state(self, is_speech: bool, sample_count: int) -> None:
        """
        Обновляет счетчики пауз и флаги начала/окончания речевого сегмента.
        """
        chunk_duration_ms = (sample_count / float(self.sample_rate)) * 1000.0

        if is_speech:
            self._handle_active_speech()
            return

        self._handle_silence(chunk_duration_ms)

    def _handle_active_speech(self) -> None:
        """
        Обработка кадра с активной речью.
        """
        if not self._speech_active:
            self._speech_active = True
            self._speech_start = True
        else:
            self._speech_start = False

        self._speech_end = False
        self._silence_ms = 0.0

    def _handle_silence(self, chunk_duration_ms: float) -> None:
        """
        Обработка кадра тишины с аккумуляцией паузы.
        """
        self._speech_start = False
        if not self._speech_active:
            self._speech_end = False
            return

        self._silence_ms += chunk_duration_ms
        if self._silence_ms >= self.silence_duration_ms:
            self._speech_active = False
            self._speech_end = True
            self._silence_ms = 0.0
        else:
            self._speech_end = False

    def reset_states(self) -> None:
        """
        Сбрасывает внутренние состояния детектора и рекуррентные тензоры ONNX.
        """
        self._speech_active = False
        self._speech_start = False
        self._speech_end = False
        self._silence_ms = 0.0
        self._last_probability = 0.0
        self._context.fill(0.0)
        self._state.fill(0.0)
        self._h.fill(0.0)
        self._c.fill(0.0)

    @property
    def is_speech_active(self) -> bool:
        """Находится ли детектор внутри активного речевого сегмента."""
        return self._speech_active

    @property
    def is_speech_start(self) -> bool:
        """Зафиксирован ли переход к речи в текущем кадре."""
        return self._speech_start

    @property
    def speech_start(self) -> bool:
        """Алиас для is_speech_start."""
        return self._speech_start

    @property
    def is_speech_end(self) -> bool:
        """Зафиксирован ли переход к тишине (конец фразы) в текущем кадре."""
        return self._speech_end

    @property
    def speech_end(self) -> bool:
        """Алиас для is_speech_end."""
        return self._speech_end

    @property
    def last_probability(self) -> float:
        """Последняя вычисленная вероятность наличия речи."""
        return self._last_probability

    @property
    def using_onnx(self) -> bool:
        """Используется ли нейросетевая модель ONNX."""
        return self._using_onnx

    @property
    def model_path(self) -> Path | None:
        """Путь к файлу ONNX модели Silero VAD."""
        return self._model_path
