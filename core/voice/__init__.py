"""
> [!NOTE]
> Пакет core.voice: Подсистема голосового взаимодействия GupkaCommander.
> Экспортирует модули захвата звука (AudioCapture), детекции речи (VADDetector),
> распознавания (FasterWhisperTranscriber), синтеза (PiperSynthesizer)
> и сквозной голосовой конвейер (VoicePipeline, PipelineState).
"""

from core.voice.audio_capture import AudioCapture
from core.voice.synthesizer import PiperSynthesizer
from core.voice.transcriber import FasterWhisperTranscriber
from core.voice.vad_detector import VADDetector
from core.voice.voice_pipeline import PipelineState, VoicePipeline

__all__ = [
    "AudioCapture",
    "FasterWhisperTranscriber",
    "PiperSynthesizer",
    "PipelineState",
    "VADDetector",
    "VoicePipeline",
]
