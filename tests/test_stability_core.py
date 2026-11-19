"""
> [!NOTE]
> Модуль тестов tests.test_stability_core: Проверка стабилизации координатора main.py.
> Тестирует жизненный цикл FailSafe, защиту от реентрантности и одновременного выполнения,
> безопасный визуальный fallback (без слепого клика), реальный Mute и проброс индикатора RMS.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Generator
from unittest.mock import MagicMock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from core.app_finder import AppFinder
from core.brain.llm_client import LocalLLMClient
from core.failsafe import FailSafeMonitor
from core.memory import MemoryManager
from core.os_controller import OSController
from core.uia import UIAutomationManager
from core.vision.vision_agent import VisionAgent
from core.voice.voice_pipeline import VoicePipeline
from gui.hotkey_manager import HotkeyManager
from gui.spotlight_bar import SpotlightBar
from gui.tray_icon import TrayManager
from gui.waveform_widget import WaveformWidget
from main import GupkaCommanderApp


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Синглтон QApplication для headless-тестирования Qt."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def stability_app(
    qapp: QApplication,
) -> Generator[tuple[GupkaCommanderApp, MagicMock, MagicMock, MagicMock, MagicMock], None, None]:
    """Фикстура для изолированного тестирования стабильности GupkaCommanderApp."""
    mock_finder = MagicMock(spec=AppFinder)
    mock_os = MagicMock(spec=OSController)
    mock_voice = MagicMock(spec=VoicePipeline)
    mock_bar = SpotlightBar(hide_on_focus_loss=False)
    mock_tray = TrayManager()
    mock_hotkey = MagicMock(spec=HotkeyManager)
    mock_uia = MagicMock(spec=UIAutomationManager)
    mock_vision = MagicMock(spec=VisionAgent)
    mock_failsafe = MagicMock(spec=FailSafeMonitor)
    mock_failsafe.is_interrupted.return_value = False
    mock_failsafe.is_aborted.return_value = False
    mock_memory = MagicMock(spec=MemoryManager)
    mock_llm = MagicMock(spec=LocalLLMClient)

    app = GupkaCommanderApp(
        app=qapp,
        app_finder=mock_finder,
        os_controller=mock_os,
        voice_pipeline=mock_voice,
        spotlight_bar=mock_bar,
        tray_manager=mock_tray,
        hotkey_manager=mock_hotkey,
        uia_manager=mock_uia,
        vision_agent=mock_vision,
        failsafe_monitor=mock_failsafe,
        memory_manager=mock_memory,
        llm_client=mock_llm,
        enable_voice=True,
        enable_hotkey=True,
        enable_tray=True,
    )

    yield app, mock_failsafe, mock_voice, mock_vision, mock_llm
    app.shutdown()


class TestFailSafeLifecycle:
    """Тестирование корректного управления жизненным циклом FailSafeMonitor."""

    def test_failsafe_starts_with_app(
        self,
        stability_app: tuple[GupkaCommanderApp, MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Проверка запуска FailSafeMonitor при старте приложения."""
        app, mock_failsafe, _, _, _ = stability_app
        mock_failsafe.reset_mock()

        app.start()
        mock_failsafe.start.assert_called_once()

    def test_failsafe_stops_on_shutdown_and_is_idempotent(
        self,
        stability_app: tuple[GupkaCommanderApp, MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Проверка остановки FailSafe и идемпотентности метода shutdown."""
        app, mock_failsafe, mock_voice, _, _ = stability_app
        mock_failsafe.reset_mock()
        mock_voice.reset_mock()

        # Первый вызов shutdown
        app.shutdown()
        mock_failsafe.stop.assert_called_once()
        mock_voice.stop.assert_called_once()

        # Повторный вызов shutdown не должен повторно останавливать ресурсы
        mock_failsafe.reset_mock()
        mock_voice.reset_mock()
        app.shutdown()
        mock_failsafe.stop.assert_not_called()
        mock_voice.stop.assert_not_called()

    def test_execute_command_resets_abort_flag(
        self,
        stability_app: tuple[GupkaCommanderApp, MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Проверка сброса флага аварийного прерывания перед выполнением команды."""
        app, mock_failsafe, _, _, _ = stability_app
        mock_failsafe.reset_mock()

        app.execute_command("громкость 50%")
        mock_failsafe.reset_abort.assert_called_once()

    def test_failsafe_reset_abort_exception_safe(
        self,
        stability_app: tuple[GupkaCommanderApp, MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Проверка безопасного выполнения команды даже при ошибке сброса FailSafe."""
        app, mock_failsafe, _, _, _ = stability_app
        mock_failsafe.reset_abort.side_effect = RuntimeError("Сбой таймера FailSafe")

        resp = app.execute_command("громкость 50%")
        assert resp != ""

    def test_failsafe_interrupted_error_handling(
        self,
        stability_app: tuple[GupkaCommanderApp, MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Проверка возврата сообщения и освобождения блокировки при InterruptedError."""
        app, _, _, _, _ = stability_app

        with patch.object(app, "_dispatch_command", side_effect=InterruptedError("Esc pressed")):
            resp = app.execute_command("нажми кнопку")
            assert "Операция экстренно прервана пользователем (FailSafe)." in resp

        # Убеждаемся, что флаг реентрантности сброшен и следующая команда выполняется
        assert not app._is_executing_command
        follow_up_resp = app.execute_command("громкость 50%")
        assert follow_up_resp != ""


class TestCommandExecutionReentrancy:
    """Тестирование защиты от одновременного выполнения команд и реентрантности."""

    def test_concurrent_execution_rejection(
        self,
        stability_app: tuple[GupkaCommanderApp, MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Проверка отклонения новой команды, если предыдущая еще выполняется."""
        app, _, _, _, _ = stability_app

        started_barrier = threading.Barrier(2)
        release_event = threading.Event()
        results: list[str] = []

        def slow_dispatch(text: str, route: object = None) -> str:
            started_barrier.wait()
            # Имитируем долгое выполнение
            release_event.wait(timeout=1.0)
            return "Медленный ответ"

        with patch.object(app, "_dispatch_command", side_effect=slow_dispatch):
            t1 = threading.Thread(
                target=lambda: results.append(app.execute_command("первая команда"))
            )
            t1.start()

            # Ждем, пока t1 захватит флаг выполнения
            started_barrier.wait()

            # Вторая команда должна немедленно отклониться
            res2 = app.execute_command("вторая команда")

            # Отпускаем первую команду
            release_event.set()
            t1.join(timeout=2.0)

        assert "Предыдущая операция еще не завершена" in res2
        assert len(results) == 1
        assert results[0] == "Медленный ответ"

    def test_no_process_events_in_stream(
        self,
        stability_app: tuple[GupkaCommanderApp, MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Проверка отсутствия вызова QApplication.processEvents во время стриминга."""
        app, _, mock_voice, _, mock_llm = stability_app
        mock_llm.is_healthy.return_value = True
        mock_llm.plan_actions.return_value = []
        mock_llm.generate_response_stream.return_value = iter(["Токен 1", "Токен 2"])

        mock_synth = MagicMock()
        mock_voice.synthesizer = mock_synth

        with patch("PySide6.QtWidgets.QApplication.processEvents") as mock_pe:
            resp = app.execute_command("Привет")
            assert "Токен 1" in resp
            mock_pe.assert_not_called()

    def test_reentrancy_lock_released_on_unhandled_exception(
        self,
        stability_app: tuple[GupkaCommanderApp, MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Проверка сброса блокировки реентрантности при необработанном исключении."""
        app, _, _, _, _ = stability_app

        with patch.object(app, "_dispatch_command", side_effect=ValueError("Непредвиденный сбой")):
            resp = app.execute_command("сбойная команда")
            assert "Произошла ошибка при выполнении" in resp

        assert not app._is_executing_command
        # Следующая команда должна успешно выполниться
        res_next = app.execute_command("громкость 50%")
        assert res_next != ""


class TestVisionFallbackNoBlindClick:
    """Тестирование безопасного визуального анализа экрана без слепых кликов."""

    def test_fallback_click_with_vlm_coords(
        self,
        stability_app: tuple[GupkaCommanderApp, MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """При обнаружении координат через VLM выполняется клик в нужную точку."""
        app, _, _, mock_vision, mock_llm = stability_app
        mock_vision.inspect_screen.return_value = {"image_b64": "valid_base64"}
        mock_llm.plan_actions.return_value = [
            {"tool": "click_screen", "parameters": {"x": 640, "y": 480}}
        ]

        resp = app._fallback_click_with_vision("Кнопка ОК", "кликни Кнопка ОК")
        mock_vision.click_at.assert_called_once_with(640, 480, verify_change=False)
        assert "Vision Fallback" in resp
        assert "Кнопка ОК" in resp

    def test_fallback_click_without_vlm_coords_no_blind_click(
        self,
        stability_app: tuple[GupkaCommanderApp, MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Если координаты не найдены, клик вслепую (500, 500) НЕ производится."""
        app, _, _, mock_vision, mock_llm = stability_app
        mock_vision.inspect_screen.return_value = {"image_b64": "valid_base64"}
        # VLM не нашел кликабельный элемент
        mock_llm.plan_actions.return_value = []

        resp = app._fallback_click_with_vision("НевидимаяКнопка", "")
        mock_vision.click_at.assert_not_called()
        assert "не найден на экране через визуальный анализ" in resp
        assert "НевидимаяКнопка" in resp

    def test_fallback_click_no_screenshot(
        self,
        stability_app: tuple[GupkaCommanderApp, MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Если снимок экрана не получен, возвращается диагностика без клика."""
        app, _, _, mock_vision, _ = stability_app
        mock_vision.inspect_screen.side_effect = RuntimeError("Экран недоступен")

        resp = app._fallback_click_with_vision("Кнопка", "")
        mock_vision.click_at.assert_not_called()
        assert "не найден на экране через визуальный анализ" in resp

    def test_fallback_click_missing_coords_in_planned_action(
        self,
        stability_app: tuple[GupkaCommanderApp, MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Если VLM вернул инструмент click_screen без параметров x или y, клик не производится."""
        app, _, _, mock_vision, mock_llm = stability_app
        mock_vision.inspect_screen.return_value = {"image_b64": "valid_base64"}
        mock_llm.plan_actions.return_value = [{"tool": "click_screen", "parameters": {}}]

        resp = app._fallback_click_with_vision("КнопкаБезКоординат", "")
        mock_vision.click_at.assert_not_called()
        assert "не найден на экране через визуальный анализ" in resp

    def test_fallback_click_non_numeric_coords(
        self,
        stability_app: tuple[GupkaCommanderApp, MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Если координаты не числа, исключение перехватывается без сбоя и слепого клика."""
        app, _, _, mock_vision, mock_llm = stability_app
        mock_vision.inspect_screen.return_value = {"image_b64": "valid_base64"}
        mock_llm.plan_actions.return_value = [
            {"tool": "click_screen", "parameters": {"x": "не_число", "y": "не_число"}}
        ]

        resp = app._fallback_click_with_vision("БитыеКоординаты", "")
        mock_vision.click_at.assert_not_called()
        assert "не найден на экране через визуальный анализ" in resp


class TestRealMuteSupport:
    """Тестирование реального отключения микрофона (Mute)."""

    def test_handle_mute_toggled_delegation(
        self,
        stability_app: tuple[GupkaCommanderApp, MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Проверка вызова set_muted и остановки TTS при переключении mute."""
        app, _, mock_voice, _, _ = stability_app
        mock_synth = MagicMock()
        mock_voice.synthesizer = mock_synth

        app.handle_mute_toggled(True)
        mock_voice.set_muted.assert_called_once_with(True)
        mock_synth.stop.assert_called_once()

    def test_voice_pipeline_muted_behavior(self) -> None:
        """Проверка игнорирования входящих аудиочанков при mute в VoicePipeline."""
        mock_cap = MagicMock()
        mock_vad = MagicMock()
        mock_stt = MagicMock()
        mock_synth = MagicMock()
        level_events: list[float] = []

        pipeline = VoicePipeline(
            audio_capture=mock_cap,
            vad_detector=mock_vad,
            transcriber=mock_stt,
            synthesizer=mock_synth,
            on_audio_level=lambda lvl: level_events.append(lvl),
        )

        pipeline.set_muted(True)
        assert pipeline.is_muted()
        mock_synth.stop.assert_called_once()

        # Имитируем чтение чанка в _pipeline_loop при muted
        chunk = np.ones(512, dtype=np.float32) * 0.5
        mock_cap.read_chunk.return_value = chunk

        # Тестируем логику итерации напрямую через вызов внутренней обработки
        # Запускаем конвейер кратковременно
        pipeline.start()
        time.sleep(0.1)
        pipeline.stop()

        # VAD не должен вызываться для обработки речи при muted
        mock_vad.process_chunk.assert_not_called()
        # Уровень звука должен транслироваться как 0.0
        assert any(lvl == 0.0 for lvl in level_events)


class TestAudioLevelIndicator:
    """Тестирование проброса RMS уровня и привязки к WaveformWidget."""

    def test_waveform_widget_set_audio_level_slot(self) -> None:
        """Проверка слота set_audio_level в WaveformWidget."""
        widget = WaveformWidget()
        widget.set_audio_level(0.75)
        # Уровень должен установиться с учетом сглаживания
        assert widget._level > 0.0

    def test_voice_bridge_connects_to_spotlight_waveform(
        self,
        stability_app: tuple[GupkaCommanderApp, MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Проверка соединения voice_bridge.audio_level_received -> spotlight.waveform."""
        app, _, _, _, _ = stability_app
        waveform = app.spotlight.waveform

        with patch.object(waveform, "set_audio_level") as mock_set:
            app.voice_bridge.audio_level_received.emit(0.65)
            mock_set.assert_called_once_with(0.65)

    def test_on_audio_level_emits_bridge_signal(
        self,
        stability_app: tuple[GupkaCommanderApp, MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Проверка проброса аудиоуровня из _on_audio_level в voice_bridge."""
        app, _, _, _, _ = stability_app
        emitted_levels: list[float] = []
        app.voice_bridge.audio_level_received.connect(lambda lvl: emitted_levels.append(lvl))

        app._on_audio_level(0.42)
        assert 0.42 in emitted_levels
