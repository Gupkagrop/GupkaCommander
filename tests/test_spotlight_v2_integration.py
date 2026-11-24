"""
> [!NOTE]
> Модуль тестов tests.test_spotlight_v2_integration: Интеграционные headless-тесты
> Фазы 5B (Concept B — Fluent 2 Docked Blade & Micro-Pore Matrix) для SpotlightBar,
> ChatDrawer (StepCard + apply_theme) и SettingsDialog (докинг, мониторы, тема, STT/TTS).
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QImage, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QComboBox

from core.bootstrap import DependencyManager
from core.voice.voice_pipeline import PipelineState
from gui.agent_widgets import StepCard, StepEngine, StepStatus
from gui.chat_drawer import ChatDrawer, render_markdown_to_html
from gui.dock_manager import (
    DisplayMode,
    DockManager,
    DockPosition,
    ScreenDescriptor,
    compute_flat_edges,
    should_invert_input_order,
)
from gui.settings_dialog import SettingsDialog
from gui.spotlight_bar import SpotlightBar
from gui.theme_manager import DARK_TOKENS, LIGHT_TOKENS, BackdropType, ThemeManager, ThemeMode


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Синглтон QApplication для headless-тестирования Qt."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(sys.argv)
    return app


def _three_monitor_setup() -> list[ScreenDescriptor]:
    """Возвращает конфигурацию из 3 мониторов со смешанным DPI и отрицательным X."""
    return [
        ScreenDescriptor(0, QRect(0, 0, 1920, 1040), 1.0, "Primary"),
        ScreenDescriptor(1, QRect(1920, 0, 2560, 1400), 1.5, "Right-4K"),
        ScreenDescriptor(2, QRect(-1920, 100, 1920, 1040), 1.25, "Left-FHD"),
    ]


@pytest.fixture
def docked_bar(qapp: QApplication) -> Generator[SpotlightBar, None, None]:
    """Изолированный экземпляр SpotlightBar с управляемым 3-мониторным DockManager."""
    dock_mgr = DockManager(initial_position=DockPosition.TOP)
    dock_mgr.set_virtual_screens(_three_monitor_setup())
    theme_mgr = ThemeManager(mode=ThemeMode.DARK)
    bar = SpotlightBar(
        hide_on_focus_loss=False,
        theme_manager=theme_mgr,
        dock_manager=dock_mgr,
    )
    yield bar
    bar.hide()
    bar.deleteLater()


def _select_combo_by_data(combo: QComboBox, value: str) -> None:
    """Выбирает пункт QComboBox по значению userData."""
    idx = combo.findData(value)
    assert idx >= 0, f"Значение {value!r} не найдено в QComboBox"
    combo.setCurrentIndex(idx)


class TestSpotlightDockingAndMultiMonitor:
    """Проверка стыковки по 8 зонам, авто-инверсии, мультимониторности и drag-snap."""

    def test_initial_width_and_zero_fps_idle(self, docked_bar: SpotlightBar) -> None:
        """На старте ширина равна DEFAULT_WIDTH, а оба визуализатора потребляют 0 FPS."""
        assert docked_bar.width() == SpotlightBar.DEFAULT_WIDTH
        assert docked_bar.dock_position == DockPosition.TOP
        assert docked_bar.display_mode == DisplayMode.FULL_DECK
        assert not docked_bar.sponge_pore.is_animating()
        assert not docked_bar.waveform._timer.isActive()

    @pytest.mark.parametrize("position", list(DockPosition))
    def test_all_eight_dock_positions_and_inversion(
        self,
        docked_bar: SpotlightBar,
        position: DockPosition,
    ) -> None:
        """Каждая из 8 позиций стыковки обнуляет внешние отступы и инвертирует низ."""
        emitted: list[str] = []
        docked_bar.dock_position_changed.connect(emitted.append)

        geom = docked_bar.set_dock_position(position, cursor_pos=QPoint(500, 500))
        assert docked_bar.dock_position == position
        assert geom.monitor_index == 0
        assert geom.rect.width() == docked_bar.width()

        # Внешние пристыкованные грани имеют нулевой margin для плоского стыка 0px
        edges = compute_flat_edges(position)
        margins = docked_bar._outer_layout.contentsMargins()
        assert (margins.left() == 0) == ("left" in edges)
        assert (margins.top() == 0) == ("top" in edges)
        assert (margins.right() == 0) == ("right" in edges)
        assert (margins.bottom() == 0) == ("bottom" in edges)

        # Проверяем порядок элементов: ручка захвата на свободной грани + авто-инверсия снизу
        first_item = docked_bar._deck_layout.itemAt(0)
        last_item = docked_bar._deck_layout.itemAt(docked_bar._deck_layout.count() - 1)
        assert first_item is not None and last_item is not None
        first_widget = first_item.widget()
        last_widget = last_item.widget()
        drawer_idx = docked_bar._deck_layout.indexOf(docked_bar.chat_drawer)
        input_idx = docked_bar._deck_layout.indexOf(docked_bar._input_row)
        if should_invert_input_order(position):
            assert first_widget is docked_bar._drag_strip
            assert last_widget is docked_bar._input_row
            assert drawer_idx < input_idx
        else:
            assert first_widget is docked_bar._input_row
            assert last_widget is docked_bar._drag_strip
            assert input_idx < drawer_idx

        if position != DockPosition.TOP:
            assert position.value in emitted

    def test_multi_monitor_center_on_screen_and_target_monitor(
        self,
        docked_bar: SpotlightBar,
    ) -> None:
        """center_on_screen выбирает активный монитор под курсором и обновляет чип DPI."""
        # Курсор на правом 4K мониторе (индекс 1, 150% DPI)
        geom_right = docked_bar.center_on_screen(cursor_pos=QPoint(2500, 300))
        assert geom_right.monitor_index == 1
        assert 1920 <= docked_bar.x() < 1920 + 2560
        mon_btn = docked_bar.context_chip_bar.get_chip_button("monitor")
        assert mon_btn is not None
        assert mon_btn.text() == "Мон. 2 · 150%"

        # Курсор на левом мониторе с отрицательным X (индекс 2, 125% DPI)
        geom_left = docked_bar.center_on_screen(cursor_pos=QPoint(-1000, 400))
        assert geom_left.monitor_index == 2
        assert -1920 <= docked_bar.x() < 0
        assert mon_btn.text() == "Мон. 3 · 125%"

        # Жёсткая привязка к монитору 1 игнорирует курсор на мониторе 2
        docked_bar.set_target_monitor("1")
        geom_fixed = docked_bar.center_on_screen(cursor_pos=QPoint(2500, 300))
        assert geom_fixed.monitor_index == 0
        assert mon_btn.text() == "Мон. 1 · 100%"

        # Возврат в auto снова следует за курсором
        docked_bar.set_target_monitor("auto")
        geom_auto = docked_bar.center_on_screen(cursor_pos=QPoint(2500, 300))
        assert geom_auto.monitor_index == 1

    def test_optional_snap_on_drag_release(self, docked_bar: SpotlightBar) -> None:
        """При snap_on_drag_release=True отпускание мыши у угла стыкует панель туда."""
        docked_bar.set_snap_on_drag_release(True)
        press_evt = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(20.0, 10.0),
            QPointF(960.0, 20.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        docked_bar.mousePressEvent(press_evt)

        rel_evt = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(20.0, 10.0),
            QPointF(1880.0, 1000.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        docked_bar.mouseReleaseEvent(rel_evt)
        assert docked_bar.dock_position == DockPosition.BOTTOM_RIGHT


class TestThemeAgentMiniBarAndChips:
    """Проверка переключения Dark/Light, AgentMiniBar, ContextChipBar и SpongePore."""

    def test_dark_light_theme_switch_propagates_to_all_children(
        self,
        docked_bar: SpotlightBar,
    ) -> None:
        """Смена темы Dark -> Light обновляет SpotlightBar, ChatDrawer, MessageCard и StepCard."""
        msg_card = docked_bar.add_chat_message("assistant", "Готов к работе")
        step_card = docked_bar.add_step_card(
            1, "Открыть настройки", "UIA", status="done", duration_ms=42, detail="Invoke()"
        )

        assert docked_bar.theme_tokens == DARK_TOKENS
        docked_bar.set_theme_mode(ThemeMode.LIGHT)
        assert docked_bar.theme_tokens == LIGHT_TOKENS
        assert LIGHT_TOKENS.surface_glass in docked_bar.styleSheet()
        assert LIGHT_TOKENS.text in msg_card._text_label.styleSheet()
        assert LIGHT_TOKENS.surface_card in step_card.styleSheet()

        docked_bar.set_backdrop_type(BackdropType.MICA)
        assert docked_bar.backdrop_type == BackdropType.MICA

        # Отрисовка в QImage в обеих темах не падает
        for mode in (ThemeMode.LIGHT, ThemeMode.DARK):
            docked_bar.set_theme_mode(mode)
            img = QImage(docked_bar.size(), QImage.Format.Format_ARGB32)
            img.fill(Qt.GlobalColor.transparent)
            docked_bar.render(img)
            assert not img.isNull()

    def test_spotlight_apply_backdrop_uses_dwm_none_and_keeps_property(
        self,
        docked_bar: SpotlightBar,
    ) -> None:
        """_apply_backdrop всегда передаёт BackdropType.NONE в DWM, сохраняя API backdrop_type."""
        docked_bar.show()
        with patch("gui.spotlight_bar.apply_win11_backdrop") as mock_dwm:
            docked_bar.set_backdrop_type(BackdropType.ACRYLIC)
            assert docked_bar.backdrop_type == BackdropType.ACRYLIC
            mock_dwm.assert_called()
            _, kwargs = mock_dwm.call_args
            assert kwargs["backdrop"] == BackdropType.NONE
            assert kwargs["disable_dwm_rounding"] is True
            assert kwargs["dark"] is True

    def test_agent_mini_bar_toggle_and_abort_requested(
        self,
        docked_bar: SpotlightBar,
    ) -> None:
        """set_agent_progress переключает FULL_DECK <-> MINI_AGENT_BAR и шлёт abort_requested."""
        aborts: list[bool] = []
        docked_bar.abort_requested.connect(lambda: aborts.append(True))

        docked_bar.set_agent_progress(
            active=True,
            step_index=2,
            max_steps=5,
            title="Клик «Сохранить»",
            engine="UIA",
        )
        assert docked_bar.display_mode == DisplayMode.MINI_AGENT_BAR
        assert not docked_bar._deck_widget.isVisibleTo(docked_bar)
        assert docked_bar.agent_mini_bar.isVisibleTo(docked_bar)
        assert "Шаг 2/5" in docked_bar.agent_mini_bar.step_text()
        assert docked_bar.agent_mini_bar.progress_bar.value() == 2

        # Клик по [Esc · Стоп]
        docked_bar.agent_mini_bar.abort_button.click()
        assert len(aborts) == 1

        # Нажатие Esc в режиме агента тоже испускает abort_requested
        esc = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier,
        )
        docked_bar.keyPressEvent(esc)
        assert len(aborts) == 2

        # Выход из режима агента возвращает FULL_DECK
        docked_bar.set_agent_progress(active=False)
        assert docked_bar.display_mode.value == DisplayMode.FULL_DECK.value
        assert docked_bar._deck_widget.isVisibleTo(docked_bar)
        assert not docked_bar.agent_mini_bar.isVisibleTo(docked_bar)

    def test_collapsed_pill_and_context_chips_and_pipeline_sync(
        self,
        docked_bar: SpotlightBar,
    ) -> None:
        """Проверка COLLAPSED_PILL, обновления ContextChipBar и синхронизации состояний."""
        docked_bar.update_context_chips(
            active_app="Google Chrome",
            memory_count=4,
            voice_mode="Голос",
            voice_active=True,
        )
        app_btn = docked_bar.context_chip_bar.get_chip_button("app")
        mem_btn = docked_bar.context_chip_bar.get_chip_button("memory")
        voice_btn = docked_bar.context_chip_bar.get_chip_button("voice")
        assert app_btn is not None and app_btn.text() == "Google Chrome"
        assert mem_btn is not None and mem_btn.text() == "Память: 4"
        assert voice_btn is not None and voice_btn.text() == "Голос"

        # Перевод в LISTENING включает 60 FPS у SpongePore и синхронизирует waveform
        docked_bar.set_pipeline_state(PipelineState.LISTENING)
        docked_bar.set_audio_level(0.7)
        assert docked_bar.sponge_pore.get_state() == "LISTENING"
        assert docked_bar.sponge_pore.current_fps() == 60
        assert docked_bar.waveform.get_state() == "LISTENING"
        assert docked_bar.waveform._target_level == pytest.approx(0.7)

        # Сворачивание в COLLAPSED_PILL гасит анимацию (0 FPS)
        docked_bar.set_display_mode(DisplayMode.COLLAPSED_PILL)
        assert docked_bar.display_mode == DisplayMode.COLLAPSED_PILL
        assert docked_bar.sponge_pore.current_fps() == 0

        # Клик по свёрнутой кромке возвращает FULL_DECK
        docked_bar._collapsed_pill.clicked.emit()
        assert docked_bar.display_mode.value == DisplayMode.FULL_DECK.value

        # Отключение микрофона обновляет чип голоса и статус
        mute_signals: list[bool] = []
        docked_bar.mute_toggled_requested.connect(mute_signals.append)
        voice_btn.click()
        assert mute_signals == [True]
        assert voice_btn.text() == "Без звука"


class TestChatDrawerStepCardsAndThemes:
    """Проверка добавления StepCard в ChatDrawer и адаптации тем."""

    def test_chat_drawer_add_step_card_preserves_message_count_and_clears(
        self,
        qapp: QApplication,
    ) -> None:
        """StepCard рендерится рядом с MessageCard, не ломая get_messages_count и clear."""
        drawer = ChatDrawer()
        try:
            drawer.add_message("user", "Открой GitHub", status="done")
            step = drawer.add_step_card(
                step_index=1,
                title="Переход на github.com",
                engine="CDP",
                status="done",
                duration_ms=280,
                detail="Page.navigate -> 200 OK",
            )
            assert isinstance(step, StepCard)
            assert step.engine == StepEngine.CDP
            assert step.status == StepStatus.DONE
            assert drawer.get_messages_count() == 1
            assert drawer.get_step_cards_count() == 1

            drawer.apply_theme(LIGHT_TOKENS)
            assert LIGHT_TOKENS.surface_card in step.styleSheet()

            drawer.clear()
            assert drawer.get_messages_count() == 0
            assert drawer.get_step_cards_count() == 0
        finally:
            drawer.deleteLater()

    def test_chat_drawer_markdown_rendering_and_plain_get_text(
        self,
        qapp: QApplication,
    ) -> None:
        """Markdown безопасно рендерится в RichText, а MessageCard.get_text возвращает plain."""
        raw_md = (
            "```python\n"
            "print('<script>alert(1)</script>')\n"
            "```\n\n"
            "**Инструкция:**\n"
            "1. Сохраните в `open_app.py` и *проверьте* запуск.\n"
            "- Поддержка списков."
        )
        html_dark = render_markdown_to_html(raw_md, DARK_TOKENS)
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_dark
        assert "<script>" not in html_dark
        assert "<table" in html_dark
        assert "<b" in html_dark and "Инструкция:" in html_dark
        assert "<i>проверьте</i>" in html_dark
        assert "open_app.py" in html_dark

        html_light = render_markdown_to_html(raw_md, LIGHT_TOKENS)
        assert LIGHT_TOKENS.text in html_light

        drawer = ChatDrawer()
        try:
            card = drawer.add_message("assistant", raw_md, status="done")
            assert card.get_text() == raw_md
            assert card._text_label.textFormat() == Qt.TextFormat.RichText
            assert "<table" in card._text_label.text()
            # Нижний отступ контента QScrollArea >= 14px для защиты от обрезания
            assert drawer._cards_layout.contentsMargins().bottom() >= 14
        finally:
            drawer.deleteLater()


class TestSettingsDialogV2Persistence:
    """Проверка новых контролов внешнего вида, докинга и движков STT/TTS."""

    def test_settings_dialog_roundtrip_appearance_docking_and_voice_engines(
        self,
        qapp: QApplication,
        tmp_path: Path,
    ) -> None:
        """Настройки докинга, монитора, темы, DWM и STT/TTS сохраняются и перечитываются."""
        settings_path = tmp_path / "config" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(
            json.dumps({"extra_section": {"keep": True}}),
            encoding="utf-8",
        )
        dep_mgr = DependencyManager(
            models_dir=tmp_path / "models",
            bin_dir=tmp_path / "bin",
            state_file=tmp_path / "config" / "bootstrap_state.json",
        )

        dlg = SettingsDialog(settings_path=settings_path, dep_manager=dep_mgr)
        try:
            # Значения по умолчанию
            assert dlg.dock_position_combo.currentData() == "top"
            assert dlg.target_monitor_combo.currentData() == "auto"
            assert dlg.theme_combo.currentData() == "system"
            assert dlg.backdrop_combo.currentData() == "acrylic"
            assert dlg.stt_engine_combo.currentData() == "gigaam-v3"
            assert dlg.tts_engine_combo.currentData() == "qwen3-tts"

            # Изменяем все новые параметры
            _select_combo_by_data(dlg.dock_position_combo, "bottom_right")
            _select_combo_by_data(dlg.target_monitor_combo, "2")
            _select_combo_by_data(dlg.theme_combo, "light")
            _select_combo_by_data(dlg.backdrop_combo, "mica")
            _select_combo_by_data(dlg.stt_engine_combo, "faster-whisper")
            _select_combo_by_data(dlg.tts_engine_combo, "piper")

            saved = dlg.save_settings()
            assert saved["extra_section"] == {"keep": True}
            assert saved["ui"] == {
                "dock_position": "bottom_right",
                "target_monitor": "2",
                "theme": "light",
                "backdrop": "mica",
            }
            assert saved["models"]["stt"]["engine"] == "faster-whisper"
            assert saved["models"]["tts"]["engine"] == "piper"
        finally:
            dlg.close()

        # Повторное открытие диалога корректно восстанавливает сохранённые значения
        reloaded = SettingsDialog(
            settings_path=settings_path,
            dep_manager=dep_mgr,
            theme_tokens=LIGHT_TOKENS,
        )
        try:
            assert reloaded.dock_position_combo.currentData() == "bottom_right"
            assert reloaded.target_monitor_combo.currentData() == "2"
            assert reloaded.theme_combo.currentData() == "light"
            assert reloaded.backdrop_combo.currentData() == "mica"
            assert reloaded.stt_engine_combo.currentData() == "faster-whisper"
            assert reloaded.tts_engine_combo.currentData() == "piper"
            assert LIGHT_TOKENS.background in reloaded.styleSheet()
        finally:
            reloaded.close()
