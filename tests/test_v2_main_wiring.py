"""
> [!NOTE]
> Модуль тестов tests.test_v2_main_wiring: Проверка сквозного связывания компонентов
> GupkaCommander 2.0 в main.py (3-уровневый роутер, многоходовый CASUAL_CHAT,
> ReActEngine + AgentMiniBar + StepCard, abort_requested -> FailSafeMonitor,
> Fast-Path и live-применение настроек settings.json).
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtWidgets import QApplication

from core.agent.react_loop import AgentStepEvent, ReActEngine
from core.app_finder import AppFinder
from core.brain.llama_manager import LlamaServerManager
from core.brain.llm_client import LocalLLMClient
from core.failsafe import FailSafeMonitor
from core.memory import MemoryManager
from core.os_controller import OSController
from core.voice.voice_pipeline import VoicePipeline
from gui.dock_manager import DisplayMode, DockPosition
from gui.hotkey_manager import HotkeyManager
from gui.spotlight_bar import SpotlightBar
from gui.theme_manager import BackdropType, ThemeMode
from gui.tray_icon import TrayManager
from main import GupkaCommanderApp


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Единый экземпляр QApplication для headless-тестов."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


def _build_app(
    qapp: QApplication,
    tmp_path: Path,
    *,
    llm_client: MagicMock | None = None,
    react_engine: MagicMock | None = None,
    llama_manager: MagicMock | None = None,
    spotlight_bar: SpotlightBar | None = None,
) -> tuple[GupkaCommanderApp, MagicMock, MagicMock, MagicMock, MemoryManager]:
    """Создает изолированный GupkaCommanderApp с моками для тестов v2 wiring."""
    mock_os = MagicMock(spec=OSController)
    mock_os.resolve_alias.return_value = None
    mock_finder = MagicMock(spec=AppFinder)
    mock_finder.find.return_value = []
    mock_voice = MagicMock(spec=VoicePipeline)
    mock_voice.synthesizer = None
    mock_failsafe = MagicMock(spec=FailSafeMonitor)
    mock_failsafe.is_interrupted.return_value = False
    memory = MemoryManager(db_path=tmp_path / "wiring_memory.db")
    bar = spotlight_bar or SpotlightBar(hide_on_focus_loss=False)

    mock_llm = llm_client or MagicMock(spec=LocalLLMClient)
    app = GupkaCommanderApp(
        app=qapp,
        app_finder=mock_finder,
        os_controller=mock_os,
        voice_pipeline=mock_voice,
        spotlight_bar=bar,
        tray_manager=TrayManager(),
        hotkey_manager=HotkeyManager(),
        llm_client=mock_llm,
        failsafe_monitor=mock_failsafe,
        memory_manager=memory,
        llama_manager=llama_manager,
        react_engine=react_engine,
        enable_voice=False,
        enable_hotkey=False,
        enable_tray=False,
    )
    return app, mock_os, mock_llm, mock_failsafe, memory


def test_casual_chat_uses_history_without_plan_actions(qapp: QApplication, tmp_path: Path) -> None:
    """CASUAL_CHAT передает историю из MemoryManager и не вызывает plan_actions или ReAct."""
    mock_llm = MagicMock(spec=LocalLLMClient)
    mock_llm.is_healthy.return_value = True
    captured_history: list[list[dict[str, str]] | None] = []

    def fake_stream(
        user_text: str,
        system_prompt: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> Iterator[str]:
        captured_history.append(history)
        yield "Отлично, "
        yield "Денис!"

    mock_llm.generate_response_stream.side_effect = fake_stream
    mock_react = MagicMock(spec=ReActEngine)

    app, _, _, _, memory = _build_app(qapp, tmp_path, llm_client=mock_llm, react_engine=mock_react)
    try:
        memory.log_turn("user", "Кто ты?")
        memory.log_turn("assistant", "Я Губка.")

        resp = app.execute_command("Привет, как дела?")
        assert resp == "Отлично, Денис!"
        mock_llm.plan_actions.assert_not_called()
        mock_react.run.assert_not_called()
        assert len(captured_history) == 1
        hist = captured_history[0]
        assert hist is not None
        assert [m["role"] for m in hist] == ["user", "assistant"]
        assert hist[0]["content"] == "Кто ты?"
        assert hist[1]["content"] == "Я Губка."
    finally:
        app.shutdown()


def test_autonomous_agent_runs_react_updates_ui_and_restores_deck(
    qapp: QApplication, tmp_path: Path
) -> None:
    """AUTONOMOUS_AGENT вызывает ReActEngine, обновляет StepCard/AgentMiniBar и возвращает Deck."""
    mock_llm = MagicMock(spec=LocalLLMClient)
    mock_llm.is_healthy.return_value = True
    mock_react = MagicMock(spec=ReActEngine)
    observed_modes: list[DisplayMode] = []

    def fake_run(goal: str, on_step_event: object = None) -> str:
        assert callable(on_step_event)
        on_step_event(
            AgentStepEvent(
                step_index=1,
                max_steps=8,
                engine="CDP",
                title="Открытие YouTube",
                status="running",
            )
        )
        observed_modes.append(app.spotlight_bar.display_mode)
        on_step_event(
            AgentStepEvent(
                step_index=1,
                max_steps=8,
                engine="CDP",
                title="Открытие YouTube",
                status="done",
                duration_ms=42.0,
                detail="Открыт ролик",
            )
        )
        return "Включено видео на YouTube."

    mock_react.run.side_effect = fake_run
    app, _, _, _, _ = _build_app(qapp, tmp_path, llm_client=mock_llm, react_engine=mock_react)
    try:
        result = app.execute_command("открой прикольное видео на ютуб и включи на свой вкус")
        assert result == "Включено видео на YouTube."
        mock_react.run.assert_called_once()
        assert DisplayMode.MINI_AGENT_BAR in observed_modes
        assert app.spotlight_bar.display_mode == DisplayMode.FULL_DECK
        assert len(app.spotlight_bar.chat_drawer.step_cards) == 1
    finally:
        app.shutdown()


def test_autonomous_agent_restores_deck_on_error_and_fallback_when_unhealthy(
    qapp: QApplication, tmp_path: Path
) -> None:
    """При ошибке ReAct Deck восстанавливается, а при нездоровой LLM работает fallback."""
    mock_llm = MagicMock(spec=LocalLLMClient)
    mock_llm.is_healthy.return_value = True
    mock_react = MagicMock(spec=ReActEngine)
    mock_react.run.side_effect = RuntimeError("Сбой шага")

    app, mock_os, _, _, _ = _build_app(qapp, tmp_path, llm_client=mock_llm, react_engine=mock_react)
    try:
        app.spotlight_bar.set_agent_progress(True, 1, 5, "Тест", "UIA")
        err_resp = app.execute_command("открой антигравити и создай новый проект")
        assert "ошибка" in err_resp.lower()
        assert app.spotlight_bar.display_mode == DisplayMode.FULL_DECK

        mock_react.run.reset_mock()
        mock_llm.is_healthy.return_value = False
        fb_resp = app.execute_command("открой антигравити и создай новый проект")
        mock_react.run.assert_not_called()
        mock_os.search.assert_called_once()
        assert "поиск в сети" in fb_resp.lower()
    finally:
        app.shutdown()


def test_abort_requested_triggers_failsafe(qapp: QApplication, tmp_path: Path) -> None:
    """Нажатие стоп в AgentMiniBar (abort_requested) взводит FailSafeMonitor."""
    app, _, _, mock_failsafe, _ = _build_app(qapp, tmp_path)
    try:
        app.spotlight_bar.abort_requested.emit()
        mock_failsafe.trigger_abort.assert_called_once_with("UI stop button")
    finally:
        app.shutdown()


def test_fast_path_stays_instant_without_react_or_llm(qapp: QApplication, tmp_path: Path) -> None:
    """FAST_PATH команды выполняются через OSController без ReActEngine и LLM."""
    mock_llm = MagicMock(spec=LocalLLMClient)
    mock_llm.is_healthy.return_value = True
    mock_react = MagicMock(spec=ReActEngine)

    app, mock_os, _, _, _ = _build_app(qapp, tmp_path, llm_client=mock_llm, react_engine=mock_react)
    try:
        vol_resp = app.execute_command("громкость выше")
        assert "увеличена" in vol_resp.lower()
        mock_os.volume_up.assert_called_once()
        mock_react.run.assert_not_called()
        mock_llm.plan_actions.assert_not_called()
        mock_llm.generate_response_stream.assert_not_called()
    finally:
        app.shutdown()


def test_settings_live_apply_on_startup_and_dialog_close(
    qapp: QApplication, tmp_path: Path
) -> None:
    """Чтение settings.json на старте и после SettingsDialog обновляет SpotlightBar."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "ui": {
                    "dock_position": "bottom",
                    "target_monitor": "1",
                    "theme": "light",
                    "backdrop": "mica",
                }
            }
        ),
        encoding="utf-8",
    )

    with patch("main.get_config_path", return_value=settings_file):
        app, _, _, _, _ = _build_app(qapp, tmp_path)
        try:
            assert app.spotlight_bar.dock_position == DockPosition.BOTTOM
            assert app.spotlight_bar.theme_manager.mode == ThemeMode.LIGHT
            assert app.spotlight_bar.backdrop_type == BackdropType.MICA

            settings_file.write_text(
                json.dumps(
                    {
                        "ui": {
                            "dock_position": "right",
                            "target_monitor": "auto",
                            "theme": "dark",
                            "backdrop": "acrylic",
                        }
                    }
                ),
                encoding="utf-8",
            )
            fake_dialog = MagicMock()
            with patch("gui.settings_dialog.SettingsDialog", return_value=fake_dialog):
                app.open_settings_dialog()

            fake_dialog.exec.assert_called_once()
            current_bar = app.spotlight_bar
            assert current_bar.dock_position.value == "right"
            assert current_bar.theme_manager.mode.value == "dark"
            assert current_bar.backdrop_type.value == "acrylic"
        finally:
            app.shutdown()


def test_autostart_llama_and_voice_from_settings(qapp: QApplication, tmp_path: Path) -> None:
    """start() запускает LlamaServerManager в фоне, а VoicePipeline — через from_settings."""
    called_event = threading.Event()

    class _FakeLlama(LlamaServerManager):
        def __init__(self) -> None:
            pass

        def start_all(self) -> bool:
            called_event.set()
            return True

        def stop(self) -> None:
            pass

    mock_pipeline = MagicMock(spec=VoicePipeline)
    mock_pipeline.synthesizer = None
    with patch(
        "main.VoicePipeline.from_settings", return_value=mock_pipeline
    ) as mock_from_settings:
        app = GupkaCommanderApp(
            app=qapp,
            app_finder=MagicMock(spec=AppFinder),
            os_controller=MagicMock(spec=OSController),
            spotlight_bar=SpotlightBar(hide_on_focus_loss=False),
            tray_manager=TrayManager(),
            hotkey_manager=HotkeyManager(),
            llm_client=MagicMock(spec=LocalLLMClient),
            failsafe_monitor=MagicMock(spec=FailSafeMonitor),
            memory_manager=MemoryManager(db_path=tmp_path / "autostart.db"),
            llama_manager=_FakeLlama(),
            enable_voice=False,
            enable_hotkey=False,
            enable_tray=False,
            enable_llm_autostart=True,
        )
        try:
            mock_from_settings.assert_called_once()
            assert mock_from_settings.call_args.kwargs.get("echo_guard") is True
            app.start(start_hidden=True)
            assert called_event.wait(timeout=2.0)
        finally:
            app.shutdown()


def test_audio_level_updates_sponge_pore_and_waveform_and_mock_fallback(
    qapp: QApplication, tmp_path: Path
) -> None:
    """Сигнал audio_level_received обновляет SpotlightBar (SpongePore + Waveform) и fallback."""
    app, _, _, _, _ = _build_app(qapp, tmp_path)
    try:
        with patch.object(
            app.spotlight_bar, "set_audio_level", wraps=app.spotlight_bar.set_audio_level
        ) as spy_bar:
            app.voice_bridge.audio_level_received.emit(0.75)
            spy_bar.assert_called_once_with(0.75)
        assert app.spotlight_bar.waveform._target_level == pytest.approx(0.75)
        assert app.spotlight_bar.sponge_pore._target_audio_level == pytest.approx(0.75)
    finally:
        app.shutdown()

    # Проверка fallback для mock spotlight_bar (не isinstance SpotlightBar)
    mock_bar = MagicMock()
    mock_bar.waveform = MagicMock()
    mock_app = GupkaCommanderApp(
        app=qapp,
        app_finder=MagicMock(spec=AppFinder),
        os_controller=MagicMock(spec=OSController),
        voice_pipeline=MagicMock(spec=VoicePipeline),
        spotlight_bar=mock_bar,
        tray_manager=TrayManager(),
        hotkey_manager=HotkeyManager(),
        llm_client=MagicMock(spec=LocalLLMClient),
        failsafe_monitor=MagicMock(spec=FailSafeMonitor),
        memory_manager=MemoryManager(db_path=tmp_path / "mock_audio.db"),
        enable_voice=False,
        enable_hotkey=False,
        enable_tray=False,
    )
    try:
        mock_app.voice_bridge.audio_level_received.emit(0.55)
        mock_bar.waveform.set_audio_level.assert_called_once_with(0.55)
    finally:
        mock_app.shutdown()


def test_dock_position_changed_persists_settings_preserving_other_keys(
    qapp: QApplication, tmp_path: Path
) -> None:
    """dock_position_changed сохраняет ui.dock_position без затирания остальных ключей."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "system": {"language": "ru"},
                "ui": {"dock_position": "top", "theme": "dark"},
            }
        ),
        encoding="utf-8",
    )

    with patch("main.get_config_path", return_value=settings_file):
        app, _, _, _, _ = _build_app(qapp, tmp_path)
        try:
            # При старте _applying_ui_settings не должен лишний раз перезаписывать файл
            app.spotlight_bar.set_dock_position(DockPosition.BOTTOM_RIGHT)
            saved = json.loads(settings_file.read_text(encoding="utf-8"))
            assert saved["ui"]["dock_position"] == "bottom_right"
            assert saved["ui"]["theme"] == "dark"
            assert saved["system"]["language"] == "ru"

            # Во время _apply_ui_settings запись блокируется флагом _applying_ui_settings
            settings_file.write_text(
                json.dumps({"system": {"language": "ru"}, "ui": {"dock_position": "left"}}),
                encoding="utf-8",
            )
            with patch.object(
                app, "_on_dock_position_changed", wraps=app._on_dock_position_changed
            ):
                app._apply_ui_settings()
                assert app.spotlight_bar.dock_position == DockPosition.LEFT
                assert (
                    json.loads(settings_file.read_text(encoding="utf-8"))["ui"]["dock_position"]
                    == "left"
                )
        finally:
            app.shutdown()


def test_context_chips_refresh_in_start_and_execute_command(
    qapp: QApplication, tmp_path: Path
) -> None:
    """start() и execute_command() обновляют живые счётчики памяти и голоса в ContextChipBar."""
    app, _, _, _, memory = _build_app(qapp, tmp_path)
    try:
        memory.set_fact("имя", "Денис")
        memory.add_note("проверить сборку")
        app.start(start_hidden=True)

        mem_btn = app.spotlight_bar.context_chip_bar.get_chip_button("memory")
        voice_btn = app.spotlight_bar.context_chip_bar.get_chip_button("voice")
        assert mem_btn is not None and mem_btn.text() == "Память: 2"
        assert voice_btn is not None and voice_btn.text() == "Без звука"

        app.execute_command("запомни, что город это Казань")
        assert mem_btn.text() == "Память: 3"
    finally:
        app.shutdown()


def test_open_settings_dialog_reloads_voice_engines_and_starts_tts(
    qapp: QApplication, tmp_path: Path
) -> None:
    """open_settings_dialog() перечитывает движки и запускает start_tts() для qwen3-tts."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "ui": {"dock_position": "top"},
                "models": {
                    "stt": {"engine": "faster-whisper"},
                    "tts": {"engine": "piper", "port": 8081},
                },
            }
        ),
        encoding="utf-8",
    )

    pipeline = VoicePipeline.from_settings(settings_path=settings_file)
    tts_started = threading.Event()
    mock_llama = MagicMock(spec=LlamaServerManager)

    def _mark_tts_started() -> bool:
        tts_started.set()
        return True

    mock_llama.start_tts.side_effect = _mark_tts_started

    with patch("main.get_config_path", return_value=settings_file):
        app = GupkaCommanderApp(
            app=qapp,
            app_finder=MagicMock(spec=AppFinder),
            os_controller=MagicMock(spec=OSController),
            voice_pipeline=pipeline,
            spotlight_bar=SpotlightBar(hide_on_focus_loss=False),
            tray_manager=TrayManager(),
            hotkey_manager=HotkeyManager(),
            llm_client=MagicMock(spec=LocalLLMClient),
            failsafe_monitor=MagicMock(spec=FailSafeMonitor),
            memory_manager=MemoryManager(db_path=tmp_path / "settings_reload.db"),
            llama_manager=mock_llama,
            enable_voice=False,
            enable_hotkey=False,
            enable_tray=False,
            enable_llm_autostart=True,
        )
        try:

            def _fake_exec() -> int:
                settings_file.write_text(
                    json.dumps(
                        {
                            "ui": {"dock_position": "top"},
                            "models": {
                                "stt": {"engine": "gigaam-v3"},
                                "tts": {"engine": "qwen3-tts", "port": 8081},
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                return 1

            fake_dialog = MagicMock()
            fake_dialog.exec.side_effect = _fake_exec
            with patch("gui.settings_dialog.SettingsDialog", return_value=fake_dialog):
                app.open_settings_dialog()

            assert pipeline._stt_engine == "gigaam-v3"
            assert pipeline._tts_engine == "qwen3-tts"
            assert tts_started.wait(timeout=2.0)
            mock_llama.start_tts.assert_called_once()
        finally:
            app.shutdown()


def test_voice_pipeline_speaker_ref_and_release_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """VoicePipeline подставляет default_speaker_ru.wav, читает settings и освобождает модели."""
    from core.paths import get_asset_path
    from core.voice.qwen_tts_synthesizer import QwenTTSSynthesizer
    from core.voice.voice_pipeline import DEFAULT_SPEAKER_REF_FILENAME, DEFAULT_SPEAKER_REF_TEXT

    default_wav = get_asset_path(DEFAULT_SPEAKER_REF_FILENAME)
    assert default_wav.is_file()

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "models": {
                    "stt": {"engine": "gigaam-v3"},
                    "tts": {"engine": "qwen3-tts", "lang": "ru", "port": 8081},
                }
            }
        ),
        encoding="utf-8",
    )

    pipeline = VoicePipeline.from_settings(settings_path=settings_file)
    synth = pipeline.synthesizer
    assert isinstance(synth, QwenTTSSynthesizer)
    assert synth._lang == "ru"
    assert synth._speaker_ref_path == default_wav
    assert synth._speaker_ref_text == DEFAULT_SPEAKER_REF_TEXT

    # Проверяем явное переопределение speaker_ref_path/text в settings.json
    custom_wav = tmp_path / "custom.wav"
    custom_wav.write_bytes(b"RIFF-CUSTOM")
    settings_file.write_text(
        json.dumps(
            {
                "models": {
                    "tts": {
                        "engine": "qwen3-tts",
                        "lang": "en",
                        "port": 8081,
                        "speaker_ref_path": str(custom_wav),
                        "speaker_ref_text": "Custom reference text.",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    pipeline.reload_engines_from_settings()
    custom_synth = pipeline.synthesizer
    assert isinstance(custom_synth, QwenTTSSynthesizer)
    assert custom_synth._lang == "en"
    assert custom_synth._speaker_ref_path == custom_wav
    assert custom_synth._speaker_ref_text == "Custom reference text."

    # release_models() останавливает ленивый синтезатор, сбрасывает ссылки и вызывает gc.collect()
    import gc

    pipeline._vad_detector = MagicMock()
    pipeline._transcriber = MagicMock()
    gc_called = [False]
    orig_gc = gc.collect

    def _spy_gc() -> int:
        gc_called[0] = True
        return orig_gc()

    monkeypatch.setattr("core.voice.voice_pipeline.gc.collect", _spy_gc)
    with patch.object(custom_synth, "stop", wraps=custom_synth.stop) as spy_stop:
        pipeline.release_models()
        spy_stop.assert_called_once()

    assert pipeline._vad_detector is None
    assert pipeline._transcriber is None
    assert pipeline._synthesizer is None
    assert gc_called[0] is True


def test_app_dependency_lock_release_and_restart_hooks(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_release_dependency_locks и _on_dependencies_changed управляют блокировками и VAD."""
    mock_llama = MagicMock(spec=LlamaServerManager)
    mock_voice = MagicMock(spec=VoicePipeline)
    mock_voice.synthesizer = None
    mock_voice.is_running.return_value = True

    app = GupkaCommanderApp(
        app=qapp,
        app_finder=MagicMock(spec=AppFinder),
        os_controller=MagicMock(spec=OSController),
        voice_pipeline=mock_voice,
        spotlight_bar=SpotlightBar(hide_on_focus_loss=False),
        tray_manager=TrayManager(),
        hotkey_manager=HotkeyManager(),
        llm_client=MagicMock(spec=LocalLLMClient),
        failsafe_monitor=MagicMock(spec=FailSafeMonitor),
        memory_manager=MemoryManager(db_path=tmp_path / "locks.db"),
        llama_manager=mock_llama,
        enable_voice=True,
        enable_hotkey=False,
        enable_tray=False,
    )
    try:
        # 1. Снятие блокировок останавливает llama_manager, voice_pipeline и вызывает release_models
        app._release_dependency_locks()
        mock_llama.stop.assert_called_once()
        mock_voice.stop.assert_called_once()
        mock_voice.release_models.assert_called_once()

        # 2. Если silero_vad удален — голос НЕ перезапускается
        mock_report_missing = MagicMock()
        mock_report_missing.items = [MagicMock(id="silero_vad", installed=False)]
        with patch("core.bootstrap.DependencyManager") as mock_dep_cls:
            mock_dep_cls.return_value.inspect.return_value = mock_report_missing
            app._on_dependencies_changed()

        mock_voice.reload_engines_from_settings.assert_called_once()
        mock_voice.start.assert_not_called()

        # 3. Если голос работал до удаления и silero_vad на диске — перезапускается
        app._release_dependency_locks()
        mock_report_present = MagicMock()
        mock_report_present.items = [MagicMock(id="silero_vad", installed=True)]
        with patch("core.bootstrap.DependencyManager") as mock_dep_cls:
            mock_dep_cls.return_value.inspect.return_value = mock_report_present
            app._on_dependencies_changed()

        mock_voice.start.assert_called_once()

        # 4. open_dependency_manager пробрасывает хуки в DependencySetupDialog
        with patch("gui.setup_dialog.DependencySetupDialog") as mock_dlg_cls:
            app.open_dependency_manager()
            kwargs = mock_dlg_cls.call_args.kwargs
            assert kwargs["manage_mode"] is True
            assert kwargs["on_before_delete"] == app._release_dependency_locks
            assert kwargs["on_after_delete"] == app._on_dependencies_changed
    finally:
        app.shutdown()


def test_cli_remove_dependencies_yes_and_installer_iss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI --remove-dependencies --yes удаляет models/bin в GUPKA_DATA_DIR без GUI."""
    import sys

    import main as main_mod

    monkeypatch.setenv("GUPKA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("core.bootstrap.shutil.which", lambda _n: None)
    monkeypatch.setattr("core.bootstrap.get_bundle_root", lambda: tmp_path / "bundle")

    models_dir = tmp_path / "models"
    bin_dir = tmp_path / "bin"
    config_dir = tmp_path / "config"
    models_dir.mkdir()
    bin_dir.mkdir()
    config_dir.mkdir()
    (models_dir / "silero_vad.onnx").write_bytes(b"v" * 2048)
    (bin_dir / "llama-server.exe").write_bytes(b"MZ" * 1024)
    memory_db = config_dir / "memory.db"
    memory_db.write_bytes(b"keep")

    monkeypatch.setattr(sys, "argv", ["main.py", "--remove-dependencies", "--yes"])
    args = main_mod.parse_arguments()
    assert args.remove_dependencies is True
    assert args.yes is True

    # Убеждаемся, что QApplication не создается повторно при --remove-dependencies
    with patch("main.QApplication") as mock_qapp:
        exit_code = main_mod.main()
        mock_qapp.assert_not_called()

    assert exit_code == 0
    assert not (models_dir / "silero_vad.onnx").exists()
    assert not (bin_dir / "llama-server.exe").exists()
    assert memory_db.exists()
    captured = capsys.readouterr()
    assert "удалено файлов — 2" in captured.out

    # Проверка секции [UninstallDelete] в scripts/installer.iss
    iss_path = Path(__file__).resolve().parent.parent / "scripts" / "installer.iss"
    iss_text = iss_path.read_text(encoding="utf-8")
    assert "[UninstallDelete]" in iss_text
    uninstall_section = iss_text.split("[UninstallDelete]")[1]
    assert r"{localappdata}\GupkaCommander\models" in uninstall_section
    assert r"{localappdata}\GupkaCommander\bin" in uninstall_section
    assert "Type: filesandordirs" in uninstall_section
    delete_entries = [
        line for line in uninstall_section.splitlines() if line.strip().startswith("Type:")
    ]
    assert len(delete_entries) == 2
    assert all("config" not in line and "memory.db" not in line for line in delete_entries)


def test_ensure_react_engine_wires_screen_capture(qapp: QApplication, tmp_path: Path) -> None:
    """_ensure_react_engine() пробрасывает ScreenCapture в StepVerifier и ReActEngine."""
    from core.vision.screen_capture import ScreenCapture
    from core.vision.vision_agent import VisionAgent

    real_sc = ScreenCapture()
    vision = VisionAgent(screen_capture=real_sc)
    app = GupkaCommanderApp(
        app=qapp,
        app_finder=MagicMock(spec=AppFinder),
        os_controller=MagicMock(spec=OSController),
        voice_pipeline=MagicMock(spec=VoicePipeline),
        spotlight_bar=SpotlightBar(hide_on_focus_loss=False),
        tray_manager=TrayManager(),
        hotkey_manager=HotkeyManager(),
        llm_client=MagicMock(spec=LocalLLMClient),
        vision_agent=vision,
        failsafe_monitor=MagicMock(spec=FailSafeMonitor),
        memory_manager=MemoryManager(db_path=tmp_path / "sc_wiring.db"),
        enable_voice=False,
        enable_hotkey=False,
        enable_tray=False,
    )
    try:
        assert app.react_engine is not None
        assert app.step_verifier is not None
        assert app.react_engine.screen_capture is real_sc
        assert app.step_verifier._screen_capture is real_sc
    finally:
        app.shutdown()

    # Также проверяем случай VisionAgent-подкласса/мока без реального ScreenCapture-атрибута
    class _VisionNoSc(VisionAgent):
        def __init__(self) -> None:
            self._screen_capture = None  # type: ignore[assignment]

    app_fallback_sc = GupkaCommanderApp(
        app=qapp,
        app_finder=MagicMock(spec=AppFinder),
        os_controller=MagicMock(spec=OSController),
        voice_pipeline=MagicMock(spec=VoicePipeline),
        spotlight_bar=SpotlightBar(hide_on_focus_loss=False),
        tray_manager=TrayManager(),
        hotkey_manager=HotkeyManager(),
        llm_client=MagicMock(spec=LocalLLMClient),
        vision_agent=_VisionNoSc(),
        failsafe_monitor=MagicMock(spec=FailSafeMonitor),
        memory_manager=MemoryManager(db_path=tmp_path / "sc_fallback.db"),
        enable_voice=False,
        enable_hotkey=False,
        enable_tray=False,
    )
    try:
        assert app_fallback_sc.react_engine is not None
        assert app_fallback_sc.step_verifier is not None
        assert isinstance(app_fallback_sc.react_engine.screen_capture, ScreenCapture)
        assert (
            app_fallback_sc.step_verifier._screen_capture
            is app_fallback_sc.react_engine.screen_capture
        )
    finally:
        app_fallback_sc.shutdown()


def test_offline_casual_chat_never_triggers_web_search(qapp: QApplication, tmp_path: Path) -> None:
    """При оффлайн LLM запросы CASUAL_CHAT не вызывают os_controller.search."""
    mock_llm = MagicMock(spec=LocalLLMClient)
    mock_llm.is_healthy.return_value = False
    mock_llm.fallback_engine = None

    app, mock_os, _, _, _ = _build_app(qapp, tmp_path, llm_client=mock_llm)
    try:
        reply_weather = app.execute_command("Какая сегодня погода?")
        assert "Qwen3.5" in reply_weather
        assert "Модели и зависимости" in reply_weather
        mock_os.search.assert_not_called()

        # Шаблон поиска или статус недоступности тоже заменяется безопасным текстом
        fake_fb = MagicMock()
        fake_fb.respond.return_value = "Команда не найдена локально. Выполняю поиск в сети: тест"
        mock_llm.fallback_engine = fake_fb
        reply_stub = app.execute_command("Что ты умеешь делать?")
        assert "Qwen3.5" in reply_stub
        mock_os.search.assert_not_called()

        # Разговорный ответ fallback_engine (например, приветствие) возвращается напрямую
        fake_fb.respond.return_value = "Привет! Я локальный оператор GupkaCommander. Чем помочь?"
        reply_hi = app.execute_command("Привет, как дела?")
        assert reply_hi == "Привет! Я локальный оператор GupkaCommander. Чем помочь?"
        mock_os.search.assert_not_called()
    finally:
        app.shutdown()


def test_retry_phrase_routes_to_react_with_last_goal_and_memory_fallback(
    qapp: QApplication, tmp_path: Path
) -> None:
    """Ретрай с _last_agent_goal или эпизодом из памяти всегда идет в ReActEngine, не в чат."""
    mock_llm = MagicMock(spec=LocalLLMClient)
    mock_llm.is_healthy.return_value = True
    mock_react = MagicMock(spec=ReActEngine)
    seen_goals: list[str] = []

    def _fake_run(goal: str, on_step_event: object = None) -> str:
        seen_goals.append(goal)
        return "Готово."

    mock_react.run.side_effect = _fake_run
    app, _, _, _, memory = _build_app(qapp, tmp_path, llm_client=mock_llm, react_engine=mock_react)
    try:
        orig_goal = "открой антигравити и создай новый проект"
        app.execute_command(orig_goal)
        assert app._last_agent_goal == orig_goal

        app.execute_command("Так, давай ещё раз")
        assert len(seen_goals) == 2
        assert f"Повторная попытка цели: {orig_goal}" in seen_goals[1]
        assert "click_screen" in seen_goals[1]
        assert "press_key" in seen_goals[1]
        assert "PowerShell" in seen_goals[1]
        assert app._last_agent_goal == orig_goal
        mock_llm.generate_response_stream.assert_not_called()

        # Повтор из новой сессии подхватывает последний эпизод из MemoryManager
        memory.save_episode(
            goal="открой калькулятор и посчитай 2+2",
            steps=[],
            outcome="Не удалось",
            success=False,
        )
        app2, _, _, _, _ = _build_app(qapp, tmp_path, llm_client=mock_llm, react_engine=mock_react)
        try:
            app2._last_agent_goal = ""
            app2.execute_command("попробуй снова")
            assert "открой калькулятор и посчитай 2+2" in seen_goals[-1]
            mock_llm.generate_response_stream.assert_not_called()
        finally:
            app2.shutdown()
    finally:
        app.shutdown()


def test_stream_card_always_finishes_on_error_and_interrupt(
    qapp: QApplication, tmp_path: Path
) -> None:
    """Карточка стриминга всегда переводится в 'error' при исключении или InterruptedError."""
    mock_llm = MagicMock(spec=LocalLLMClient)
    mock_llm.is_healthy.return_value = True

    def _failing_stream(_text: str, **_kwargs: object) -> Iterator[str]:
        yield "Начало ответа... "
        raise RuntimeError("Сбой SSE потока")

    mock_llm.generate_response_stream.side_effect = _failing_stream
    app, _, _, mock_failsafe, _ = _build_app(qapp, tmp_path, llm_client=mock_llm)
    try:
        err_resp = app.execute_command("Привет, расскажи про Python")
        assert "ошибка" in err_resp.lower()
        assert app._active_stream_card is None
        stream_cards = [
            c for c in app.spotlight_bar.chat_drawer._cards if c.get_role() == "assistant"
        ]
        assert stream_cards
        assert stream_cards[0].get_status() == "error"

        # Теперь проверяем InterruptedError от FailSafeMonitor посреди стрима
        def _interrupted_stream(_text: str, **_kwargs: object) -> Iterator[str]:
            yield "Поток "
            mock_failsafe.is_interrupted.return_value = True
            yield "прерван"

        mock_llm.generate_response_stream.side_effect = _interrupted_stream
        mock_failsafe.is_interrupted.return_value = False
        app.spotlight_bar.chat_drawer.clear()

        abort_resp = app.execute_command("Привет, как дела?")
        assert "прервана" in abort_resp.lower()
        assert app._active_stream_card is None
        cards_after_abort = [
            c for c in app.spotlight_bar.chat_drawer._cards if c.get_role() == "assistant"
        ]
        assert cards_after_abort
        assert cards_after_abort[0].get_status() == "error"
    finally:
        app.shutdown()


def test_refresh_context_chips_reads_win32_foreground_skipping_gupka(
    qapp: QApplication, tmp_path: Path
) -> None:
    """_refresh_context_chips читает активное окно через Win32, пропуская GupkaCommander."""
    import ctypes

    app, _, _, _, _ = _build_app(qapp, tmp_path)
    try:
        titles = {100: "GupkaCommander", 200: "Antigravity — localAgent"}

        fake_user32 = MagicMock()
        fake_user32.GetForegroundWindow.return_value = 100
        fake_user32.GetWindow.side_effect = lambda hwnd, _cmd: 200 if hwnd == 100 else 0
        fake_user32.IsWindowVisible.return_value = 1

        def _fake_get_text(hwnd: int, buf: object, _max_len: int) -> int:
            txt = titles.get(hwnd, "")
            buf.value = txt  # type: ignore[attr-defined]
            return len(txt)

        fake_user32.GetWindowTextW.side_effect = _fake_get_text

        with (
            patch("main.os.name", "nt"),
            patch.object(ctypes, "windll", MagicMock(user32=fake_user32), create=True),
        ):
            app._refresh_context_chips()

        app_btn = app.spotlight_bar.context_chip_bar.get_chip_button("app")
        assert app_btn is not None
        assert app_btn.text() == "Antigravity — localAgent"
    finally:
        app.shutdown()


def test_casual_chat_prompt_forbids_refusals_and_stub_scripts(
    qapp: QApplication, tmp_path: Path
) -> None:
    """Системный промпт LocalLLMClient и _stream_cognitive_response запрещает отказы и заглушки."""
    from core.brain.llm_client import DEFAULT_CHAT_SYSTEM_PROMPT

    assert "GupkaCommander" in DEFAULT_CHAT_SYSTEM_PROMPT
    assert "нет доступа к экрану/интерфейсу" in DEFAULT_CHAT_SYSTEM_PROMPT
    assert "open_app.py" in DEFAULT_CHAT_SYSTEM_PROMPT

    captured_prompts: list[str | None] = []
    mock_llm = MagicMock(spec=LocalLLMClient)
    mock_llm.is_healthy.return_value = True

    def _fake_stream(
        _text: str,
        system_prompt: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> Iterator[str]:
        captured_prompts.append(system_prompt)
        yield "Готов помочь!"

    mock_llm.generate_response_stream.side_effect = _fake_stream
    app, _, _, _, memory = _build_app(qapp, tmp_path, llm_client=mock_llm)
    try:
        memory.set_fact("редактор", "Antigravity")
        reply = app.execute_command("Привет, что нового?")
        assert reply == "Готов помочь!"
        assert captured_prompts and captured_prompts[0] is not None
        assert "нет доступа к экрану/интерфейсу" in captured_prompts[0]
        assert "open_app.py" in captured_prompts[0]
        assert "Antigravity" in captured_prompts[0]
    finally:
        app.shutdown()


def test_e2e_electron_launch_vision_som_filter_and_retry(
    qapp: QApplication, tmp_path: Path
) -> None:
    """Сквозной тест: Electron UIA (4 кнопки рамки) -> Vision без Свернуть -> ретрай."""
    from core.app_finder import AppInfo
    from core.vision.monitor_manager import MonitorInfo, MonitorManager
    from core.vision.screen_capture import ScreenFrame
    from core.vision.som_annotator import SoMAnnotator

    mock_finder = MagicMock(spec=AppFinder)
    mock_finder.find.return_value = [
        AppInfo(name="Antigravity", path=r"C:\Apps\Antigravity.exe", source="test")
    ]
    mock_os = MagicMock(spec=OSController)
    mock_os.resolve_alias.return_value = None

    mon_mgr = MonitorManager(
        monitors=[MonitorInfo(index=1, left=0, top=0, width=1920, height=1080, is_primary=True)]
    )
    mock_sc = MagicMock()
    mock_sc.capture_frame.return_value = ScreenFrame(
        width=1920,
        height=1080,
        rgb=b"\x20" * (1920 * 1080 * 3),
        dhash="4444444444444444",
        monitor_index=1,
    )
    mock_uia = MagicMock()
    mock_uia.inspect_window_elements.return_value = [
        {
            "name": name,
            "control_type": "ButtonControl",
            "rect": {"left": 1700 + i * 50, "top": 0, "right": 1745 + i * 50, "bottom": 30},
        }
        for i, name in enumerate(("Свернуть", "Развернуть", "Восстановить", "Закрыть"))
    ]
    som = SoMAnnotator()
    mock_vision = MagicMock()
    mock_vision.screen_capture = mock_sc
    mock_vision.click_at.return_value = True

    mock_llm = MagicMock(spec=LocalLLMClient)
    mock_llm.tools = []
    mock_llm.is_healthy.return_value = True

    seen_calls: list[dict[str, Any]] = []

    def _plan_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
        seen_calls.append(kwargs)
        step = len(seen_calls)
        if step == 1:
            return [{"tool": "open_application", "parameters": {"app_name": "Antigravity"}}]
        if step == 2:
            # Имитируем ошибочную попытку нажать [1] на 1-м запуске -> защитный SoM-фильтр отклоняет
            return [{"tool": "click_som", "parameters": {"mark_id": 1}}]
        if step == 3:
            return [
                {
                    "tool": "finish_task",
                    "parameters": {
                        "summary": "Открыл Antigravity, но проект не создан.",
                        "success": False,
                    },
                }
            ]
        # Повторная попытка после "Так, давай ещё раз" -> альтернативные инструменты Vision/hotkey
        if step == 4:
            return [
                {"tool": "press_key", "parameters": {"key": "ctrl+shift+p"}},
                {"tool": "click_screen", "parameters": {"x": 500, "y": 300}},
            ]
        return [
            {
                "tool": "finish_task",
                "parameters": {"summary": "Проект создан через палитру команд.", "success": True},
            }
        ]

    mock_llm.plan_actions.side_effect = _plan_side_effect

    memory = MemoryManager(db_path=tmp_path / "e2e_electron.db")
    from core.agent.tool_registry import ToolRegistry

    fs = FailSafeMonitor()
    registry = ToolRegistry(
        os_controller=mock_os,
        app_finder=mock_finder,
        uia_manager=mock_uia,
        vision_agent=mock_vision,
        som_annotator=som,
        memory_manager=memory,
        monitor_manager=mon_mgr,
        failsafe_monitor=fs,
    )
    react_engine = ReActEngine(
        llm_client=mock_llm,
        tool_registry=registry,
        memory_manager=memory,
        monitor_manager=mon_mgr,
        uia_manager=mock_uia,
        screen_capture=mock_sc,
        som_annotator=som,
        failsafe_monitor=fs,
        max_steps=6,
    )

    app = GupkaCommanderApp(
        app=qapp,
        app_finder=mock_finder,
        os_controller=mock_os,
        voice_pipeline=MagicMock(spec=VoicePipeline, synthesizer=None),
        spotlight_bar=SpotlightBar(hide_on_focus_loss=False),
        tray_manager=TrayManager(),
        hotkey_manager=HotkeyManager(),
        llm_client=mock_llm,
        vision_agent=mock_vision,
        uia_manager=mock_uia,
        failsafe_monitor=fs,
        memory_manager=memory,
        som_annotator=som,
        tool_registry=registry,
        react_engine=react_engine,
        enable_voice=False,
        enable_hotkey=False,
        enable_tray=False,
    )
    try:
        with patch.object(app, "_launch_app", return_value=True):
            first_resp = app.execute_command("открой антигравити и создай новый проект")
            assert "проект не создан" in first_resp.lower()
            assert seen_calls[0]["image_b64"] is not None
            assert som.last_lookup == {}

            retry_resp = app.execute_command("Так, давай ещё раз")
            assert retry_resp == "Проект создан через палитру команд."
            assert (
                "Повторная попытка цели: открой антигравити и создай новый проект"
                in seen_calls[3]["prompt"]
            )
            mock_vision.hotkey.assert_called_once_with("ctrl", "shift", "p")
            mock_vision.click_at.assert_called_once()
            mock_llm.generate_response_stream.assert_not_called()
    finally:
        app.shutdown()
