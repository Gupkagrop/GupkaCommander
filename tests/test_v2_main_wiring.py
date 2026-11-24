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
