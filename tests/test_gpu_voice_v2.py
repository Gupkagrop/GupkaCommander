"""Комплексные тесты Фазы 4 (GPU-First Zero-CPU-Heat Brain & Voice Stack 2.0).

> [!NOTE]
> **Назначение файла:** Покрывает модульными тестами с моками (HTTP, ONNX, sounddevice,
> nvidia-smi, Popen):
> 1. `QwenTTSSynthesizer`: `POST :8081/tts`, декодирование WAV, `speak_stream`, мгновенная
>    остановка (Barge-In), авто-откат на `PiperSynthesizer` при недоступности сервера.
> 2. `GigaAMOnnxTranscriber`: NumPy log-mel (64 bins, 16 кГц), жадный CTC-декодинг,
>    нормализация IT-брендов, авто-откат на `FasterWhisperTranscriber`.
> 3. `LlamaServerManager` + `VRAMBudgetGuard`: запуск `:8080` (`-fa on -ctk q8_0 -ctv q8_0`)
>    и `:8081` (`--tts-lang ru`), ограничение VRAM < 6.5 ГБ (VLM-only + Piper fallback).
> 4. `VoicePipeline`: выбор `gigaam-v3` и `qwen3-tts` через аргументы и `settings.json`.
> 5. `DependencyManager` и `scripts/download_models.py`: спецификации Qwen3.5-4B, Qwen3-TTS,
>    GigaAM v3 ONNX и приоритет CUDA для `llama.cpp`.
"""

from __future__ import annotations

import io
import json
import threading
import time
import wave
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import requests

from core.bootstrap import (
    GIGAAM_MODEL_FILENAME,
    GIGAAM_VOCAB_FILENAME,
    QWEN_TTS_MMPROJ_FILENAME,
    QWEN_TTS_MODEL_FILENAME,
    VLM_MMPROJ_FILENAME,
    VLM_MODEL_FILENAME,
    DependencyManager,
    _select_llama_asset,
)
from core.brain.llama_manager import (
    DEFAULT_MMPROJ_ALT_FILENAME,
    DEFAULT_MMPROJ_FILENAME,
    DEFAULT_MODEL_FILENAME,
    DEFAULT_TTS_MMPROJ_FILENAME,
    DEFAULT_TTS_MODEL_FILENAME,
    LEGACY_V1_MMPROJ_FILENAME,
    LEGACY_V1_MODEL_FILENAME,
    LlamaServerManager,
    VRAMBudgetGuard,
    VRAMStatus,
)
from core.voice.gigaam_transcriber import (
    GIGAAM_N_MELS,
    GigaAMOnnxTranscriber,
    compute_log_mel_spectrogram,
    greedy_ctc_decode,
    normalize_brand_terms,
)
from core.voice.qwen_tts_synthesizer import QwenTTSSynthesizer, _decode_wav_to_float32
from core.voice.voice_pipeline import SynthesizerProtocol, TranscriberProtocol, VoicePipeline
from scripts.download_models import download_gigaam_models, download_qwen_tts_models


def _make_wav_bytes(samples_i16: np.ndarray, sample_rate: int = 24000, channels: int = 1) -> bytes:
    """Создает корректный WAV-буфер в памяти для тестов TTS."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples_i16.astype(np.int16).tobytes())
    return buf.getvalue()


class _FakeOutputStream:
    """Мок sounddevice.OutputStream с поддержкой контекстного менеджера и abort()."""

    def __init__(self, written_blocks: list[np.ndarray], abort_event: threading.Event) -> None:
        self._written = written_blocks
        self._abort_event = abort_event
        self.aborted = False

    def __enter__(self) -> _FakeOutputStream:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def write(self, block: np.ndarray) -> None:
        if self._abort_event.is_set():
            raise RuntimeError("aborted")
        self._written.append(block.copy())
        time.sleep(0.01)

    def abort(self) -> None:
        self.aborted = True
        self._abort_event.set()


class TestQwenTTSSynthesizer:
    """Тесты GPU-синтезатора речи QwenTTSSynthesizer (:8081/tts)."""

    def test_protocol_and_wav_playback_with_speaker_ref(self, tmp_path: Path) -> None:
        """Проверка POST /tts, передачи speaker_ref_b64 и воспроизведения WAV."""
        pcm = np.linspace(-16000, 16000, 2500, dtype=np.int16)
        wav_bytes = _make_wav_bytes(pcm, sample_rate=24000)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = wav_bytes
        mock_resp.raise_for_status.return_value = None

        mock_session = MagicMock(spec=requests.Session)
        mock_session.post.return_value = mock_resp

        ref_file = tmp_path / "ref.wav"
        ref_file.write_bytes(b"RIFF_REF_AUDIO")

        written_blocks: list[np.ndarray] = []
        abort_event = threading.Event()
        stream_kwargs_seen: list[dict[str, Any]] = []

        def _stream_factory(**kwargs: Any) -> _FakeOutputStream:
            stream_kwargs_seen.append(kwargs)
            return _FakeOutputStream(written_blocks, abort_event)

        synth = QwenTTSSynthesizer(
            base_url="http://127.0.0.1:8081",
            lang="ru",
            speaker_ref_path=ref_file,
            speaker_ref_text="Эталонная фраза",
            output_stream_factory=_stream_factory,
            session=mock_session,
        )
        assert isinstance(synth, SynthesizerProtocol)

        synth.speak("Привет, Денис!", blocking=True)

        assert synth.backend == QwenTTSSynthesizer.BACKEND_QWEN_TTS
        assert synth.is_speaking() is False
        mock_session.post.assert_called_once()
        call_args = mock_session.post.call_args
        assert call_args.args[0] == "http://127.0.0.1:8081/tts"
        payload = call_args.kwargs["json"]
        assert payload["input"] == "Привет, Денис!"
        assert payload["lang"] == "ru"
        assert "speaker_ref_b64" in payload
        assert payload["speaker_ref_text"] == "Эталонная фраза"

        assert stream_kwargs_seen[0]["samplerate"] == 24000
        assert stream_kwargs_seen[0]["channels"] == 1
        total_samples = sum(len(b) for b in written_blocks)
        assert total_samples == 2500

    def test_session_isolation_and_barge_in_stop(self) -> None:
        """Проверка мгновенного прерывания (Barge-In) через stop() и abort() потока."""
        pcm = np.zeros(1024 * 20, dtype=np.int16)
        wav_bytes = _make_wav_bytes(pcm, sample_rate=24000)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = wav_bytes
        mock_resp.raise_for_status.return_value = None

        mock_session = MagicMock(spec=requests.Session)
        mock_session.post.return_value = mock_resp

        written_blocks: list[np.ndarray] = []
        abort_event = threading.Event()
        streams: list[_FakeOutputStream] = []

        def _stream_factory(**_kwargs: Any) -> _FakeOutputStream:
            st = _FakeOutputStream(written_blocks, abort_event)
            streams.append(st)
            return st

        synth = QwenTTSSynthesizer(
            output_stream_factory=_stream_factory,
            session=mock_session,
        )
        synth.speak("Длинная фраза для проверки мгновенной остановки", blocking=False)

        deadline = time.monotonic() + 2.0
        while not streams and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(streams) == 1
        assert synth.is_speaking() is True

        synth.stop()
        assert synth.is_speaking() is False
        assert streams[0].aborted is True
        assert sum(len(b) for b in written_blocks) < len(pcm)

    def test_speak_stream_and_piper_fallback_when_offline(self) -> None:
        """Проверка speak_stream и автоматического отката на Piper при офлайне :8081."""
        mock_session = MagicMock(spec=requests.Session)
        mock_session.post.side_effect = requests.ConnectionError("Connection refused :8081")

        mock_fallback = MagicMock(spec=SynthesizerProtocol)
        mock_fallback.is_speaking.return_value = False

        synth = QwenTTSSynthesizer(
            fallback_synthesizer=mock_fallback,
            session=mock_session,
        )
        synth.speak_stream(iter(["Первое предложение.", "Второе предложение."]), blocking=True)

        assert synth.backend == QwenTTSSynthesizer.BACKEND_FALLBACK
        assert mock_fallback.speak.call_count == 2
        mock_fallback.speak.assert_any_call("Первое предложение.", blocking=True)
        mock_fallback.speak.assert_any_call("Второе предложение.", blocking=True)
        assert synth.is_speaking() is False

    def test_decode_wav_rejects_corrupt_buffer(self) -> None:
        """Проверка валидации поврежденного WAV-ответа."""
        with pytest.raises(ValueError):
            _decode_wav_to_float32(b"not-a-wav")

    def test_timeout_8s_and_circuit_breaker_cooldown_with_recovery(self) -> None:
        """Таймаут 8с и circuit breaker (45с) мгновенно переводят фразы в Piper без зависаний."""
        pcm = np.linspace(-10000, 10000, 800, dtype=np.int16)
        wav_bytes = _make_wav_bytes(pcm, sample_rate=24000)

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.content = wav_bytes
        ok_resp.raise_for_status.return_value = None

        err_resp = MagicMock()
        err_resp.status_code = 503
        err_resp.raise_for_status.side_effect = requests.HTTPError("503 Service Unavailable")

        mock_session = MagicMock(spec=requests.Session)
        mock_session.post.side_effect = [err_resp, ok_resp]
        mock_fallback = MagicMock(spec=SynthesizerProtocol)
        mock_fallback.is_speaking.return_value = False

        synth = QwenTTSSynthesizer(
            fallback_synthesizer=mock_fallback,
            session=mock_session,
        )
        assert synth._timeout_seconds == pytest.approx(8.0)
        assert synth._cooldown_seconds == pytest.approx(45.0)

        # 1-я фраза падает с 503 -> взводит cooldown -> следующие фразы не ходят в :8081
        synth.speak_stream(
            iter(["Первая фраза.", "Вторая фраза.", "Третья фраза."]),
            blocking=True,
        )
        assert synth.is_in_cooldown is True
        assert mock_session.post.call_count == 1
        assert mock_fallback.speak.call_count == 3

        # При cooldown_seconds=0.0 следующее предложение снова пробует /tts и восстанавливается
        written_blocks: list[np.ndarray] = []
        abort_event = threading.Event()
        mock_session_rec = MagicMock(spec=requests.Session)
        mock_session_rec.post.side_effect = [err_resp, ok_resp]
        mock_fallback_rec = MagicMock(spec=SynthesizerProtocol)
        mock_fallback_rec.is_speaking.return_value = False

        synth_rec = QwenTTSSynthesizer(
            cooldown_seconds=0.0,
            fallback_synthesizer=mock_fallback_rec,
            output_stream_factory=lambda **_kw: _FakeOutputStream(written_blocks, abort_event),
            session=mock_session_rec,
        )
        synth_rec.speak_stream(iter(["Сбойная фраза.", "Восстановленная фраза."]), blocking=True)
        assert mock_session_rec.post.call_count == 2
        assert mock_fallback_rec.speak.call_count == 1
        assert synth_rec.backend == QwenTTSSynthesizer.BACKEND_QWEN_TTS


class TestGigaAMOnnxTranscriber:
    """Тесты распознавания речи GigaAM v3 ONNX и нормализации терминов."""

    def test_log_mel_shape_and_greedy_ctc_decode(self) -> None:
        """Проверка расчета 64-bin log-mel спектрограммы и CTC-декодирования."""
        audio = np.sin(2 * np.pi * 440.0 * np.arange(16000) / 16000.0).astype(np.float32)
        mel = compute_log_mel_spectrogram(audio)
        assert mel.shape[0] == GIGAAM_N_MELS
        assert mel.shape[1] > 90
        assert mel.dtype == np.float32

        vocab = ["<unk>", "▁при", "вет", "▁ю", "туб", "<blk>"]
        blank_id = 5
        # Симулируем CTC-логиты формы (1, T=8, V=6) с повторами и blank-токенами
        frame_ids = [1, 1, 5, 2, 5, 3, 4, 4]
        logits = np.full((1, len(frame_ids), len(vocab)), -10.0, dtype=np.float32)
        for t, tid in enumerate(frame_ids):
            logits[0, t, tid] = 10.0

        decoded = greedy_ctc_decode(logits, vocab=vocab, blank_id=blank_id)
        assert decoded == "привет ютуб"

    def test_brand_normalization(self) -> None:
        """Проверка нормализации русских транслитераций IT-брендов."""
        assert normalize_brand_terms("открой ютуб в хром") == "открой YouTube в Chrome"
        assert normalize_brand_terms("запусти антигравити и гитхаб") == (
            "запусти Antigravity и GitHub"
        )
        # Похожие по корню обычные слова не портятся
        assert normalize_brand_terms("хромосома ютубер") == "хромосома ютубер"

    def test_onnx_inference_and_brand_normalizer(self, tmp_path: Path) -> None:
        """Проверка полного вызова GigaAMOnnxTranscriber с моком InferenceSession."""
        model_path = tmp_path / GIGAAM_MODEL_FILENAME
        vocab_path = tmp_path / GIGAAM_VOCAB_FILENAME
        model_path.write_bytes(b"ONNX")
        vocab_path.write_text("<unk>\n▁от\nкрой\n▁хром\n<blk>\n", encoding="utf-8")

        fake_input0 = MagicMock()
        fake_input0.name = "features"
        fake_input0.shape = ["batch", 64, "time"]
        fake_input1 = MagicMock()
        fake_input1.name = "feature_lengths"

        logits = np.full((1, 5, 5), -5.0, dtype=np.float32)
        for t, tid in enumerate([1, 2, 4, 3, 4]):
            logits[0, t, tid] = 8.0

        fake_session = MagicMock()
        fake_session.get_inputs.return_value = [fake_input0, fake_input1]
        fake_session.run.return_value = [logits]

        transcriber = GigaAMOnnxTranscriber(
            model_path=model_path,
            vocab_path=vocab_path,
            session_factory=lambda _path, providers: fake_session,
        )
        assert isinstance(transcriber, TranscriberProtocol)

        audio = np.ones(3200, dtype=np.float32) * 0.1
        text = transcriber.transcribe(audio, language="ru")
        assert text == "открой Chrome"
        assert transcriber.is_initialized is True
        assert transcriber.is_fallback is False

        run_inputs = fake_session.run.call_args.args[1]
        assert run_inputs["features"].shape[1] == 64
        assert run_inputs["feature_lengths"].dtype == np.int64

    def test_missing_files_fallback_to_whisper(self, tmp_path: Path) -> None:
        """Проверка автоматического отката на FasterWhisperTranscriber при отсутствии ONNX."""
        mock_whisper = MagicMock(spec=TranscriberProtocol)
        mock_whisper.transcribe.return_value = "открой гитхаб"

        transcriber = GigaAMOnnxTranscriber(
            model_path=tmp_path / "missing.onnx",
            vocab_path=tmp_path / "missing_vocab.txt",
            fallback_transcriber=mock_whisper,
        )
        text = transcriber.transcribe(np.ones(1600, dtype=np.float32))
        assert transcriber.is_fallback is True
        assert text == "открой GitHub"
        mock_whisper.transcribe.assert_called_once()


class TestLlamaServerManagerDualGPU:
    """Тесты двухсервисного LlamaServerManager (:8080 VLM + :8081 TTS) и VRAMBudgetGuard."""

    def test_vram_budget_guard_nvidia_smi_parsing_and_thresholds(self) -> None:
        """Проверка разбора nvidia-smi и порога < 6.5 ГБ VRAM."""
        ok_proc = MagicMock(returncode=0, stdout="8192, 6800\n")
        with patch("core.brain.llama_manager.subprocess.run", return_value=ok_proc):
            guard = VRAMBudgetGuard(min_total_gb=6.5, min_free_gb=1.5)
            status = guard.query_vram()
            assert status.gpu_available is True
            assert status.total_gb == pytest.approx(8.0)
            assert guard.can_run_tts() is True

        low_proc = MagicMock(returncode=0, stdout="4096, 3500\n")
        with patch("core.brain.llama_manager.subprocess.run", return_value=low_proc):
            guard_low = VRAMBudgetGuard(min_total_gb=6.5, min_free_gb=1.5)
            assert guard_low.can_run_tts() is False

        with patch("core.brain.llama_manager.subprocess.run", side_effect=TimeoutError("timeout")):
            guard_err = VRAMBudgetGuard(min_total_gb=6.5)
            assert guard_err.can_run_tts() is False

        assert VRAMBudgetGuard().min_free_gb == pytest.approx(2.2)

    def test_start_waits_for_vlm_health_before_checking_vram_for_tts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """start() ждёт готовности VLM (/health == 200) перед проверкой VRAM и стартом TTS."""
        monkeypatch.setattr("core.bootstrap.shutil.which", lambda _name: None)
        monkeypatch.setattr("core.bootstrap.get_bundle_root", lambda: tmp_path / "bundle")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "llama-server.exe").write_bytes(b"MZ")
        llm_dir = tmp_path / "models" / "llm"
        llm_dir.mkdir(parents=True)
        (llm_dir / DEFAULT_MODEL_FILENAME).write_bytes(b"vlm")
        (llm_dir / DEFAULT_MMPROJ_FILENAME).write_bytes(b"mmproj")
        tts_dir = tmp_path / "models" / "tts"
        tts_dir.mkdir(parents=True)
        (tts_dir / DEFAULT_TTS_MODEL_FILENAME).write_bytes(b"tts")
        (tts_dir / DEFAULT_TTS_MMPROJ_FILENAME).write_bytes(b"tts-mmproj")

        settings_file = tmp_path / "settings.json"
        settings_file.write_text(
            json.dumps({"models": {"tts": {"engine": "qwen3-tts"}, "llm": {"autostart": True}}}),
            encoding="utf-8",
        )

        # Пока VLM не готов, VRAM ещё 7 ГБ, но после загрузки VLM остаётся 1.9 ГБ (< 2.2 ГБ)
        vlm_ready = False
        probe_calls: list[bool] = []
        health_checks = 0

        def _health() -> bool:
            nonlocal health_checks, vlm_ready
            health_checks += 1
            if health_checks >= 4:
                vlm_ready = True
            return vlm_ready

        def _vram_probe() -> VRAMStatus:
            probe_calls.append(vlm_ready)
            free_mb = 1950.0 if vlm_ready else 7000.0
            return VRAMStatus(gpu_available=True, total_mb=8192.0, free_mb=free_mb)

        def _fake_popen(*_args: Any, **_kwargs: Any) -> MagicMock:
            p = MagicMock()
            p.poll.return_value = None
            return p

        mock_popen = MagicMock(side_effect=_fake_popen)
        with (
            patch("core.brain.llama_manager.MIN_VLM_BYTES", 1),
            patch("core.brain.llama_manager.MIN_TTS_BYTES", 1),
        ):
            mgr = LlamaServerManager(
                models_dir=tmp_path / "models",
                bin_dir=bin_dir,
                settings_path=settings_file,
                popen_factory=mock_popen,
                health_checker=_health,
                tts_health_checker=lambda: False,
                vram_guard=VRAMBudgetGuard(vram_probe=_vram_probe),
                vlm_ready_timeout=2.0,
                vlm_poll_interval=0.01,
            )
            assert mgr.start() is True
            assert vlm_ready is True
            assert probe_calls == [True]
            assert mock_popen.call_count == 1
            assert mgr.is_tts_running_managed() is False

            # Если health_checker=None с моковым popen_factory, ожидание пропускается без сна
            mock_popen_fast = MagicMock(side_effect=_fake_popen)
            mgr_fast = LlamaServerManager(
                models_dir=tmp_path / "models",
                bin_dir=bin_dir,
                settings_path=settings_file,
                popen_factory=mock_popen_fast,
                health_checker=None,
                tts_health_checker=lambda: False,
                vram_guard=VRAMBudgetGuard(
                    vram_probe=lambda: VRAMStatus(gpu_available=True, total_mb=8192, free_mb=3200)
                ),
            )
            with (
                patch.object(mgr_fast, "is_server_healthy", return_value=False),
                patch("core.brain.llama_manager.time.sleep") as mock_sleep,
            ):
                assert mgr_fast.start() is True
                mock_sleep.assert_not_called()
                assert mock_popen_fast.call_count == 2

    def test_dual_service_launch_8gb_vram_vs_low_vram(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Проверка запуска VLM+TTS на 8 ГБ VRAM и VLM-only на < 6.5 ГБ VRAM."""
        monkeypatch.setattr("core.bootstrap.shutil.which", lambda _name: None)
        monkeypatch.setattr("core.bootstrap.get_bundle_root", lambda: tmp_path / "bundle")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "llama-server.exe").write_bytes(b"MZ")

        llm_dir = tmp_path / "models" / "llm"
        llm_dir.mkdir(parents=True)
        (llm_dir / DEFAULT_MODEL_FILENAME).write_bytes(b"vlm")
        (llm_dir / DEFAULT_MMPROJ_FILENAME).write_bytes(b"mmproj")

        tts_dir = tmp_path / "models" / "tts"
        tts_dir.mkdir(parents=True)
        (tts_dir / DEFAULT_TTS_MODEL_FILENAME).write_bytes(b"tts")
        (tts_dir / DEFAULT_TTS_MMPROJ_FILENAME).write_bytes(b"tts-mmproj")

        settings_file = tmp_path / "settings.json"
        settings_file.write_text(
            json.dumps(
                {
                    "models": {
                        "tts": {"engine": "qwen3-tts", "lang": "ru", "port": 8081},
                        "llm": {"autostart": True, "n_gpu_layers": 99, "context_window": 8192},
                    }
                }
            ),
            encoding="utf-8",
        )

        # 1. На 8 ГБ VRAM запускаются оба сервиса (:8080 и :8081)
        procs: list[MagicMock] = []

        def _fake_popen(*_args: Any, **_kwargs: Any) -> MagicMock:
            p = MagicMock()
            p.poll.return_value = None
            procs.append(p)
            return p

        mock_popen = MagicMock(side_effect=_fake_popen)
        guard_8gb = VRAMBudgetGuard(
            vram_probe=lambda: VRAMStatus(gpu_available=True, total_mb=8192, free_mb=7000)
        )

        with (
            patch("core.brain.llama_manager.MIN_VLM_BYTES", 1),
            patch("core.brain.llama_manager.MIN_TTS_BYTES", 1),
        ):
            mgr = LlamaServerManager(
                models_dir=tmp_path / "models",
                bin_dir=bin_dir,
                settings_path=settings_file,
                popen_factory=mock_popen,
                health_checker=lambda: False,
                tts_health_checker=lambda: False,
                vram_guard=guard_8gb,
            )
            assert mgr.start() is True
            assert mock_popen.call_count == 2
            assert mgr.is_running_managed() is True
            assert mgr.is_tts_running_managed() is True
            assert mgr.should_use_qwen_tts() is True

            vlm_cmd = mock_popen.call_args_list[0].args[0]
            assert "-fa" in vlm_cmd and "on" in vlm_cmd
            assert "-ctk" in vlm_cmd and "q8_0" in vlm_cmd
            assert "-ctv" in vlm_cmd
            assert "--image-min-tokens" in vlm_cmd
            assert "--jinja" in vlm_cmd
            assert "8080" in vlm_cmd

            tts_cmd = mock_popen.call_args_list[1].args[0]
            assert "8081" in tts_cmd
            assert "--tts-lang" in tts_cmd and "ru" in tts_cmd

            mgr.stop()
            assert all(p.terminate.called for p in procs)

        # 2. При < 6.5 ГБ VRAM запускается ТОЛЬКО VLM (:8080), TTS -> Piper
        mock_popen_low = MagicMock(side_effect=_fake_popen)
        guard_4gb = VRAMBudgetGuard(
            vram_probe=lambda: VRAMStatus(gpu_available=True, total_mb=4096, free_mb=3500)
        )
        with (
            patch("core.brain.llama_manager.MIN_VLM_BYTES", 1),
            patch("core.brain.llama_manager.MIN_TTS_BYTES", 1),
        ):
            mgr_low = LlamaServerManager(
                models_dir=tmp_path / "models",
                bin_dir=bin_dir,
                settings_path=settings_file,
                popen_factory=mock_popen_low,
                health_checker=lambda: False,
                tts_health_checker=lambda: False,
                vram_guard=guard_4gb,
            )
            assert mgr_low.start() is True
            assert mock_popen_low.call_count == 1
            assert mgr_low.is_running_managed() is True
            assert mgr_low.is_tts_running_managed() is False

    def test_find_model_paths_v2_priority_and_legacy_v1_fallback(self, tmp_path: Path) -> None:
        """Проверяет приоритет v2 Qwen3.5-4B и откат на legacy v1 в find_model_paths."""
        llm_dir = tmp_path / "models" / "llm"
        llm_dir.mkdir(parents=True)

        v1_model = llm_dir / LEGACY_V1_MODEL_FILENAME
        v1_mmproj = llm_dir / LEGACY_V1_MMPROJ_FILENAME
        v1_model.write_bytes(b"v1-model")
        v1_mmproj.write_bytes(b"v1-mmproj")

        mgr = LlamaServerManager(models_dir=tmp_path / "models")
        with patch("core.brain.llama_manager.MIN_VLM_BYTES", 1):
            found_m, found_p = mgr.find_model_paths()
            assert found_m == v1_model
            assert found_p == v1_mmproj

            v2_model = llm_dir / DEFAULT_MODEL_FILENAME
            v2_alt_mmproj = llm_dir / DEFAULT_MMPROJ_ALT_FILENAME
            v2_model.write_bytes(b"v2-model")
            v2_alt_mmproj.write_bytes(b"v2-mmproj")

            found_m2, found_p2 = mgr.find_model_paths()
            assert found_m2 == v2_model
            assert found_p2 == v2_alt_mmproj


class TestVoicePipelineAndBootstrapV2:
    """Тесты выбора новых движков в VoicePipeline и спецификаций bootstrap/download."""

    def test_voice_pipeline_engine_selection_and_from_settings(self, tmp_path: Path) -> None:
        """Проверка выбора GigaAMOnnxTranscriber и QwenTTSSynthesizer через VoicePipeline."""
        from core.voice.gigaam_transcriber import GigaAMOnnxTranscriber
        from core.voice.qwen_tts_synthesizer import QwenTTSSynthesizer

        pipeline = VoicePipeline(stt_engine="gigaam-v3", tts_engine="qwen3-tts")
        assert isinstance(pipeline.transcriber, GigaAMOnnxTranscriber)
        assert isinstance(pipeline.synthesizer, QwenTTSSynthesizer)

        settings_file = tmp_path / "settings.json"
        settings_file.write_text(
            json.dumps(
                {
                    "models": {
                        "stt": {"engine": "gigaam-v3"},
                        "tts": {"engine": "qwen3-tts", "port": 8091},
                    }
                }
            ),
            encoding="utf-8",
        )
        pipeline_cfg = VoicePipeline.from_settings(settings_path=settings_file)
        assert isinstance(pipeline_cfg.transcriber, GigaAMOnnxTranscriber)
        assert isinstance(pipeline_cfg.synthesizer, QwenTTSSynthesizer)
        assert pipeline_cfg.synthesizer.base_url == "http://127.0.0.1:8091"

    def test_bootstrap_specs_cuda_priority_and_download_helpers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Проверка новых FileSpec, приоритета CUDA llama.cpp и скрипта скачивания."""
        monkeypatch.setattr("core.bootstrap.shutil.which", lambda _name: None)
        monkeypatch.setattr("core.bootstrap.get_bundle_root", lambda: tmp_path / "bundle")

        mgr = DependencyManager(
            models_dir=tmp_path / "models",
            bin_dir=tmp_path / "bin",
            state_file=tmp_path / "state.json",
        )
        vlm_specs = mgr._get_file_specs("vlm_qwen")
        assert [s.relative_path for s in vlm_specs] == [
            f"llm/{VLM_MODEL_FILENAME}",
            f"llm/{VLM_MMPROJ_FILENAME}",
        ]
        tts_specs = mgr._get_file_specs("qwen3_tts")
        assert [s.relative_path for s in tts_specs] == [
            f"tts/{QWEN_TTS_MODEL_FILENAME}",
            f"tts/{QWEN_TTS_MMPROJ_FILENAME}",
        ]
        gigaam_specs = mgr._get_file_specs("gigaam_v3")
        assert [s.relative_path for s in gigaam_specs] == [
            f"gigaam/{GIGAAM_MODEL_FILENAME}",
            f"gigaam/{GIGAAM_VOCAB_FILENAME}",
        ]

        releases = [
            {
                "tag_name": "b9999",
                "assets": [
                    {
                        "name": "llama-b9999-bin-win-vulkan-x64.zip",
                        "browser_download_url": "https://x/vulkan.zip",
                    },
                    {
                        "name": "llama-b9999-bin-win-cuda-12.4-x64.zip",
                        "browser_download_url": "https://x/cuda.zip",
                    },
                ],
            }
        ]
        assert _select_llama_asset(releases) == (
            "llama-b9999-bin-win-cuda-12.4-x64.zip",
            "https://x/cuda.zip",
        )

        with patch("scripts.download_models.download_file", return_value=True) as mock_dl:
            tts_paths = download_qwen_tts_models(tmp_path)
            gigaam_paths = download_gigaam_models(tmp_path)
            assert len(tts_paths) == 2
            assert len(gigaam_paths) == 2
            assert mock_dl.call_count == 4

    def test_voice_pipeline_reload_engines_from_settings(self, tmp_path: Path) -> None:
        """reload_engines_from_settings останавливает старый TTS и сбрасывает ленивые движки."""
        from core.voice.gigaam_transcriber import GigaAMOnnxTranscriber
        from core.voice.qwen_tts_synthesizer import QwenTTSSynthesizer
        from core.voice.synthesizer import PiperSynthesizer
        from core.voice.transcriber import FasterWhisperTranscriber

        settings_file = tmp_path / "settings.json"
        settings_file.write_text(
            json.dumps(
                {
                    "models": {
                        "stt": {"engine": "faster-whisper"},
                        "tts": {"engine": "piper", "port": 8081},
                    }
                }
            ),
            encoding="utf-8",
        )
        pipeline = VoicePipeline.from_settings(settings_path=settings_file)
        assert isinstance(pipeline.transcriber, FasterWhisperTranscriber)
        old_synth = pipeline.synthesizer
        assert isinstance(old_synth, PiperSynthesizer)

        settings_file.write_text(
            json.dumps(
                {
                    "models": {
                        "stt": {"engine": "gigaam-v3"},
                        "tts": {"engine": "qwen3-tts", "port": 8092},
                    }
                }
            ),
            encoding="utf-8",
        )
        with patch.object(old_synth, "stop", wraps=old_synth.stop) as spy_stop:
            pipeline.reload_engines_from_settings()
            spy_stop.assert_called_once()

        assert isinstance(pipeline.transcriber, GigaAMOnnxTranscriber)
        assert isinstance(pipeline.synthesizer, QwenTTSSynthesizer)
        assert pipeline.synthesizer.base_url == "http://127.0.0.1:8092"
