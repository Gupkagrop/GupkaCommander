"""
> [!NOTE]
> Модуль тестов tests.test_live_fixes: Регрессионные тесты по итогам живого
> тестирования GupkaCommander.exe (эхо TTS, YouTube AI fallback, AppFinder,
> флаги llama-server, файловое логирование и новый интерфейс SpotlightBar).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import MagicMock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

import main as app_main
from core.app_finder import AppFinder, AppInfo
from core.brain.llama_manager import LlamaServerManager
from core.brain.llm_client import LocalLLMClient
from core.memory import MemoryManager
from core.routing.intent_router import Intent, IntentRouter, RouteResult
from core.voice.voice_pipeline import PipelineState, VoicePipeline
from gui.spotlight_bar import SpotlightBar
from gui.styles import get_status_label_ru


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Фикстура синглтона QApplication для headless-тестов GUI."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(sys.argv)
    return app


class _DummyCapture:
    """Управляемый mock захвата аудио для детерминированного прогона VoicePipeline."""

    def __init__(self, chunks: list[np.ndarray]) -> None:
        self._chunks = list(chunks)
        self._idx = 0
        self.all_read = threading.Event()
        self.is_muted = False

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def read_chunk(self, timeout: float | None = None) -> np.ndarray | None:
        if self._idx < len(self._chunks):
            chunk = self._chunks[self._idx]
            self._idx += 1
            if self._idx >= len(self._chunks):
                self.all_read.set()
            return chunk
        time.sleep(0.01)
        return None

    def clear(self) -> None:
        self._chunks.clear()

    def is_active(self) -> bool:
        return True

    def mute(self) -> None:
        self.is_muted = True

    def unmute(self) -> None:
        self.is_muted = False


def test_voice_pipeline_echo_guard_suppresses_tts_echo() -> None:
    """При echo_guard=True во время речи TTS аудио-чанки игнорируются и stop() не вызывается."""
    vad = MagicMock()
    vad.process_chunk.return_value = True
    vad.is_speech_active = True

    synth = MagicMock()
    synth.is_speaking.return_value = True

    capture = _DummyCapture([np.ones(512, dtype=np.float32) * 0.2 for _ in range(4)])
    transcriber = MagicMock()
    levels: list[float] = []

    pipeline = VoicePipeline(
        audio_capture=capture,
        vad_detector=vad,
        transcriber=transcriber,
        synthesizer=synth,
        on_audio_level=levels.append,
        echo_guard=True,
        echo_tail_seconds=0.35,
    )
    pipeline.start()
    assert capture.all_read.wait(timeout=1.5)
    time.sleep(0.03)

    vad.process_chunk.assert_not_called()
    synth.stop.assert_not_called()
    assert all(level == 0.0 for level in levels)

    pipeline.stop()


def test_voice_pipeline_echo_tail_cooldown() -> None:
    """После завершения синтеза хвост echo_tail_seconds ещё подавляет вход перед открытием VAD."""
    synth = MagicMock()
    synth.is_speaking.return_value = True

    pipeline = VoicePipeline(
        audio_capture=_DummyCapture([]),
        vad_detector=MagicMock(),
        transcriber=MagicMock(),
        synthesizer=synth,
        echo_guard=True,
        echo_tail_seconds=0.15,
    )
    assert pipeline._is_echo_suppressed() is True

    synth.is_speaking.return_value = False
    assert pipeline._is_echo_suppressed() is True

    time.sleep(0.18)
    assert pipeline._is_echo_suppressed() is False


def test_voice_pipeline_barge_in_preserved_without_echo_guard() -> None:
    """При echo_guard=False (по умолчанию) Barge-In продолжает прерывать синтезатор."""
    vad = MagicMock()
    vad.process_chunk.return_value = True
    vad.is_speech_active = True

    synth = MagicMock()
    synth.is_speaking.return_value = True

    capture = _DummyCapture([np.ones(512, dtype=np.float32) * 0.2])
    pipeline = VoicePipeline(
        audio_capture=capture,
        vad_detector=vad,
        transcriber=MagicMock(),
        synthesizer=synth,
        echo_guard=False,
    )
    pipeline.start()
    assert capture.all_read.wait(timeout=1.5)
    time.sleep(0.03)
    pipeline.stop()

    vad.process_chunk.assert_called()
    synth.stop.assert_called()


def test_intent_router_youtube_notes_and_notepad() -> None:
    """Проверяет маршрутизацию YouTube-запросов, заметок и точного запуска блокнота."""
    router = IntentRouter()

    yt_route = router.route("Открой какое нибудь прикольное видео на ютуб на свой вкус")
    assert yt_route.intent == Intent.AI_COGNITIVE

    notes_route = router.route("Открой заметки")
    assert notes_route.intent == Intent.MEMORY
    assert notes_route.params.get("action") == "list_notes"

    notepad_route = router.route("Открой блокнот")
    assert notepad_route.intent == Intent.OPEN_APP
    assert notepad_route.target == "notepad"


def test_app_finder_notepad_prefers_builtin_over_git_release_notes(tmp_path: Path) -> None:
    """Запрос 'блокнот'/'notepad' находит notepad.exe, а не похожий ярлык Git Release Notes."""
    commands_file = tmp_path / "commands.json"
    commands_file.write_text('{"app_aliases": {"блокнот": "notepad"}}', encoding="utf-8")

    finder = AppFinder(cache_path=tmp_path / "cache.json", commands_path=commands_file)
    finder._apps = [
        AppInfo(
            name="Git Release Notes",
            path="C:/Program Files/Git/ReleaseNotes.html",
            source="start_menu",
        ),
        AppInfo(
            name="Google Chrome",
            path="C:/Program Files/Google/Chrome/chrome.exe",
            source="start_menu",
        ),
    ]

    by_ru = finder.find("блокнот")
    assert len(by_ru) == 1
    assert by_ru[0].source == "builtin"
    assert Path(by_ru[0].path).name.lower() == "notepad.exe"

    by_en = finder.find("notepad")
    assert len(by_en) == 1
    assert by_en[0].source == "builtin"
    assert Path(by_en[0].path).name.lower() == "notepad.exe"


def test_llama_manager_flags_and_llm_client_timeout() -> None:
    """Проверяет флаги --image-min-tokens 1024, --jinja и таймаут LocalLLMClient 30 сек."""
    manager = LlamaServerManager()
    cmd = manager.build_command(
        binary=Path("llama-server.exe"),
        model=Path("qwen3-vl.gguf"),
        mmproj=Path("mmproj.gguf"),
        n_gpu_layers=-1,
        ctx_size=4096,
    )
    assert "--jinja" in cmd
    idx = cmd.index("--image-min-tokens")
    assert cmd[idx + 1] == "1024"

    client = LocalLLMClient()
    assert client.timeout_seconds == 30.0


def test_setup_logging_with_none_stderr_and_no_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """setup_logging пишет в gupka.log при sys.stderr=None и не дублирует RotatingFileHandler."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    for h in list(root.handlers):
        root.removeHandler(h)

    monkeypatch.setattr(app_main, "get_logs_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "stderr", None)

    try:
        log_path = app_main.setup_logging()
        second_path = app_main.setup_logging()

        assert log_path == tmp_path / "gupka.log"
        assert second_path == log_path

        file_handlers = [
            h
            for h in root.handlers
            if isinstance(h, RotatingFileHandler) and Path(h.baseFilename) == log_path
        ]
        assert len(file_handlers) == 1

        test_logger = logging.getLogger("gupka.test_live_fixes")
        test_logger.info("Тестовая запись в gupka.log")
        for h in file_handlers:
            h.flush()

        content = log_path.read_text(encoding="utf-8")
        assert "Тестовая запись в gupka.log" in content
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
            if isinstance(h, RotatingFileHandler):
                h.close()
        for h in saved_handlers:
            root.addHandler(h)
        root.setLevel(saved_level)


def test_handle_open_app_falls_back_to_ai_cognitive_when_llm_healthy(
    qapp: QApplication, tmp_path: Path
) -> None:
    """Если приложение не найдено, _handle_open_app делегирует запрос здоровому LLM."""
    mock_finder = MagicMock()
    mock_finder.find.return_value = []

    mock_llm = MagicMock()
    mock_llm.is_healthy.return_value = True

    mock_bar = MagicMock()
    mock_tray = MagicMock()
    mock_tray.is_muted.return_value = False

    commander = app_main.GupkaCommanderApp(
        app=qapp,
        app_finder=mock_finder,
        llm_client=mock_llm,
        memory_manager=MemoryManager(db_path=tmp_path / "memory.db"),
        spotlight_bar=mock_bar,
        tray_manager=mock_tray,
        enable_voice=False,
        enable_hotkey=False,
        enable_tray=False,
        enable_llm_autostart=False,
        async_execution=False,
    )
    commander._handle_ai_cognitive = MagicMock(return_value="Открываю подборку на YouTube.")  # type: ignore[method-assign]

    route = RouteResult(
        intent=Intent.OPEN_APP,
        query="Открой видео на свой вкус",
        target="видео на свой вкус",
        params={"app": "видео на свой вкус"},
        matched_rule="open_app_pattern",
    )
    res = commander._handle_open_app(route, raw_text="Открой видео на свой вкус")
    assert res == "Открываю подборку на YouTube."
    commander._handle_ai_cognitive.assert_called_once_with("Открой видео на свой вкус")
    commander.shutdown()


def test_spotlight_bar_redesign_signals_labels_and_drag(qapp: QApplication) -> None:
    """Проверяет русские бейджи, сигналы микрофона/настроек и перетаскивание SpotlightBar."""
    bar = SpotlightBar()
    try:
        bar.set_pipeline_state("LISTENING")
        assert bar._status_badge.text() == get_status_label_ru("LISTENING")
        assert bar._status_badge.text() == "Слушаю"

        bar.set_pipeline_state(PipelineState.IDLE)
        assert bar._status_badge.text() == "Готов"

        mute_events: list[bool] = []
        settings_events: list[bool] = []
        bar.mute_toggled_requested.connect(mute_events.append)
        bar.settings_requested.connect(lambda: settings_events.append(True))

        # Нажатие кнопки микрофона отправляет сигнал и переводит бейдж в 'Без звука'
        bar._mic_btn.click()
        assert mute_events == [True]
        assert bar._status_badge.text() == "Без звука"

        # Программный set_muted не должен дублировать mute_toggled_requested
        bar.set_muted(False)
        assert mute_events == [True]
        assert bar._status_badge.text() == "Готов"

        bar._settings_btn.click()
        assert settings_events == [True]

        # Симуляция перетаскивания мышью смещает позицию окна
        start_pos = bar.pos()
        press_global = bar.mapToGlobal(start_pos)
        press_evt = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(20.0, 20.0),
            QPointF(press_global),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        bar.mousePressEvent(press_evt)

        move_evt = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(70.0, 50.0),
            QPointF(press_global.x() + 50, press_global.y() + 30),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        bar.mouseMoveEvent(move_evt)

        rel_evt = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(70.0, 50.0),
            QPointF(press_global.x() + 50, press_global.y() + 30),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        bar.mouseReleaseEvent(rel_evt)

        assert bar.pos().x() == start_pos.x() + 50
        assert bar.pos().y() == start_pos.y() + 30
    finally:
        bar.close()
