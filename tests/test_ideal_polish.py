"""Тесты финальной полировки по рекомендациям Gemini 4 Argon.

Проверяют:
1. Неблокирующее выполнение команд в QThread (CommandWorker) и живой стриминг через VoiceBridge.
2. Автоматический жизненный цикл llama-server.exe (LlamaServerManager).
3. Отдельное окно настроек SettingsDialog и разделение пунктов трея.
4. Подавление ложного Mouse Shake в FailSafeMonitor во время движений агента (при сохранении Esc).
5. Изоляцию сессий PiperSynthesizer при быстром Barge-In.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pynput import keyboard  # type: ignore[import-untyped]
from PySide6.QtWidgets import QApplication

from core.bootstrap import DependencyManager
from core.brain.llama_manager import (
    DEFAULT_MMPROJ_FILENAME,
    DEFAULT_MODEL_FILENAME,
    LlamaServerManager,
)
from core.failsafe import FailSafeMonitor
from core.voice.synthesizer import PiperSynthesizer
from gui.settings_dialog import SettingsDialog
from gui.spotlight_bar import SpotlightBar
from gui.tray_icon import TrayManager
from main import GupkaCommanderApp


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """Offscreen QApplication для headless-тестирования GUI и QThread."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    return app


class TestAsyncCommandAndStreamingUI:
    """Тесты фонового выполнения команд в QThread и живого обновления карточек через VoiceBridge."""

    def test_async_sse_streaming_updates_chat_card_off_gui_thread(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        """Проверка неблокирующего стриминга токенов в фоновом QThread с обновлением ChatDrawer."""
        mock_llm = MagicMock()
        mock_llm.is_healthy.return_value = True
        mock_llm.plan_actions.return_value = []

        worker_thread_ids: list[int] = []

        def _fake_stream(_prompt: str, **_kwargs: object) -> Iterator[str]:
            worker_thread_ids.append(threading.get_ident())
            yield "Привет, "
            time.sleep(0.02)
            yield "Денис!"

        mock_llm.generate_response_stream.side_effect = _fake_stream

        bar = SpotlightBar()
        tray = TrayManager()
        mock_mem = MagicMock()
        mock_mem.format_system_prompt_context.return_value = ""

        app_ctrl = GupkaCommanderApp(
            app=qapp,
            llm_client=mock_llm,
            spotlight_bar=bar,
            tray_manager=tray,
            memory_manager=mock_mem,
            enable_voice=False,
            enable_hotkey=False,
            enable_tray=False,
            async_execution=True,
        )

        main_tid = threading.get_ident()
        received_chunks: list[str] = []
        app_ctrl.voice_bridge.stream_chunk_received.connect(received_chunks.append)

        started = app_ctrl.execute_command_async("Расскажи о себе")
        assert started is True
        assert app_ctrl._command_worker is not None

        # Прокачиваем очередь событий Qt до завершения фонового потока
        deadline = time.monotonic() + 3.0
        while app_ctrl._command_worker.isRunning() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        app_ctrl._command_worker.wait(2000)
        qapp.processEvents()

        # Воркер действительно выполнялся не в главном потоке GUI
        assert worker_thread_ids and worker_thread_ids[0] != main_tid
        assert received_chunks == ["Привет, ", "Денис!"]

        cards = bar.chat_drawer._cards
        assert len(cards) >= 2
        assert cards[-1].get_text() == "Привет, Денис!"
        assert cards[-1].get_status() == "done"

        app_ctrl.shutdown()
        bar.deleteLater()
        tray.deleteLater()

    def test_tray_settings_and_models_have_separate_handlers(self, qapp: QApplication) -> None:
        """Проверка разведения сигналов трея 'Настройки...' и 'Модели и зависимости...'."""
        tray = TrayManager()
        bar = SpotlightBar()
        app_ctrl = GupkaCommanderApp(
            app=qapp,
            spotlight_bar=bar,
            tray_manager=tray,
            enable_voice=False,
            enable_hotkey=False,
            enable_tray=False,
        )

        with (
            patch.object(app_ctrl, "open_settings_dialog") as mock_settings,
            patch.object(app_ctrl, "open_dependency_manager") as mock_models,
        ):
            # Переподключаем на моки через публичные сигналы
            tray.settings_triggered.disconnect()
            tray.models_triggered.disconnect()
            tray.settings_triggered.connect(mock_settings)
            tray.models_triggered.connect(mock_models)

            tray.settings_action.trigger()
            tray.models_action.trigger()

            mock_settings.assert_called_once()
            mock_models.assert_called_once()

        # Проверяем, что оба метода существуют и отличны друг от друга
        assert app_ctrl.open_settings_dialog != app_ctrl.open_dependency_manager
        app_ctrl.shutdown()
        bar.deleteLater()
        tray.deleteLater()


class TestLlamaServerManager:
    """Тесты менеджера жизненного цикла llama-server.exe."""

    def test_skips_when_missing_or_external_healthy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Проверка пропуска запуска при отсутствии файлов или уже работающем внешнем сервере."""
        monkeypatch.setattr("core.bootstrap.shutil.which", lambda _name: None)
        monkeypatch.setattr("core.bootstrap.get_bundle_root", lambda: tmp_path / "bundle")

        mock_popen = MagicMock()
        mgr = LlamaServerManager(
            models_dir=tmp_path / "models",
            bin_dir=tmp_path / "bin",
            settings_path=tmp_path / "settings.json",
            popen_factory=mock_popen,
            health_checker=lambda: False,
        )
        assert mgr.is_available() is False
        assert mgr.start() is False
        mock_popen.assert_not_called()

        # Если внешний сервер уже запущен — start() возвращает True без создания процесса
        mgr_ext = LlamaServerManager(
            models_dir=tmp_path / "models",
            bin_dir=tmp_path / "bin",
            settings_path=tmp_path / "settings.json",
            popen_factory=mock_popen,
            health_checker=lambda: True,
        )
        assert mgr_ext.start() is True
        mock_popen.assert_not_called()

    def test_start_respects_autostart_and_stops_managed_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Проверка запуска llama-server.exe, учета autostart и корректной остановки в stop()."""
        monkeypatch.setattr("core.bootstrap.shutil.which", lambda _name: None)
        monkeypatch.setattr("core.bootstrap.get_bundle_root", lambda: tmp_path / "bundle")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(parents=True)
        binary = bin_dir / "llama-server.exe"
        binary.write_bytes(b"MZ")

        llm_dir = tmp_path / "models" / "llm"
        llm_dir.mkdir(parents=True)
        (llm_dir / DEFAULT_MODEL_FILENAME).write_bytes(b"gguf")
        (llm_dir / DEFAULT_MMPROJ_FILENAME).write_bytes(b"mmproj")

        settings_file = tmp_path / "settings.json"
        settings_file.write_text(
            json.dumps({"models": {"llm": {"autostart": False, "n_gpu_layers": 50}}}),
            encoding="utf-8",
        )

        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        mock_popen = MagicMock(return_value=fake_proc)

        with patch("core.brain.llama_manager.MIN_VLM_BYTES", 1):
            mgr = LlamaServerManager(
                models_dir=tmp_path / "models",
                bin_dir=bin_dir,
                settings_path=settings_file,
                popen_factory=mock_popen,
                health_checker=lambda: False,
            )
            # При autostart=False обычный start() не запускает процесс
            assert mgr.start() is False
            mock_popen.assert_not_called()

            # При force=True процесс запускается с нужными аргументами
            assert mgr.start(force=True) is True
            mock_popen.assert_called_once()
            cmd = mock_popen.call_args.args[0]
            assert str(binary) == cmd[0]
            assert "--model" in cmd
            assert "--mmproj" in cmd
            assert "-ngl" in cmd and "50" in cmd
            assert mgr.is_running_managed() is True

            # Корректное завершение
            mgr.stop()
            fake_proc.terminate.assert_called_once()


class TestSettingsDialogGUI:
    """Headless-тесты окна настроек SettingsDialog."""

    def test_load_modify_and_save_preserves_unknown_keys(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        """Проверка загрузки, редактирования и сохранения настроек с сохранением прочих ключей."""
        settings_path = tmp_path / "config" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(
            json.dumps(
                {
                    "custom_flag": "keep_me",
                    "audio": {"vad_threshold": 0.45, "silence_duration_ms": 500},
                }
            ),
            encoding="utf-8",
        )
        dep_mgr = DependencyManager(
            models_dir=tmp_path / "models",
            bin_dir=tmp_path / "bin",
            state_file=tmp_path / "config" / "bootstrap_state.json",
        )

        dlg = SettingsDialog(settings_path=settings_path, dep_manager=dep_mgr)
        assert dlg.vad_threshold_spin.value() == pytest.approx(0.45)
        assert dlg.silence_ms_spin.value() == 500

        dlg.vad_threshold_spin.setValue(0.65)
        dlg.silence_ms_spin.setValue(750)
        dlg.shake_threshold_spin.setValue(450)
        dlg.llm_autostart_cb.setChecked(False)
        dlg.show_bootstrap_cb.setChecked(False)

        saved = dlg.save_settings()
        assert saved["custom_flag"] == "keep_me"
        assert saved["audio"]["vad_threshold"] == pytest.approx(0.65)
        assert saved["audio"]["silence_duration_ms"] == 750
        assert saved["system"]["mouse_failsafe"]["shake_threshold_pixels"] == 450
        assert saved["models"]["llm"]["autostart"] is False
        assert dep_mgr.is_skip_prompt_saved() is True

        # Проверяем сборку .spec
        spec_text = (Path(__file__).resolve().parent.parent / "GupkaCommander.spec").read_text(
            encoding="utf-8"
        )
        assert "'core.brain.llama_manager'" in spec_text
        assert "'gui.settings_dialog'" in spec_text
        assert "settings.json" in spec_text
        dlg.close()


class TestFailSafeAndSynthesizerHardening:
    """Тесты подавления Mouse Shake во время работы агента и изоляции сессий TTS."""

    def test_suppress_mouse_shake_allows_agent_moves_but_keeps_esc(self) -> None:
        """Синтетические движения внутри suppress_mouse_shake не вызывают сбой, а Esc работает."""
        monitor = FailSafeMonitor(shake_window_seconds=0.5, shake_threshold_px=200.0)

        with monitor.suppress_mouse_shake():
            monitor._on_mouse_move(0, 0)
            monitor._on_mouse_move(500, 500)
            monitor._on_mouse_move(0, 0)
            assert monitor.is_aborted() is False

            # Но нажатие Esc по-прежнему мгновенно останавливает операцию
            monitor._on_key_press(keyboard.Key.esc)
            assert monitor.is_aborted() is True
            assert monitor.abort_reason == "Escape key pressed"

        # После сброса и выхода из контекста потряхивание снова активно
        monitor.reset_abort()
        monitor._on_mouse_move(0, 0)
        monitor._on_mouse_move(500, 0)
        assert monitor.is_aborted() is True
        assert monitor.abort_reason == "Mouse shake detected"

    def test_synthesizer_session_isolation_on_rapid_barge_in(self) -> None:
        """Запоздалое завершение старой сессии не сбрасывает _is_speaking новой сессии."""
        synth = PiperSynthesizer(use_sapi_fallback=False)
        synth.initialize()

        old_session_id, old_stop = synth._begin_session()
        assert synth.is_speaking() is True

        # Имитация мгновенного Barge-In: новая сессия стартует до завершения старого потока
        synth.stop()
        assert old_stop.is_set() is True

        new_session_id, new_stop = synth._begin_session()
        assert new_session_id > old_session_id
        assert new_stop.is_set() is False
        assert old_stop.is_set() is True  # Старая сессия остается остановленной навсегда

        # Старый поток доходит до finally -> _finish_session(old_session_id)
        synth._finish_session(old_session_id)
        assert synth.is_speaking() is True  # Новая сессия не затронута!

        # Завершение актуальной сессии корректно сбрасывает флаг
        synth._finish_session(new_session_id)
        assert synth.is_speaking() is False
