"""
> [!NOTE]
> Модуль tests.test_voice_pipeline: Модульные тесты для VoicePipeline.
> Проверяет смену состояний, Barge-In, буферизацию речи, вызовы хуков и жизненный цикл.
"""

import queue
import time
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np

from core.voice.voice_pipeline import (
    AudioCaptureProtocol,
    PipelineState,
    SynthesizerProtocol,
    TranscriberProtocol,
    VADDetectorProtocol,
    VoicePipeline,
)


class MockAudioCapture:
    """Мок для захвата аудио с очередью чанков."""

    def __init__(self) -> None:
        self.chunks_queue: queue.Queue[np.ndarray | None] = queue.Queue()
        self.started = False
        self.stopped = False
        self.cleared = False

    def start(self) -> None:
        self.started = True
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True
        self.started = False

    def read_chunk(self, timeout: float | None = None) -> np.ndarray | None:
        try:
            return self.chunks_queue.get(timeout=timeout or 0.05)
        except queue.Empty:
            return None

    def clear(self) -> None:
        self.cleared = True
        while not self.chunks_queue.empty():
            try:
                self.chunks_queue.get_nowait()
            except queue.Empty:
                break

    def is_active(self) -> bool:
        return self.started and not self.stopped

    def mute(self) -> None:
        self.muted = True

    def unmute(self) -> None:
        self.muted = False


class MockVADDetector:
    """Мок для детектора речи с управляемым флагом speech."""

    def __init__(self, is_speech_active: bool = False) -> None:
        self._is_speech_active = is_speech_active
        self.speech_flags: list[bool] = []
        self.reset_count = 0

    @property
    def is_speech_active(self) -> bool:
        return self._is_speech_active

    @is_speech_active.setter
    def is_speech_active(self, value: bool) -> None:
        self._is_speech_active = value

    def process_chunk(self, chunk: np.ndarray) -> bool:
        if self.speech_flags:
            val = self.speech_flags.pop(0)
            self._is_speech_active = val
            return val
        return self._is_speech_active

    def reset_states(self) -> None:
        self.reset_count += 1
        self._is_speech_active = False


class MockTranscriber:
    """Мок для транскрибера STT."""

    def __init__(self, return_text: str = "Тестовая транскрипция") -> None:
        self.return_text = return_text
        self.transcribed_audios: list[np.ndarray] = []

    def transcribe(self, audio: np.ndarray, language: str = "ru", beam_size: int = 1) -> str:
        self.transcribed_audios.append(audio)
        return self.return_text


class MockSynthesizer:
    """Мок для синтезатора речи TTS с поддержкой Barge-In."""

    def __init__(self) -> None:
        self._speaking = False
        self.spoken_texts: list[str] = []
        self.stopped = False

    def speak(self, text: str, blocking: bool = False) -> None:
        self._speaking = True
        self.spoken_texts.append(text)
        if blocking:
            self._speaking = False

    def stop(self) -> None:
        self._speaking = False
        self.stopped = True

    def is_speaking(self) -> bool:
        return self._speaking


def test_pipeline_protocol_compliance() -> None:
    """Проверка соответствия моков протоколам."""
    cap = MockAudioCapture()
    vad = MockVADDetector()
    stt = MockTranscriber()
    tts = MockSynthesizer()

    assert isinstance(cap, AudioCaptureProtocol)
    assert isinstance(vad, VADDetectorProtocol)
    assert isinstance(stt, TranscriberProtocol)
    assert isinstance(tts, SynthesizerProtocol)


def test_pipeline_initialization() -> None:
    """Проверка корректной инициализации параметров и состояния."""
    cap = MockAudioCapture()
    vad = MockVADDetector()
    stt = MockTranscriber()
    tts = MockSynthesizer()

    pipeline = VoicePipeline(
        audio_capture=cap,
        vad_detector=vad,
        transcriber=stt,
        synthesizer=tts,
        silence_duration_ms=400,
        sample_rate=16000,
        chunk_size=512,
    )

    assert pipeline.state == PipelineState.IDLE
    assert not pipeline.is_running()
    assert pipeline.silence_duration_ms == 400


def test_pipeline_lifecycle() -> None:
    """Проверка методов start, stop и is_running."""
    cap = MockAudioCapture()
    vad = MockVADDetector()
    stt = MockTranscriber()
    tts = MockSynthesizer()

    pipeline = VoicePipeline(
        audio_capture=cap,
        vad_detector=vad,
        transcriber=stt,
        synthesizer=tts,
    )

    pipeline.start()
    assert pipeline.is_running()
    assert cap.started

    pipeline.stop()
    assert not pipeline.is_running()
    assert cap.stopped


def test_pipeline_speech_processing_and_hooks() -> None:
    """Проверка цикла детекции речи, вызова хуков и передачи аудио в STT."""
    cap = MockAudioCapture()
    vad = MockVADDetector()
    stt = MockTranscriber(return_text="Команда открыть блокнот")
    tts = MockSynthesizer()

    states_recorded: list[PipelineState] = []
    transcriptions_recorded: list[str] = []
    speech_started_events: list[bool] = []
    speech_ended_events: list[bool] = []

    pipeline = VoicePipeline(
        audio_capture=cap,
        vad_detector=vad,
        transcriber=stt,
        synthesizer=tts,
        silence_duration_ms=100,
        chunk_size=512,
        sample_rate=16000,
        on_state_changed=lambda s: states_recorded.append(s),
        on_speech_start=lambda: speech_started_events.append(True),
        on_speech_end=lambda: speech_ended_events.append(True),
        on_transcription=lambda t: transcriptions_recorded.append(t),
    )

    # Задаем 2 чанка речи, затем чанк тишины
    chunk1 = np.ones(512, dtype=np.float32) * 0.5
    chunk2 = np.ones(512, dtype=np.float32) * 0.7
    silence = np.zeros(512, dtype=np.float32)

    vad.speech_flags = [True, True, False]

    cap.chunks_queue.put(chunk1)
    cap.chunks_queue.put(chunk2)
    cap.chunks_queue.put(silence)

    pipeline.start()

    # Даем конвейеру обработать чанки
    time.sleep(0.3)
    pipeline.stop()

    assert len(speech_started_events) >= 1
    assert len(speech_ended_events) >= 1
    assert "Команда открыть блокнот" in transcriptions_recorded
    assert len(stt.transcribed_audios) == 1
    # Проверяем склейку двух чанков
    assert len(stt.transcribed_audios[0]) == 1024
    assert PipelineState.LISTENING in states_recorded
    assert PipelineState.PROCESSING in states_recorded
    assert pipeline.state == PipelineState.IDLE


def test_pipeline_barge_in() -> None:
    """Проверка немедленной остановки синтезатора при обнаружении речи (Barge-In)."""
    cap = MockAudioCapture()
    vad = MockVADDetector()
    stt = MockTranscriber(return_text="Хватит говорить")
    tts = MockSynthesizer()

    pipeline = VoicePipeline(
        audio_capture=cap,
        vad_detector=vad,
        transcriber=stt,
        synthesizer=tts,
    )

    # Устанавливаем синтезатор в состояние разговора
    tts.speak("Я сейчас долго и подробно рассказываю ответ...", blocking=False)
    assert tts.is_speaking()

    # Подаем речь пользователя
    speech_chunk = np.ones(512, dtype=np.float32) * 0.8
    vad.speech_flags = [True, False]

    cap.chunks_queue.put(speech_chunk)
    cap.chunks_queue.put(np.zeros(512, dtype=np.float32))

    pipeline.start()
    time.sleep(0.2)
    pipeline.stop()

    # Синтезатор должен быть остановлен немедленно (Barge-In)
    assert tts.stopped
    assert not tts.is_speaking()


def test_pipeline_speak_blocking() -> None:
    """Проверка вызова speak в блокирующем режиме."""
    cap = MockAudioCapture()
    vad = MockVADDetector()
    stt = MockTranscriber()
    tts = MockSynthesizer()

    pipeline = VoicePipeline(
        audio_capture=cap,
        vad_detector=vad,
        transcriber=stt,
        synthesizer=tts,
    )

    pipeline.speak("Привет, мир!", blocking=True)
    assert "Привет, мир!" in tts.spoken_texts
    assert pipeline.state == PipelineState.IDLE


def test_voice_pipeline_exports_and_default_instantiation() -> None:
    """Проверка экспортов пакета core.voice и инициализации по умолчанию."""
    import core.voice as voice_pkg

    assert hasattr(voice_pkg, "VoicePipeline")
    assert hasattr(voice_pkg, "AudioCapture")
    assert hasattr(voice_pkg, "VADDetector")
    assert hasattr(voice_pkg, "FasterWhisperTranscriber")
    assert hasattr(voice_pkg, "PiperSynthesizer")
    assert hasattr(voice_pkg, "PipelineState")

    # Инициализация с ленивым созданием компонентов
    pipeline = voice_pkg.VoicePipeline()
    assert isinstance(pipeline.audio_capture, voice_pkg.AudioCapture)
    assert isinstance(pipeline.vad_detector, voice_pkg.VADDetector)
    assert isinstance(pipeline.transcriber, voice_pkg.FasterWhisperTranscriber)
    assert isinstance(pipeline.synthesizer, voice_pkg.PiperSynthesizer)


def test_pipeline_safe_callback_exception_handling() -> None:
    """Проверка устойчивости конвейера к исключениям в пользовательских хуках."""
    cap = MockAudioCapture()
    vad = MockVADDetector()
    stt = MockTranscriber(return_text="Текст")
    tts = MockSynthesizer()

    def bad_callback(*args: Any) -> None:
        raise ValueError("Ошибка в коде пользователя!")

    pipeline = VoicePipeline(
        audio_capture=cap,
        vad_detector=vad,
        transcriber=stt,
        synthesizer=tts,
        on_state_changed=bad_callback,
        on_speech_start=bad_callback,
        on_speech_end=bad_callback,
        on_transcription=bad_callback,
    )

    chunk = np.ones(512, dtype=np.float32)
    vad.speech_flags = [True, False]
    cap.chunks_queue.put(chunk)
    cap.chunks_queue.put(np.zeros(512, dtype=np.float32))

    pipeline.start()
    time.sleep(0.2)
    pipeline.stop()

    # Конвейер должен успешно пережить исключения в хуках
    assert not pipeline.is_running()


def test_pipeline_full_state_machine_cycle() -> None:
    """
    Проверка полного цикла состояний:
    IDLE -> LISTENING -> PROCESSING -> SPEAKING -> IDLE.
    """
    cap = MockAudioCapture()
    vad = MockVADDetector()
    stt = MockTranscriber(return_text="Команда")
    tts = MockSynthesizer()

    recorded_states: list[PipelineState] = []

    pipeline = VoicePipeline(
        audio_capture=cap,
        vad_detector=vad,
        transcriber=stt,
        synthesizer=tts,
        silence_duration_ms=100,
        chunk_size=512,
        sample_rate=16000,
        on_state_changed=lambda s: recorded_states.append(s),
    )

    assert pipeline.state == PipelineState.IDLE
    pipeline.start()

    vad.speech_flags = [True, False]
    cap.chunks_queue.put(np.ones(512, dtype=np.float32) * 0.5)
    cap.chunks_queue.put(np.zeros(512, dtype=np.float32))

    time.sleep(0.2)

    pipeline.speak("Ответ системы", blocking=True)
    pipeline.stop()

    expected_sequence = [
        PipelineState.LISTENING,
        PipelineState.PROCESSING,
        PipelineState.IDLE,
        PipelineState.SPEAKING,
        PipelineState.IDLE,
    ]
    assert recorded_states == expected_sequence
    assert pipeline.state == PipelineState.IDLE


def test_pipeline_barge_in_active_synthesis_interruption() -> None:
    """
    Проверка Barge-In при активном воспроизведении речи:
    прерывание синтеза и переход в LISTENING.
    """
    cap = MockAudioCapture()
    vad = MockVADDetector()
    stt = MockTranscriber(return_text="Остановись")
    tts = MockSynthesizer()

    states_recorded: list[PipelineState] = []
    transcriptions: list[str] = []

    pipeline = VoicePipeline(
        audio_capture=cap,
        vad_detector=vad,
        transcriber=stt,
        synthesizer=tts,
        silence_duration_ms=100,
        on_state_changed=lambda s: states_recorded.append(s),
        on_transcription=lambda t: transcriptions.append(t),
    )

    pipeline.start()
    pipeline.speak("Очень длинное голосовое сообщение...", blocking=False)
    assert pipeline.state == PipelineState.SPEAKING
    assert tts.is_speaking()

    vad.speech_flags = [True, False]
    cap.chunks_queue.put(np.ones(512, dtype=np.float32) * 0.7)
    cap.chunks_queue.put(np.zeros(512, dtype=np.float32))

    time.sleep(0.2)
    pipeline.stop()

    assert tts.stopped
    assert not tts.is_speaking()
    assert "Остановись" in transcriptions
    assert PipelineState.LISTENING in states_recorded


def test_pipeline_multi_chunk_buffer_accumulation() -> None:
    """Проверка корректной конкатенации нескольких чанков речи в единый массив для STT."""
    cap = MockAudioCapture()
    vad = MockVADDetector()
    stt = MockTranscriber(return_text="Многокадровое аудио")
    tts = MockSynthesizer()

    pipeline = VoicePipeline(
        audio_capture=cap,
        vad_detector=vad,
        transcriber=stt,
        synthesizer=tts,
        silence_duration_ms=100,
        chunk_size=512,
    )

    chunk_a = np.full(512, 0.1, dtype=np.float32)
    chunk_b = np.full(512, 0.2, dtype=np.float32)
    chunk_c = np.full(512, 0.3, dtype=np.float32)
    silence = np.zeros(512, dtype=np.float32)

    vad.speech_flags = [True, True, True, False]
    cap.chunks_queue.put(chunk_a)
    cap.chunks_queue.put(chunk_b)
    cap.chunks_queue.put(chunk_c)
    cap.chunks_queue.put(silence)

    pipeline.start()
    time.sleep(0.25)
    pipeline.stop()

    assert len(stt.transcribed_audios) == 1
    accumulated = stt.transcribed_audios[0]
    assert accumulated.shape == (1536,)
    assert np.allclose(accumulated[:512], chunk_a)
    assert np.allclose(accumulated[512:1024], chunk_b)
    assert np.allclose(accumulated[1024:], chunk_c)


class MockVADWithoutActiveAttr:
    """Мок VAD детектора без свойства is_speech_active для тестирования fallback-счетчика."""

    def __init__(self, flags: list[bool]) -> None:
        self.flags = flags

    def process_chunk(self, chunk: np.ndarray) -> bool:
        if self.flags:
            return self.flags.pop(0)
        return False

    def reset_states(self) -> None:
        pass


def test_pipeline_silence_counter_fallback() -> None:
    """Проверка резервного расчета окончания речи по порогу чанков тишины."""
    cap = MockAudioCapture()
    vad = MockVADWithoutActiveAttr(flags=[True, False, False])
    stt = MockTranscriber(return_text="Тест счетчика тишины")
    tts = MockSynthesizer()

    transcriptions: list[str] = []

    pipeline = VoicePipeline(
        audio_capture=cap,
        vad_detector=vad,  # type: ignore[arg-type]
        transcriber=stt,
        synthesizer=tts,
        silence_duration_ms=64,
        sample_rate=16000,
        chunk_size=512,
        on_transcription=lambda t: transcriptions.append(t),
    )

    cap.chunks_queue.put(np.ones(512, dtype=np.float32) * 0.5)
    cap.chunks_queue.put(np.zeros(512, dtype=np.float32))
    cap.chunks_queue.put(np.zeros(512, dtype=np.float32))

    pipeline.start()
    time.sleep(0.25)
    pipeline.stop()

    assert "Тест счетчика тишины" in transcriptions


@patch("sounddevice.InputStream")
def test_real_voice_components_integration(mock_stream_cls: Any) -> None:
    """Сквозная интеграция реальных классов AudioCapture, VADDetector, Transcriber и Synthesizer."""
    from core.voice.audio_capture import AudioCapture
    from core.voice.synthesizer import PiperSynthesizer
    from core.voice.transcriber import FasterWhisperTranscriber
    from core.voice.vad_detector import VADDetector

    mock_stream = MagicMock()
    mock_stream.active = True
    mock_stream_cls.return_value = mock_stream

    cap = AudioCapture(sample_rate=16000, chunk_size=512)
    vad = VADDetector(
        model_path="non_existent.onnx",
        sample_rate=16000,
        chunk_size=512,
        silence_duration_ms=50,
        energy_threshold=0.01,
    )
    stt = FasterWhisperTranscriber(
        download_root="non_existent_dir",
        local_files_only=True,
        fallback_text="Сквозной тест успешно",
    )
    tts = PiperSynthesizer(use_sapi_fallback=False)

    transcriptions: list[str] = []

    pipeline = VoicePipeline(
        audio_capture=cap,
        vad_detector=vad,
        transcriber=stt,
        synthesizer=tts,
        silence_duration_ms=50,
        on_transcription=lambda t: transcriptions.append(t),
    )

    pipeline.start()

    speech_chunk = np.ones(512, dtype=np.float32) * 0.4
    silence_chunk = np.zeros(512, dtype=np.float32)

    cap._enqueue_chunk(speech_chunk)
    for _ in range(4):
        cap._enqueue_chunk(silence_chunk)

    for _ in range(30):
        if transcriptions:
            break
        time.sleep(0.05)
    pipeline.speak("Подтверждаю", blocking=True)
    assert "Сквозной тест успешно" in transcriptions
    assert pipeline.state == PipelineState.IDLE
    pipeline.stop()


def test_voice_pipeline_mute_and_audio_level() -> None:
    """Проверка переключения mute и передачи RMS уровня громкости в callback."""
    cap = MockAudioCapture()
    vad = MockVADDetector()
    stt = MockTranscriber()
    tts = MockSynthesizer()

    levels: list[float] = []

    pipeline = VoicePipeline(
        audio_capture=cap,
        vad_detector=vad,
        transcriber=stt,
        synthesizer=tts,
        on_audio_level=lambda lvl: levels.append(lvl),
    )

    pipeline.start()

    # Подаем тестовый чанк со значением 0.5 (RMS = 0.5)
    test_chunk = np.ones(512, dtype=np.float32) * 0.5
    cap.chunks_queue.put(test_chunk)

    time.sleep(0.15)
    assert len(levels) > 0
    assert abs(levels[-1] - 0.5) < 0.05

    # Включаем Mute
    pipeline.set_muted(True)
    assert pipeline.is_muted()
    assert getattr(cap, "muted", False) is True

    # При mute отправляется 0.0
    cap.chunks_queue.put(test_chunk)
    time.sleep(0.15)
    assert levels[-1] == 0.0

    # Выключаем Mute
    pipeline.set_muted(False)
    assert not pipeline.is_muted()
    assert getattr(cap, "muted", False) is False

    pipeline.stop()
