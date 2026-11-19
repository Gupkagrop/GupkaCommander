"""
> [!NOTE]
> Модуль tests.test_gui_components: Комплексные интеграционные и компонентные тесты
> графического интерфейса PySide6 (Sprint 3) в headless-режиме (offscreen).
> Проверяет WaveformWidget, ChatDrawer, SpotlightBar, HotkeyManager, TrayManager
> и сквозную координацию главного приложения GupkaCommanderApp (main.py).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

# Гарантируем offscreen-платформу Qt до инициализации любого GUI компонента
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QImage, QKeyEvent
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from core.app_finder import AppFinder, AppInfo
from core.os_controller import OSController
from core.voice.voice_pipeline import PipelineState, VoicePipeline
from gui.chat_drawer import ChatDrawer, MessageCard
from gui.hotkey_manager import HotkeyManager
from gui.spotlight_bar import SpotlightBar
from gui.tray_icon import TrayManager
from gui.waveform_widget import WaveformWidget
from main import GupkaCommanderApp, main, parse_arguments


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Синглтон приложения QApplication для headless-тестирования."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def spotlight_bar(qapp: QApplication) -> Generator[SpotlightBar, None, None]:
    """Изолированная фикстура SpotlightBar для тестов."""
    bar = SpotlightBar(hide_on_focus_loss=False)
    yield bar
    bar.hide()
    bar.deleteLater()


@pytest.fixture
def tray_manager(qapp: QApplication) -> Generator[TrayManager, None, None]:
    """Изолированная фикстура TrayManager для тестов."""
    tray = TrayManager()
    yield tray
    tray.hide()
    tray.deleteLater()


class TestWaveformWidgetHeadless:
    """Тестирование состояний и визуализации виджета WaveformWidget."""

    def test_initial_state_and_size_hint(self, qapp: QApplication) -> None:
        """Проверка начального состояния, размеров и флагов виджета."""
        widget = WaveformWidget()
        assert widget.get_state() == "IDLE"
        hint = widget.sizeHint()
        assert isinstance(hint, QSize)
        assert hint.width() >= 48
        assert hint.height() >= 24
        assert widget.minimumWidth() >= 48
        assert widget.minimumHeight() >= 28
        widget.stop_animation()
        widget.deleteLater()

    def test_state_transitions_all_valid_and_fallback(self, qapp: QApplication) -> None:
        """Проверка перехода между всеми состояниями и отката при неизвестном значении."""
        widget = WaveformWidget()

        # Строковые значения
        widget.set_state("LISTENING")
        assert widget.get_state() == "LISTENING"

        widget.set_state("PROCESSING")
        assert widget.get_state() == "PROCESSING"

        widget.set_state("SPEAKING")
        assert widget.get_state() == "SPEAKING"

        widget.set_state("IDLE")
        assert widget.get_state() == "IDLE"

        # Enum PipelineState
        widget.set_state(PipelineState.LISTENING)
        assert widget.get_state() == "LISTENING"

        widget.set_state(PipelineState.SPEAKING)
        assert widget.get_state() == "SPEAKING"

        # Неизвестное состояние -> откат к IDLE
        widget.set_state("INVALID_STATE_XYZ")
        assert widget.get_state() == "IDLE"

        widget.stop_animation()
        widget.deleteLater()

    def test_level_clamping_and_smoothing(self, qapp: QApplication) -> None:
        """Проверка ограничения и экспоненциального сглаживания амплитуды."""
        widget = WaveformWidget()

        # Нормальное значение
        widget.set_level(0.6)
        assert widget._target_level == 0.6
        assert 0.0 <= widget._level <= 0.6

        # Превышение верхнего порога
        widget.set_level(5.0)
        assert widget._target_level == 1.0

        # Превышение нижнего порога
        widget.set_level(-2.5)
        assert widget._target_level == 0.0

        widget.stop_animation()
        widget.deleteLater()

    def test_animation_tick_and_lifecycle(self, qapp: QApplication) -> None:
        """Проверка шага таймера анимации и управления жизненным циклом."""
        widget = WaveformWidget()
        assert widget._timer.isActive()

        initial_phase = widget._phase
        widget._level = 0.8
        widget._on_animation_tick()

        # Фаза должна увеличиться, а уровень затухнуть
        assert widget._phase > initial_phase
        assert widget._level < 0.8

        # Остановка и запуск
        widget.stop_animation()
        assert not widget._timer.isActive()
        widget.start_animation()
        assert widget._timer.isActive()

        widget.close()
        assert not widget._timer.isActive()
        widget.deleteLater()

    def test_hide_and_show_events_control_timer(self, qapp: QApplication) -> None:
        """Проверка остановки таймера анимации при hideEvent и возобновления при showEvent."""
        widget = WaveformWidget()
        widget.show()
        assert widget._timer.isActive()

        widget.hide()
        assert not widget._timer.isActive()

        widget.show()
        assert widget._timer.isActive()

        widget.stop_animation()
        widget.deleteLater()

    def test_painting_all_states_on_image(self, qapp: QApplication) -> None:
        """Проверка рендеринга каждого из 4 состояний на виртуальный QImage."""
        widget = WaveformWidget()
        widget.resize(64, 32)

        states = ("IDLE", "LISTENING", "PROCESSING", "SPEAKING")
        for state in states:
            widget.set_state(state)
            widget.set_level(0.7)

            image = QImage(64, 32, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            widget.render(image)

            assert not image.isNull()
            assert image.width() == 64
            assert image.height() == 32

        widget.stop_animation()
        widget.deleteLater()


class TestChatDrawerHeadless:
    """Тестирование выдвижной панели истории диалога ChatDrawer и карточек MessageCard."""

    def test_message_card_properties_and_roles(self, qapp: QApplication) -> None:
        """Проверка создания карточки сообщений для разных ролей и статусов."""
        roles = ("user", "assistant", "action", "system")
        for role in roles:
            card = MessageCard(role=role, text=f"Текст для {role}", status="running")
            assert card.get_role() == role
            assert card.get_status() == "running"
            assert card.get_text() == f"Текст для {role}"

            # Обновление свойств
            card.set_text("Обновленный текст")
            assert card.get_text() == "Обновленный текст"

            card.set_status("done")
            assert card.get_status() == "done"
            card.deleteLater()

    def test_chat_drawer_initial_state(self, qapp: QApplication) -> None:
        """Проверка исходного сложенного состояния ChatDrawer."""
        drawer = ChatDrawer(target_height=250)
        assert not drawer.is_expanded()
        assert drawer.maximumHeight() == 0
        assert drawer.get_messages_count() == 0
        drawer.deleteLater()

    def test_add_messages_and_clear(self, qapp: QApplication) -> None:
        """Проверка наполнения истории сообщениями и последующей очистки."""
        drawer = ChatDrawer()

        c1 = drawer.add_message("user", "Запусти калькулятор", status="done")
        c2 = drawer.add_message("action", "Выполнение calc.exe", status="running")
        c3 = drawer.add_message("assistant", "Калькулятор запущен", status="done")

        assert drawer.get_messages_count() == 3
        assert isinstance(c1, MessageCard)
        assert c2.get_status() == "running"
        assert c3.get_role() == "assistant"

        # Очистка
        drawer.clear()
        assert drawer.get_messages_count() == 0
        drawer.deleteLater()

    def test_drawer_smooth_expansion_toggle(self, qapp: QApplication) -> None:
        """Проверка логики переключения раскрытия панели."""
        drawer = ChatDrawer(target_height=300)

        # Раскрытие
        drawer.toggle(expand=True)
        assert drawer.is_expanded()
        assert drawer._animation.endValue() == 300

        # Сворачивание
        drawer.toggle(expand=False)
        assert not drawer.is_expanded()
        assert drawer._animation.endValue() == 0

        # Инверсия (toggle без аргументов)
        drawer.toggle()
        assert drawer.is_expanded()
        assert drawer._animation.endValue() == 300

        # Повторный вызов с тем же состоянием не должен перезапускать анимацию
        drawer.toggle(expand=True)
        assert drawer.is_expanded()
        drawer.deleteLater()


class TestSpotlightBarHeadless:
    """Тестирование поведения плавающего окна ввода SpotlightBar."""

    def test_window_flags_and_attributes(self, spotlight_bar: SpotlightBar) -> None:
        """Проверка флагов безрамочного окна поверх всех окон."""
        flags = spotlight_bar.windowFlags()
        assert bool(flags & Qt.WindowType.FramelessWindowHint)
        assert bool(flags & Qt.WindowType.WindowStaysOnTopHint)
        assert bool(flags & Qt.WindowType.Tool)
        assert spotlight_bar.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert spotlight_bar.width() == SpotlightBar.DEFAULT_WIDTH

    def test_child_components_existence(self, spotlight_bar: SpotlightBar) -> None:
        """Проверка инициализации всех дочерних элементов управления."""
        assert spotlight_bar.search_input is not None
        assert spotlight_bar.waveform is not None
        assert spotlight_bar.chat_drawer is not None
        assert spotlight_bar._search_icon is not None
        assert spotlight_bar._status_badge is not None
        assert spotlight_bar._toggle_drawer_btn is not None

    def test_text_input_and_clear(self, spotlight_bar: SpotlightBar) -> None:
        """Проверка установки, чтения и очистки поля ввода."""
        spotlight_bar.set_input_text("Привет, Губка!")
        assert spotlight_bar.get_input_text() == "Привет, Губка!"

        spotlight_bar.clear_input()
        assert spotlight_bar.get_input_text() == ""

    def test_command_submitted_signal_on_enter(self, spotlight_bar: SpotlightBar) -> None:
        """Проверка отправки команды при непустом вводе и игнорирования пустых строк."""
        emitted_commands: list[str] = []
        spotlight_bar.command_submitted.connect(emitted_commands.append)

        # Пустой ввод не отправляется
        spotlight_bar.set_input_text("   ")
        spotlight_bar._on_enter_pressed()
        assert len(emitted_commands) == 0

        # Корректная команда
        spotlight_bar.set_input_text("открой терминал")
        spotlight_bar._on_enter_pressed()
        assert len(emitted_commands) == 1
        assert emitted_commands[0] == "открой терминал"

    def test_escape_key_hides_window(self, spotlight_bar: SpotlightBar) -> None:
        """Проверка скрытия окна при нажатии клавиши Escape."""
        spotlight_bar.show_bar()
        assert spotlight_bar.isVisible()

        esc_event = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier,
        )
        spotlight_bar.keyPressEvent(esc_event)
        assert not spotlight_bar.isVisible()

    def test_toggle_visibility_and_signals(self, spotlight_bar: SpotlightBar) -> None:
        """Проверка переключения видимости и отправки visibility_changed."""
        visibility_events: list[bool] = []
        spotlight_bar.visibility_changed.connect(visibility_events.append)

        spotlight_bar.hide_bar()
        assert not spotlight_bar.isVisible()

        spotlight_bar.toggle_visibility()
        assert spotlight_bar.isVisible()
        assert visibility_events[-1] is True

        spotlight_bar.toggle_visibility()
        assert not spotlight_bar.isVisible()
        assert visibility_events[-1] is False

    def test_focus_loss_handling(self, spotlight_bar: SpotlightBar) -> None:
        """Проверка автоскрытия окна при потере фокуса при включенной опции."""
        spotlight_bar.set_hide_on_focus_loss(True)
        assert spotlight_bar.get_hide_on_focus_loss() is True

        spotlight_bar.show_bar()
        # Симулируем событие потери активности окна
        change_event = QEvent(QEvent.Type.ActivationChange)
        with patch.object(spotlight_bar, "isActiveWindow", return_value=False):
            spotlight_bar.changeEvent(change_event)

        assert not spotlight_bar.isVisible()

    def test_state_and_level_updates(self, spotlight_bar: SpotlightBar) -> None:
        """Проверка трансляции состояния и уровня амплитуды во внутренние виджеты."""
        spotlight_bar.set_pipeline_state(PipelineState.SPEAKING)
        assert spotlight_bar.waveform.get_state() == "SPEAKING"
        assert spotlight_bar._status_badge.text() == "SPEAKING"

        spotlight_bar.set_audio_level(0.85)
        assert spotlight_bar.waveform._target_level == 0.85

    def test_add_chat_message_auto_expands_drawer(self, spotlight_bar: SpotlightBar) -> None:
        """Проверка добавления карточки и автоматического раскрытия ящика истории."""
        assert not spotlight_bar.chat_drawer.is_expanded()

        spotlight_bar.add_chat_message("user", "Привет!", status="done")
        assert spotlight_bar.chat_drawer.get_messages_count() == 1
        assert spotlight_bar.chat_drawer.is_expanded()


class TestHotkeyManagerHeadless:
    """Тестирование диспетчера глобальных горячих клавиш HotkeyManager."""

    def test_initial_state(self, qapp: QApplication) -> None:
        """Проверка исходных параметров менеджера хоткеев."""
        manager = HotkeyManager()
        assert not manager.is_active
        assert manager.backend_name == "none"

    def test_callback_and_signal_emission(self, qapp: QApplication) -> None:
        """Проверка срабатывания сигнала и прямого коллбэка при триггере хоткея."""
        callback_called = [False]
        signal_called = [False]

        def on_callback() -> None:
            callback_called[0] = True

        def on_signal() -> None:
            signal_called[0] = True

        manager = HotkeyManager(callback=on_callback)
        manager.hotkey_pressed.connect(on_signal)

        manager._on_hotkey_triggered()
        assert callback_called[0] is True
        assert signal_called[0] is True

    def test_win32_success_registration(self, qapp: QApplication) -> None:
        """Проверка успешной регистрации через Win32 API."""
        manager = HotkeyManager()
        with (
            patch("platform.system", return_value="Windows"),
            patch.object(manager, "_start_win32", return_value=True),
        ):
            ok = manager.start()
            assert ok is True
            assert manager.is_active is True
            assert manager.backend_name == "win32"

            # Повторный запуск возвращает True без повторной инициализации
            assert manager.start() is True

        manager.stop()
        assert not manager.is_active
        assert manager.backend_name == "none"

    def test_pynput_fallback_on_win32_failure(self, qapp: QApplication) -> None:
        """Проверка автоматического отката на pynput при недоступности Win32."""
        manager = HotkeyManager()
        with (
            patch.object(manager, "_start_win32", return_value=False),
            patch.object(manager, "_start_pynput", return_value=True),
        ):
            ok = manager.start()
            assert ok is True
            assert manager.is_active is True
            assert manager.backend_name == "pynput"

        manager.stop()
        assert not manager.is_active
        assert manager.backend_name == "none"

    def test_both_backends_fail(self, qapp: QApplication) -> None:
        """Проверка сценария, когда оба бэкенда недоступны."""
        manager = HotkeyManager()
        with (
            patch.object(manager, "_start_win32", return_value=False),
            patch.object(manager, "_start_pynput", return_value=False),
        ):
            ok = manager.start()
            assert ok is False
            assert not manager.is_active
            assert manager.backend_name == "none"


class TestTrayManagerHeadless:
    """Тестирование диспетчера системного трея TrayManager."""

    def test_menu_actions_and_signals(self, tray_manager: TrayManager) -> None:
        """Проверка наличия действий меню и срабатывания сигналов."""
        assert tray_manager.show_hide_action.text() == "Показать / Скрыть"
        assert tray_manager.mute_action.text() == "Отключить микрофон"
        assert tray_manager.mute_action.isCheckable()
        assert tray_manager.settings_action.text() == "Настройки..."
        assert tray_manager.quit_action.text() == "Выход"

        show_hide_called = [False]
        settings_called = [False]
        quit_called = [False]

        tray_manager.show_hide_triggered.connect(lambda: show_hide_called.__setitem__(0, True))
        tray_manager.settings_triggered.connect(lambda: settings_called.__setitem__(0, True))
        tray_manager.quit_requested.connect(lambda: quit_called.__setitem__(0, True))

        tray_manager.show_hide_action.trigger()
        assert show_hide_called[0] is True

        tray_manager.settings_action.trigger()
        assert settings_called[0] is True

        # Проверка клика по иконке трея
        show_hide_called[0] = False
        tray_manager._on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
        assert show_hide_called[0] is True

        # Проверка выхода
        with patch.object(QApplication, "quit"):
            tray_manager.quit_action.trigger()
            assert quit_called[0] is True

    def test_mute_action_and_state(self, tray_manager: TrayManager) -> None:
        """Проверка переключения заглушения микрофона."""
        mute_events: list[bool] = []
        tray_manager.mute_toggled.connect(mute_events.append)

        tray_manager.set_muted(True)
        assert tray_manager.is_muted() is True
        assert tray_manager.mute_action.isChecked() is True

        tray_manager.set_muted(False)
        assert tray_manager.is_muted() is False
        assert tray_manager.mute_action.isChecked() is False

    def test_state_change_and_icon_tooltip(self, tray_manager: TrayManager) -> None:
        """Проверка обновления иконки и тултипа для всех состояний конвейера."""
        states = ("IDLE", "LISTENING", "PROCESSING", "SPEAKING")
        for st in states:
            tray_manager.set_state(st)
            assert tray_manager.get_state() == st
            assert "GupkaCommander" in tray_manager.tray_icon.toolTip()

    def test_show_hide_and_messages(self, tray_manager: TrayManager) -> None:
        """Проверка методов отображения и уведомлений."""
        tray_manager.show()
        tray_manager.hide()
        tray_manager.show_message("Заголовок", "Сообщение")


class TestMainApplicationWiringHeadless:
    """Тестирование главного контроллера приложения GupkaCommanderApp."""

    def test_end_to_end_command_dispatch(
        self,
        qapp: QApplication,
        tmp_path: Path,
    ) -> None:
        """Проверка полного цикла маршрутизации команд и вызова компонентов."""
        mock_os = MagicMock(spec=OSController)
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

        # 1. Веб-алиас
        mock_os.resolve_alias.return_value = "https://youtube.com"
        resp_web = app_ctrl.execute_command("youtube")
        mock_os.open_alias.assert_called_once_with("youtube")
        assert "youtube" in resp_web.lower()
        mock_voice.speak.assert_called_once_with(resp_web, blocking=False)

        # 2. Поисковый запрос
        mock_os.reset_mock()
        mock_voice.reset_mock()
        mock_os.resolve_alias.return_value = None
        resp_search = app_ctrl.execute_command("найди в google погоду")
        mock_os.search.assert_called_once_with("погоду")
        assert "погоду" in resp_search

        # 3. Системные действия: громкость и терминал
        mock_os.reset_mock()
        resp_vol = app_ctrl.execute_command("прибавь звук")
        mock_os.volume_up.assert_called_once()
        assert "увеличена" in resp_vol

        mock_os.reset_mock()
        resp_term = app_ctrl.execute_command("терминал")
        mock_os.open_terminal.assert_called_once()
        assert "терминала" in resp_term

        # 4. Запуск приложения через AppFinder
        mock_os.reset_mock()
        fake_app = AppInfo(name="Калькулятор", path=r"C:\Windows\calc.exe", source="test")
        mock_finder.find.return_value = [fake_app]

        with patch.object(app_ctrl, "_launch_app", return_value=True) as mock_launch:
            resp_app = app_ctrl.execute_command("открой калькулятор")
            assert "Калькулятор" in resp_app
            mock_launch.assert_called_once_with(fake_app.path)

        # 5. Резервный поиск в поисковой системе
        mock_os.reset_mock()
        mock_finder.find.return_value = []
        resp_fallback = app_ctrl.execute_command("неизвестная абракадабра")
        mock_os.search.assert_called_once_with("неизвестная абракадабра")
        assert "абракадабра" in resp_fallback

        app_ctrl.shutdown()
        mock_bar.deleteLater()
        mock_tray.deleteLater()

    def test_voice_bridge_signals_and_mute(
        self,
        qapp: QApplication,
    ) -> None:
        """Проверка связи VoiceBridge с SpotlightBar и реакции на Mute."""
        mock_os = MagicMock(spec=OSController)
        mock_voice = MagicMock(spec=VoicePipeline)
        mock_synth = MagicMock()
        mock_voice.synthesizer = mock_synth

        mock_bar = SpotlightBar(hide_on_focus_loss=False)
        mock_tray = TrayManager()
        mock_hotkey = HotkeyManager()

        app_ctrl = GupkaCommanderApp(
            app=qapp,
            app_finder=MagicMock(spec=AppFinder),
            os_controller=mock_os,
            voice_pipeline=mock_voice,
            spotlight_bar=mock_bar,
            tray_manager=mock_tray,
            hotkey_manager=mock_hotkey,
            enable_voice=True,
            enable_hotkey=False,
            enable_tray=False,
        )

        # Сигнал изменения состояния
        app_ctrl.voice_bridge.state_changed.emit("LISTENING")
        assert mock_bar.waveform.get_state() == "LISTENING"
        assert mock_tray.get_state() == "LISTENING"

        # Сигнал уровня аудио
        app_ctrl.voice_bridge.audio_level_received.emit(0.75)
        assert mock_bar.waveform._target_level == 0.75

        # Заглушение звука через трей останавливает синтезатор
        mock_tray.set_muted(True)
        mock_synth.stop.assert_called_once()

        # Распознавание транскрипции
        with patch.object(app_ctrl, "execute_command") as mock_exec:
            app_ctrl.voice_bridge.transcription_received.emit("запусти браузер")
            mock_exec.assert_called_once_with("запусти браузер")
            assert mock_bar.get_input_text() == "запусти браузер"

        app_ctrl.shutdown()
        mock_bar.deleteLater()
        mock_tray.deleteLater()

    def test_app_lifecycle_start_and_shutdown(
        self,
        qapp: QApplication,
    ) -> None:
        """Проверка запуска и корректного завершения всех подсистем."""
        mock_voice = MagicMock(spec=VoicePipeline)
        mock_hotkey = MagicMock(spec=HotkeyManager)
        mock_tray = MagicMock(spec=TrayManager)
        mock_bar = MagicMock(spec=SpotlightBar)

        app_ctrl = GupkaCommanderApp(
            app=qapp,
            app_finder=MagicMock(spec=AppFinder),
            os_controller=MagicMock(spec=OSController),
            voice_pipeline=mock_voice,
            spotlight_bar=mock_bar,
            tray_manager=mock_tray,
            hotkey_manager=mock_hotkey,
            enable_voice=True,
            enable_hotkey=True,
            enable_tray=True,
        )

        # Start
        app_ctrl.start()
        mock_tray.show.assert_called_once()
        mock_hotkey.start.assert_called_once()
        mock_voice.start.assert_called_once()
        mock_bar.show_bar.assert_called_once()

        # Shutdown
        app_ctrl.shutdown()
        mock_hotkey.stop.assert_called_once()
        mock_voice.stop.assert_called_once()
        mock_bar.hide_bar.assert_called_once()
        mock_tray.hide.assert_called_once()

    def test_cli_argument_parsing(self) -> None:
        """Проверка разбора аргументов командной строки."""
        test_args = ["main.py", "--no-voice", "--no-hotkey", "--no-tray", "--headless"]
        with patch.object(sys, "argv", test_args):
            args = parse_arguments()
            assert args.no_voice is True
            assert args.no_hotkey is True
            assert args.no_tray is True
            assert args.headless is True

    def test_main_function_headless_exit(self) -> None:
        """Проверка безопасного запуска и немедленного выхода функции main() в headless-режиме."""
        test_args = ["main.py", "--headless", "--no-voice", "--no-hotkey", "--no-tray"]
        with (
            patch.object(sys, "argv", test_args),
            patch("main.VoicePipeline"),
        ):
            exit_code = main()
            assert exit_code == 0
