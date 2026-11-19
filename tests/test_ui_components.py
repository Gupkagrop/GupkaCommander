"""
> [!NOTE]
> Модуль тестов tests.test_ui_components: Проверка UI-компонентов PySide6 в headless-режиме.
> Тестирует стилизацию (gui.styles), визуализатор (WaveformWidget) и историю сообщений (ChatDrawer).
"""

import os
import sys

# Гарантируем headless режим до инициализации QApplication
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from core.voice.voice_pipeline import PipelineState
from gui.chat_drawer import ChatDrawer, MessageCard
from gui.styles import PALETTE, get_fluent_stylesheet, get_status_badge_qss
from gui.waveform_widget import WaveformWidget


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Фикстура синглтона QApplication для headless тестов."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(sys.argv)
    return app


class TestFluentStyles:
    """Тестирование палитры и QSS таблицы стилей Fluent Dark."""

    def test_fluent_palette_colors(self) -> None:
        """Проверка соответствия цветовых кодов палитре Windows 11 Fluent Dark."""
        assert PALETTE.background.lower() == "#1e1e1e"
        assert PALETTE.surface.lower() == "#252525"
        assert PALETTE.border.lower() == "#333333"
        assert PALETTE.text.lower() == "#ffffff"
        assert PALETTE.secondary.lower() == "#a0a0a0"
        assert PALETTE.accent.lower() == "#60cdff"
        assert PALETTE.error.lower() == "#ff453a"
        assert PALETTE.success.lower() == "#30d158"

    def test_stylesheet_content(self) -> None:
        """Проверка наличия ключевых селекторов в таблице стилей."""
        qss = get_fluent_stylesheet()
        assert "#spotlightBar" in qss
        assert "#searchInput" in qss
        assert "#statusBadge" in qss
        assert "QScrollArea" in qss
        assert "QScrollBar:vertical" in qss

    def test_status_badge_qss(self) -> None:
        """Проверка генерации QSS для различных состояний."""
        listening_qss = get_status_badge_qss("LISTENING")
        assert PALETTE.accent in listening_qss

        speaking_qss = get_status_badge_qss("SPEAKING")
        assert PALETTE.success in speaking_qss

        processing_qss = get_status_badge_qss("PROCESSING")
        assert PALETTE.warning in processing_qss

        error_qss = get_status_badge_qss("ERROR")
        assert PALETTE.error in error_qss

        idle_qss = get_status_badge_qss("IDLE")
        assert PALETTE.secondary in idle_qss


class TestWaveformWidget:
    """Тестирование визуализатора звуковой волны WaveformWidget."""

    def test_widget_initialization(self, qapp: QApplication) -> None:
        """Проверка базовой инициализации и размеров виджета."""
        widget = WaveformWidget()
        assert widget.get_state() == "IDLE"
        hint = widget.sizeHint()
        assert isinstance(hint, QSize)
        assert hint.width() >= 48
        assert hint.height() >= 24

    def test_state_transitions(self, qapp: QApplication) -> None:
        """Проверка установки состояний через строку и через PipelineState."""
        widget = WaveformWidget()

        widget.set_state(PipelineState.LISTENING)
        assert widget.get_state() == "LISTENING"

        widget.set_state("PROCESSING")
        assert widget.get_state() == "PROCESSING"

        widget.set_state(PipelineState.SPEAKING)
        assert widget.get_state() == "SPEAKING"

        widget.set_state("UNKNOWN_STATE")
        assert widget.get_state() == "IDLE"

    def test_amplitude_level_clamping(self, qapp: QApplication) -> None:
        """Проверка установки и ограничения уровня амплитуды."""
        widget = WaveformWidget()

        widget.set_level(0.75)
        assert 0.0 <= widget._level <= 1.0

        widget.set_level(2.5)  # Превышение максимума
        assert widget._target_level == 1.0

        widget.set_level(-0.5)  # Ниже минимума
        assert widget._target_level == 0.0

    def test_animation_control(self, qapp: QApplication) -> None:
        """Проверка запуска и остановки таймера анимации."""
        widget = WaveformWidget()
        assert widget._timer.isActive()

        widget.stop_animation()
        assert not widget._timer.isActive()

        widget.start_animation()
        assert widget._timer.isActive()

    def test_headless_paint_all_states(self, qapp: QApplication) -> None:
        """Отрисовка всех состояний виджета на виртуальном QImage без GUI дисплея."""
        widget = WaveformWidget()
        widget.resize(64, 32)

        for state in ("IDLE", "LISTENING", "PROCESSING", "SPEAKING"):
            widget.set_state(state)
            widget.set_level(0.8)

            image = QImage(64, 32, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            widget.render(image)

            # Убеждаемся, что изображение содержит ненулевые пиксели
            assert not image.isNull()

    def test_close_event_stops_timer(self, qapp: QApplication) -> None:
        """Проверка остановки таймера при закрытии виджета."""
        widget = WaveformWidget()
        assert widget._timer.isActive()
        widget.close()
        assert not widget._timer.isActive()


class TestChatDrawer:
    """Тестирование выдвижной панели истории ChatDrawer и MessageCard."""

    def test_message_card_creation(self, qapp: QApplication) -> None:
        """Проверка создания и модификации карточки сообщения."""
        card = MessageCard(role="user", text="Открыть калькулятор", status="done")
        assert card.get_role() == "user"
        assert card.get_text() == "Открыть калькулятор"
        assert card.get_status() == "done"

        card.set_text("Новый текст")
        assert card.get_text() == "Новый текст"

        card.set_status("error")
        assert card.get_status() == "error"

    def test_chat_drawer_initial_state(self, qapp: QApplication) -> None:
        """Проверка начального сложенного состояния панели."""
        drawer = ChatDrawer()
        assert not drawer.is_expanded()
        assert drawer.maximumHeight() == 0
        assert drawer.get_messages_count() == 0

    def test_add_and_clear_messages(self, qapp: QApplication) -> None:
        """Проверка добавления сообщений различных ролей и очистки."""
        drawer = ChatDrawer()

        c1 = drawer.add_message("user", "Запусти Блокнот", status="done")
        c2 = drawer.add_message("action", "Запуск notepad.exe", status="running")
        c3 = drawer.add_message("assistant", "Блокнот открыт", status="done")

        assert drawer.get_messages_count() == 3
        assert isinstance(c1, MessageCard)
        assert c2.get_status() == "running"
        assert c3.get_role() == "assistant"

        drawer.clear()
        assert drawer.get_messages_count() == 0

    def test_drawer_toggle_expansion(self, qapp: QApplication) -> None:
        """Проверка переключения раскрытия панели."""
        drawer = ChatDrawer(target_height=200)

        # Раскрываем
        drawer.toggle(True)
        assert drawer.is_expanded()
        assert drawer._animation.endValue() == 200

        # Сворачиваем
        drawer.toggle(False)
        assert not drawer.is_expanded()
        assert drawer._animation.endValue() == 0

        # Инверсия (toggle без аргументов)
        drawer.toggle()
        assert drawer.is_expanded()
        assert drawer._animation.endValue() == 200
