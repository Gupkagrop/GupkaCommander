"""
> [!NOTE]
> Модуль тестов tests.test_spotlight_app: Проверка модулей SpotlightBar, TrayManager,
> HotkeyManager и интеграции точки входа main.py в headless-режиме PySide6.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

# Устанавливаем offscreen платформу до инициализации Qt
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from core.app_finder import AppFinder, AppInfo
from core.os_controller import OSController
from core.uia import UIAutomationManager
from core.vision.vision_agent import VisionAgent
from core.voice.voice_pipeline import PipelineState, VoicePipeline
from gui.hotkey_manager import HotkeyManager
from gui.spotlight_bar import SpotlightBar
from gui.tray_icon import TrayManager
from main import GupkaCommanderApp


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Синглтон приложения QApplication для headless-тестирования."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def spotlight_bar(qapp: QApplication) -> Generator[SpotlightBar, None, None]:
    """Фикстура окна ввода SpotlightBar."""
    bar = SpotlightBar(hide_on_focus_loss=False)
    yield bar
    bar.hide()
    bar.deleteLater()


@pytest.fixture
def tray_manager(qapp: QApplication) -> Generator[TrayManager, None, None]:
    """Фикстура системного трея TrayManager."""
    tray = TrayManager()
    yield tray
    tray.hide()
    tray.deleteLater()


class TestHotkeyManager:
    """Тестирование глобального менеджера горячих клавиш HotkeyManager."""

    def test_initial_state(self, qapp: QApplication) -> None:
        """Проверка начального состояния менеджера хоткеев."""
        manager = HotkeyManager()
        assert not manager.is_active
        assert manager.backend_name == "none"

    def test_hotkey_trigger_signal_and_callback(self, qapp: QApplication) -> None:
        """Проверка срабатывания сигнала и прямого обратного вызова при нажатии."""
        callback_called = [False]
        signal_emitted = [False]

        def on_callback() -> None:
            callback_called[0] = True

        def on_signal() -> None:
            signal_emitted[0] = True

        manager = HotkeyManager(callback=on_callback)
        manager.hotkey_pressed.connect(on_signal)

        manager._on_hotkey_triggered()
        assert callback_called[0] is True
        assert signal_emitted[0] is True

    def test_pynput_fallback_when_win32_fails(self, qapp: QApplication) -> None:
        """Проверка переключения на pynput при неудаче регистрации через Win32 API."""
        manager = HotkeyManager()
        with (
            patch.object(manager, "_start_win32", return_value=False),
            patch.object(manager, "_start_pynput", return_value=True),
        ):
            success = manager.start()
            assert success is True
            assert manager.is_active is True
            assert manager.backend_name == "pynput"

        manager.stop()
        assert not manager.is_active
        assert manager.backend_name == "none"

    def test_win32_success_registration(self, qapp: QApplication) -> None:
        """Проверка успешной регистрации через Win32 API."""
        manager = HotkeyManager()
        with (
            patch("platform.system", return_value="Windows"),
            patch.object(manager, "_start_win32", return_value=True),
        ):
            success = manager.start()
            assert success is True
            assert manager.is_active is True
            assert manager.backend_name == "win32"

        manager.stop()
        assert not manager.is_active


class TestSpotlightBar:
    """Тестирование плавающего окна ввода SpotlightBar."""

    def test_window_flags_and_properties(self, spotlight_bar: SpotlightBar) -> None:
        """Проверка флагов окна и полупрозрачного фона."""
        flags = spotlight_bar.windowFlags()
        assert bool(flags & Qt.WindowType.FramelessWindowHint)
        assert bool(flags & Qt.WindowType.WindowStaysOnTopHint)
        assert bool(flags & Qt.WindowType.Tool)
        assert spotlight_bar.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def test_child_widgets_presence(self, spotlight_bar: SpotlightBar) -> None:
        """Проверка наличия всех ключевых элементов управления."""
        assert spotlight_bar.search_input is not None
        assert spotlight_bar.waveform is not None
        assert spotlight_bar.chat_drawer is not None
        assert spotlight_bar.search_input.placeholderText() != ""

    def test_command_submitted_on_enter(self, spotlight_bar: SpotlightBar) -> None:
        """Проверка отправки команды по нажатию Enter."""
        submitted_text = [""]

        def on_submit(text: str) -> None:
            submitted_text[0] = text

        spotlight_bar.command_submitted.connect(on_submit)
        spotlight_bar.set_input_text("запусти блокнот")
        spotlight_bar._on_enter_pressed()

        assert submitted_text[0] == "запусти блокнот"

    def test_escape_key_hides_window(self, spotlight_bar: SpotlightBar) -> None:
        """Проверка скрытия окна по клавише Escape."""
        spotlight_bar.show_bar()
        assert spotlight_bar.isVisible()

        esc_event = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier,
        )
        spotlight_bar.keyPressEvent(esc_event)
        assert not spotlight_bar.isVisible()

    def test_pipeline_state_and_level_updates(self, spotlight_bar: SpotlightBar) -> None:
        """Проверка обновления визуализатора волн и бейджа статуса."""
        spotlight_bar.set_pipeline_state(PipelineState.LISTENING)
        assert spotlight_bar.waveform.get_state() == "LISTENING"

        spotlight_bar.set_audio_level(0.75)
        assert spotlight_bar.waveform._level > 0.0

    def test_chat_drawer_embedding_and_messaging(self, spotlight_bar: SpotlightBar) -> None:
        """Проверка добавления карточек в ChatDrawer через SpotlightBar."""
        drawer = spotlight_bar.chat_drawer
        initial_count = drawer.get_messages_count()

        spotlight_bar.add_chat_message("user", "Привет", status="done")
        assert drawer.get_messages_count() == initial_count + 1

        spotlight_bar.toggle_chat_drawer(expand=False)
        assert not drawer.is_expanded()
        spotlight_bar.toggle_chat_drawer(expand=True)
        assert drawer.is_expanded()

    def test_toggle_visibility(self, spotlight_bar: SpotlightBar) -> None:
        """Проверка переключения видимости окна."""
        spotlight_bar.hide_bar()
        assert not spotlight_bar.isVisible()

        spotlight_bar.toggle_visibility()
        assert spotlight_bar.isVisible()

        spotlight_bar.toggle_visibility()
        assert not spotlight_bar.isVisible()


class TestTrayManager:
    """Тестирование системного трея TrayManager."""

    def test_menu_actions_exist(self, tray_manager: TrayManager) -> None:
        """Проверка наличия всех требуемых пунктов контекстного меню."""
        assert tray_manager.show_hide_action.text() == "Показать / Скрыть"
        assert tray_manager.mute_action.text() == "Отключить микрофон"
        assert tray_manager.mute_action.isCheckable()
        assert tray_manager.settings_action.text() == "Настройки..."
        assert tray_manager.quit_action.text() == "Выход"

    def test_mute_toggle_signal(self, tray_manager: TrayManager) -> None:
        """Проверка срабатывания сигнала при отключении микрофона."""
        mute_states: list[bool] = []
        tray_manager.mute_toggled.connect(mute_states.append)

        tray_manager.set_muted(True)
        assert tray_manager.is_muted() is True
        assert tray_manager.mute_action.isChecked() is True

        tray_manager.set_muted(False)
        assert tray_manager.is_muted() is False
        assert tray_manager.mute_action.isChecked() is False

    def test_state_change_updates(self, tray_manager: TrayManager) -> None:
        """Проверка смены состояния и обновления подсказки."""
        tray_manager.set_state(PipelineState.SPEAKING)
        assert tray_manager.get_state() == "SPEAKING"
        assert "Воспроизведение" in tray_manager.tray_icon.toolTip()


class TestMainApplicationController:
    """Тестирование главного контроллера GupkaCommanderApp."""

    def test_full_wiring_and_command_execution(
        self,
        qapp: QApplication,
        tmp_path: Path,
    ) -> None:
        """Проверка связывания компонентов и выполнения команд."""
        # Мокируем сервисы для детерминированного тестирования
        mock_os = MagicMock(spec=OSController)
        mock_os.resolve_alias.return_value = "https://youtube.com"

        mock_voice = MagicMock(spec=VoicePipeline)
        mock_finder = MagicMock(spec=AppFinder)
        mock_bar = SpotlightBar(hide_on_focus_loss=False)
        mock_tray = TrayManager()
        mock_hotkey = HotkeyManager()

        app_ctrl = GupkaCommanderApp(
            app=qapp,
            app_finder=mock_finder,
            os_controller=mock_os,
            voice_pipeline=mock_voice,
            spotlight_bar=mock_bar,
            tray_manager=mock_tray,
            hotkey_manager=mock_hotkey,
            enable_voice=True,
            enable_hotkey=False,
            enable_tray=False,
        )

        # 1. Тестируем команду веб-алиаса
        response = app_ctrl.execute_command("youtube")
        mock_os.open_alias.assert_called_once_with("youtube")
        assert "youtube" in response.lower()
        mock_voice.speak.assert_called_once_with(response, blocking=False)

        # 2. Тестируем системную команду громкости
        mock_voice.reset_mock()
        vol_resp = app_ctrl.execute_command("громкость выше")
        mock_os.volume_up.assert_called_once()
        assert "увеличена" in vol_resp

        # 3. Тестируем запуск приложения через AppFinder
        mock_os.resolve_alias.return_value = None
        fake_app = AppInfo(name="Блокнот", path=r"C:\Windows\notepad.exe", source="test")
        mock_finder.find.return_value = [fake_app]

        with patch.object(app_ctrl, "_launch_app", return_value=True) as mock_launch:
            app_resp = app_ctrl.execute_command("открой блокнот")
            assert "Блокнот" in app_resp
            mock_launch.assert_called_once_with(fake_app.path)

        # 4. Тестируем голосовой мост VoiceBridge
        app_ctrl.voice_bridge.state_changed.emit("SPEAKING")
        assert mock_bar.waveform.get_state() == "SPEAKING"
        assert mock_tray.get_state() == "SPEAKING"

        # 5. Тестируем хоткей
        assert not mock_bar.isVisible()
        mock_hotkey.hotkey_pressed.emit()
        assert mock_bar.isVisible()

        # Очистка
        app_ctrl.shutdown()


class TestGupkaCommanderAppUIAutomation:
    """Тестирование интеграции Microsoft UI Automation и каскадного Vision Fallback."""

    @pytest.fixture
    def app_with_uia(
        self, qapp: QApplication
    ) -> Generator[tuple[GupkaCommanderApp, MagicMock, MagicMock], None, None]:
        """Фикстура приложения GupkaCommanderApp с моками UIA и Vision."""
        mock_finder = MagicMock(spec=AppFinder)
        mock_os = MagicMock(spec=OSController)
        mock_voice = MagicMock(spec=VoicePipeline)
        mock_bar = SpotlightBar(hide_on_focus_loss=False)
        mock_tray = TrayManager()
        mock_hotkey = HotkeyManager()
        mock_uia = MagicMock(spec=UIAutomationManager)
        mock_vision = MagicMock(spec=VisionAgent)

        app_ctrl = GupkaCommanderApp(
            app=qapp,
            app_finder=mock_finder,
            os_controller=mock_os,
            voice_pipeline=mock_voice,
            spotlight_bar=mock_bar,
            tray_manager=mock_tray,
            hotkey_manager=mock_hotkey,
            uia_manager=mock_uia,
            vision_agent=mock_vision,
            enable_voice=False,
            enable_hotkey=False,
            enable_tray=False,
        )
        yield app_ctrl, mock_uia, mock_vision
        app_ctrl.shutdown()

    def test_uia_click_element_success(
        self, app_with_uia: tuple[GupkaCommanderApp, MagicMock, MagicMock]
    ) -> None:
        """Проверка успешного вызова click_element через UIA."""
        app, mock_uia, mock_vision = app_with_uia
        mock_uia.click_element.return_value = True

        resp = app.execute_command("нажми кнопку Сохранить в окне Блокнот")
        mock_uia.click_element.assert_called_once_with("Сохранить", window_title="Блокнот")
        assert "Нажат элемент" in resp
        assert "Сохранить" in resp
        assert "Блокнот" in resp
        mock_vision.click_at.assert_not_called()

    def test_uia_cascading_fallback_to_vision(
        self, app_with_uia: tuple[GupkaCommanderApp, MagicMock, MagicMock]
    ) -> None:
        """Проверка каскадного fallback на VisionAgent при отсутствии элемента в UIA."""
        app, mock_uia, mock_vision = app_with_uia
        mock_uia.click_element.return_value = False
        mock_vision.inspect_screen.return_value = {"image_b64": "fake"}

        resp = app.execute_command("нажми кнопку Несуществующая")
        mock_uia.click_element.assert_called_once_with("Несуществующая", window_title=None)
        mock_vision.click_at.assert_called_once()
        assert "Vision Fallback" in resp

    def test_uia_type_into_element(
        self, app_with_uia: tuple[GupkaCommanderApp, MagicMock, MagicMock]
    ) -> None:
        """Проверка ввода текста через UIA."""
        app, mock_uia, _ = app_with_uia
        mock_uia.type_into_element.return_value = True

        resp = app.execute_command("введи в поле Логин текст admin")
        mock_uia.type_into_element.assert_called_once_with("Логин", "admin", window_title=None)
        assert "Введен текст" in resp

    def test_uia_toggle_state(
        self, app_with_uia: tuple[GupkaCommanderApp, MagicMock, MagicMock]
    ) -> None:
        """Проверка переключения CheckBox через UIA."""
        app, mock_uia, _ = app_with_uia
        mock_uia.set_toggle_state.return_value = True

        resp = app.execute_command("поставь галочку Автосохранение")
        mock_uia.set_toggle_state.assert_called_once_with("Автосохранение", True, window_title=None)
        assert "включен" in resp

    def test_execute_planned_tool_uia(
        self, app_with_uia: tuple[GupkaCommanderApp, MagicMock, MagicMock]
    ) -> None:
        """Проверка прямого вызова UIA-инструментов через _execute_planned_tool."""
        app, mock_uia, _ = app_with_uia
        mock_uia.click_element.return_value = True
        mock_uia.type_into_element.return_value = True
        mock_uia.inspect_window_elements.return_value = [{"Name": "Btn"}]

        res_click = app._execute_planned_tool(
            "click_ui_element",
            {"element_name": "OK", "window_title": "Calc", "control_type": "Button"},
        )
        assert "Нажат элемент" in res_click

        res_type = app._execute_planned_tool(
            "type_into_ui_element",
            {"element_name": "Search", "text": "Python", "window_title": "Browser"},
        )
        assert "Введен текст" in res_type

        res_inspect = app._execute_planned_tool(
            "inspect_ui_elements",
            {"window_title": "Calc"},
        )
        assert "Найдено 1 элементов" in res_inspect

