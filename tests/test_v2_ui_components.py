"""
> [!NOTE]
> Модуль тестов tests.test_v2_ui_components: Headless-тесты PySide6 для компонентов
> GupkaCommander 2.0 (Concept B): ThemeManager, DockManager, SpongePoreWidget,
> ContextChipBar, StepCard и AgentMiniBar.
"""

from __future__ import annotations

import os
import sys

# Гарантируем offscreen-рендеринг Qt до инициализации QApplication
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QEnterEvent, QHideEvent, QImage, QKeyEvent, QShowEvent
from PySide6.QtWidgets import QApplication, QWidget

from gui.agent_widgets import (
    AgentMiniBar,
    ContextChipBar,
    StepCard,
    StepEngine,
    StepStatus,
    format_duration_ms,
)
from gui.dock_manager import (
    DisplayMode,
    DockManager,
    DockPosition,
    ScreenDescriptor,
    compute_corner_radii,
)
from gui.sponge_pore_widget import (
    PoreState,
    SpongePoreWidget,
    build_micro_pore_tile,
    clear_micro_pore_tile_cache,
)
from gui.theme_manager import (
    DARK_TOKENS,
    LIGHT_TOKENS,
    ThemeManager,
    ThemeMode,
    ThemeTokens,
    apply_win11_backdrop,
    build_blade_stylesheet,
)


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Синглтон QApplication для headless-тестирования виджетов."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(sys.argv)
    return app


def _render_widget_to_image(widget: QWidget, width: int, height: int) -> QImage:
    """Рендерит произвольный виджет в прозрачный QImage для проверки отрисовки."""
    widget.resize(width, height)
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    widget.render(image)
    return image


def _count_visible_pixels(image: QImage) -> int:
    """Подсчитывает количество ненулевых по альфа-каналу пикселей в QImage."""
    count = 0
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() > 0:
                count += 1
    return count


class TestThemeManager:
    """Проверка дизайн-токенов Dark/Light, DWM-обёртки и генерации QSS."""

    def test_dark_and_light_palette_tokens(self) -> None:
        """Проверка ключевых цветов Sponge Ochre и Windows 11 Fluent 2."""
        assert DARK_TOKENS.is_dark is True
        assert DARK_TOKENS.background.lower() == "#1b1d22"
        assert DARK_TOKENS.sponge_ochre.lower() == "#e9b44c"
        assert DARK_TOKENS.accent.lower() == "#60cdff"
        assert DARK_TOKENS.success.lower() == "#3dd68c"
        assert DARK_TOKENS.danger.lower() == "#ff5c52"

        assert LIGHT_TOKENS.is_dark is False
        assert LIGHT_TOKENS.background.lower() == "#f6f5f1"
        assert LIGHT_TOKENS.sponge_ochre.lower() == "#b87d14"
        assert LIGHT_TOKENS.accent.lower() == "#0067c0"
        assert LIGHT_TOKENS.success.lower() == "#107c41"
        assert LIGHT_TOKENS.danger.lower() == "#c42b1c"

    def test_theme_manager_switching_and_signal(self, qapp: QApplication) -> None:
        """Проверка переключения режимов DARK/LIGHT и испускания theme_changed."""
        manager = ThemeManager(mode=ThemeMode.DARK)
        emitted: list[ThemeTokens] = []
        manager.theme_changed.connect(emitted.append)

        assert manager.is_dark is True
        assert manager.tokens == DARK_TOKENS

        light_tokens = manager.set_mode(ThemeMode.LIGHT)
        assert light_tokens == LIGHT_TOKENS
        assert manager.is_dark is False
        assert len(emitted) == 1
        assert emitted[0] == LIGHT_TOKENS

        # Повторная установка того же режима не испускает лишний сигнал
        manager.set_mode("light")
        assert len(emitted) == 1

    def test_apply_win11_backdrop_safe_noop_in_offscreen(self) -> None:
        """В offscreen-режиме и при невалидном hwnd DWM-вызов безопасно возвращает False."""
        assert apply_win11_backdrop(0, dark=True) is False
        assert apply_win11_backdrop(-5, dark=False, backdrop="mica") is False
        assert apply_win11_backdrop(123456, dark=True, backdrop="acrylic") is False

    @pytest.mark.parametrize(
        ("position", "expected_radii"),
        [
            (DockPosition.TOP, (0, 0, 18, 18)),
            (DockPosition.BOTTOM, (18, 18, 0, 0)),
            (DockPosition.LEFT, (0, 18, 18, 0)),
            (DockPosition.RIGHT, (18, 0, 0, 18)),
            (DockPosition.TOP_LEFT, (0, 0, 18, 0)),
            (DockPosition.TOP_RIGHT, (0, 0, 0, 18)),
            (DockPosition.BOTTOM_LEFT, (0, 18, 0, 0)),
            (DockPosition.BOTTOM_RIGHT, (18, 0, 0, 0)),
        ],
    )
    def test_build_blade_stylesheet_per_dock_position(
        self,
        position: DockPosition,
        expected_radii: tuple[int, int, int, int],
    ) -> None:
        """Проверка корректных радиусов скругления и токенов в QSS для всех 8 позиций."""
        tl, tr, br, bl = expected_radii
        qss_dark = build_blade_stylesheet(DARK_TOKENS, position)
        assert f"border-top-left-radius: {tl}px;" in qss_dark
        assert f"border-top-right-radius: {tr}px;" in qss_dark
        assert f"border-bottom-right-radius: {br}px;" in qss_dark
        assert f"border-bottom-left-radius: {bl}px;" in qss_dark
        assert DARK_TOKENS.sponge_ochre in qss_dark

        qss_light = build_blade_stylesheet(LIGHT_TOKENS, position)
        assert LIGHT_TOKENS.sponge_ochre in qss_light
        assert LIGHT_TOKENS.surface_glass in qss_light


class TestDockManager:
    """Проверка мультимониторной геометрии, 8 позиций стыковки и snap-зон."""

    @staticmethod
    def _three_monitors() -> list[ScreenDescriptor]:
        """Создаёт конфигурацию из 3 мониторов с отрицательным X и смешанным DPI."""
        return [
            ScreenDescriptor(0, QRect(-1920, 0, 1920, 1040), 1.0, "Left-1080p"),
            ScreenDescriptor(1, QRect(0, 0, 2560, 1400), 1.5, "Center-QHD-150%"),
            ScreenDescriptor(2, QRect(2560, -200, 1080, 1880), 1.25, "Right-Vertical"),
        ]

    @pytest.mark.parametrize("position", list(DockPosition))
    @pytest.mark.parametrize("mode", list(DisplayMode))
    def test_all_positions_and_modes_flush_without_clipping(
        self,
        qapp: QApplication,
        position: DockPosition,
        mode: DisplayMode,
    ) -> None:
        """Все 8 позиций x 3 режима лежат строго внутри экрана и вплотную к краям."""
        manager = DockManager()
        avail = QRect(0, 0, 1920, 1040)
        manager.set_virtual_screens([ScreenDescriptor(0, avail, 1.5, "Primary")])

        geom = manager.compute_geometry(position, mode, monitor_index=0)
        assert avail.contains(geom.rect)
        assert geom.corner_radii == compute_corner_radii(position, 18)
        assert geom.dpi_scale == 1.5
        assert geom.is_vertical is (position in (DockPosition.LEFT, DockPosition.RIGHT))
        assert geom.invert_input_order is (
            position in (DockPosition.BOTTOM, DockPosition.BOTTOM_LEFT, DockPosition.BOTTOM_RIGHT)
        )

        # Проверка плоского примыкания 0px по всем заявленным внешним граням
        if "top" in geom.flat_edges:
            assert geom.rect.top() == avail.top()
        if "bottom" in geom.flat_edges:
            assert geom.rect.bottom() == avail.bottom()
        if "left" in geom.flat_edges:
            assert geom.rect.left() == avail.left()
        if "right" in geom.flat_edges:
            assert geom.rect.right() == avail.right()

    def test_multimonitor_negative_and_offset_resolution(self, qapp: QApplication) -> None:
        """Проверка определения активного монитора и геометрии при отрицательных координатах."""
        manager = DockManager()
        screens = self._three_monitors()
        manager.set_virtual_screens(screens)

        left_mon = manager.resolve_active_monitor(QPoint(-900, 500))
        center_mon = manager.resolve_active_monitor(QPoint(1280, 700))
        right_mon = manager.resolve_active_monitor(QPoint(3000, 400))

        assert left_mon.index == 0
        assert left_mon.dpi_scale == 1.0
        assert center_mon.index == 1
        assert center_mon.dpi_scale == 1.5
        assert right_mon.index == 2
        assert right_mon.dpi_scale == 1.25

        geom_left = manager.compute_geometry(
            DockPosition.TOP_LEFT,
            DisplayMode.FULL_DECK,
            cursor_pos=QPoint(-1000, 100),
        )
        assert geom_left.monitor_index == 0
        assert geom_left.rect.left() == -1920
        assert geom_left.rect.top() == 0
        assert screens[0].available_geometry.contains(geom_left.rect)

        geom_right = manager.compute_geometry(
            DockPosition.BOTTOM_RIGHT,
            DisplayMode.MINI_AGENT_BAR,
            cursor_pos=QPoint(3100, 1500),
        )
        assert geom_right.monitor_index == 2
        assert geom_right.rect.right() == screens[2].available_geometry.right()
        assert geom_right.rect.bottom() == screens[2].available_geometry.bottom()
        assert screens[2].available_geometry.contains(geom_right.rect)

    @pytest.mark.parametrize(
        ("offset", "expected"),
        [
            ((0.50, 0.05), DockPosition.TOP),
            ((0.50, 0.95), DockPosition.BOTTOM),
            ((0.05, 0.50), DockPosition.LEFT),
            ((0.95, 0.50), DockPosition.RIGHT),
            ((0.08, 0.08), DockPosition.TOP_LEFT),
            ((0.92, 0.08), DockPosition.TOP_RIGHT),
            ((0.08, 0.92), DockPosition.BOTTOM_LEFT),
            ((0.92, 0.92), DockPosition.BOTTOM_RIGHT),
        ],
    )
    def test_detect_snap_zone_all_eight_zones(
        self,
        qapp: QApplication,
        offset: tuple[float, float],
        expected: DockPosition,
    ) -> None:
        """Проверка распознавания всех 8 зон прилипания на мониторе с отрицательным X."""
        manager = DockManager()
        screens = self._three_monitors()
        manager.set_virtual_screens(screens)
        left_avail = screens[0].available_geometry

        px = left_avail.left() + int(left_avail.width() * offset[0])
        py = left_avail.top() + int(left_avail.height() * offset[1])
        detected = manager.detect_snap_zone(QPoint(px, py))
        assert detected == expected

    def test_tiny_screen_never_clips(self, qapp: QApplication) -> None:
        """Даже на экстремально малом экране окно не выходит за availableGeometry."""
        manager = DockManager()
        tiny = QRect(0, 0, 320, 240)
        manager.set_virtual_screens([ScreenDescriptor(0, tiny, 1.0, "Tiny")])

        geom = manager.compute_geometry(
            DockPosition.BOTTOM,
            DisplayMode.FULL_DECK,
            monitor_index=0,
            content_width=900,
            content_height=600,
        )
        assert tiny.contains(geom.rect)
        assert geom.rect.width() == 320
        assert geom.rect.height() == 240


class TestSpongePoreWidget:
    """Проверка энергосбережения (0/15/60 FPS), рендеринга состояний и кеша тайла."""

    def test_idle_is_zero_fps_and_hover_wakes_15_fps(self, qapp: QApplication) -> None:
        """В покое IDLE таймер остановлен (0 FPS), наведение включает 15 FPS."""
        widget = SpongePoreWidget(tokens=DARK_TOKENS)
        assert widget.get_state() == PoreState.IDLE.value
        assert widget.is_animating() is False
        assert widget.current_fps() == 0

        enter_ev = QEnterEvent(QPointF(10.0, 10.0), QPointF(10.0, 10.0), QPointF(10.0, 10.0))
        widget.enterEvent(enter_ev)
        assert widget.is_animating() is True
        assert widget.current_fps() == 15

        widget.leaveEvent(QEvent(QEvent.Type.Leave))
        assert widget.is_animating() is False
        assert widget.current_fps() == 0

    def test_active_states_run_at_60_fps_and_stop_when_collapsed_or_hidden(
        self,
        qapp: QApplication,
    ) -> None:
        """LISTENING/PROCESSING/SPEAKING работают на 60 FPS; collapsed/hide останавливают таймер."""
        widget = SpongePoreWidget(tokens=DARK_TOKENS)

        for active_state in (PoreState.LISTENING, PoreState.PROCESSING, PoreState.SPEAKING):
            widget.set_state(active_state)
            assert widget.is_animating() is True
            assert widget.current_fps() == 60

        widget.set_collapsed(True)
        assert widget.is_animating() is False
        assert widget.current_fps() == 0

        widget.set_collapsed(False)
        assert widget.current_fps() == 60

        widget.hideEvent(QHideEvent())
        assert widget.is_animating() is False

        widget.showEvent(QShowEvent())
        assert widget.current_fps() == 60

        for static_state in (PoreState.MUTED, PoreState.ERROR, PoreState.IDLE):
            widget.set_state(static_state)
            assert widget.is_animating() is False
            assert widget.current_fps() == 0

    @pytest.mark.parametrize("state", list(PoreState))
    @pytest.mark.parametrize("tokens", [DARK_TOKENS, LIGHT_TOKENS])
    def test_all_states_paint_visible_pixels_in_both_themes(
        self,
        qapp: QApplication,
        state: PoreState,
        tokens: ThemeTokens,
    ) -> None:
        """Каждое состояние корректно рисуется на QImage в Dark и Light темах."""
        widget = SpongePoreWidget(tokens=tokens, size=40)
        widget.set_state(state)
        widget.set_audio_level(0.75)
        widget.set_phase_for_preview(1.2)

        image = _render_widget_to_image(widget, 40, 40)
        assert _count_visible_pixels(image) > 50

    def test_micro_pore_tile_caching_and_dpr(self, qapp: QApplication) -> None:
        """Проверка кеширования 64x64 QPixmap и масштабирования по DPR."""
        clear_micro_pore_tile_cache()
        tile_dark_1 = build_micro_pore_tile(DARK_TOKENS, dpr=1.0)
        tile_dark_1_again = build_micro_pore_tile(DARK_TOKENS, dpr=1.0)
        tile_dark_15 = build_micro_pore_tile(DARK_TOKENS, dpr=1.5)
        tile_light_1 = build_micro_pore_tile(LIGHT_TOKENS, dpr=1.0)

        assert tile_dark_1 is tile_dark_1_again
        assert tile_dark_1 is not tile_light_1
        assert tile_dark_1.width() == 64
        assert tile_dark_15.width() == 96
        assert tile_dark_15.devicePixelRatio() == pytest.approx(1.5)


class TestAgentWidgets:
    """Проверка ContextChipBar, StepCard и AgentMiniBar."""

    def test_context_chip_bar_clicks_and_updates(self, qapp: QApplication) -> None:
        """Клик по чипу испускает chip_clicked с верным ID, обновление меняет текст."""
        bar = ContextChipBar(tokens=DARK_TOKENS)
        bar.set_context(
            monitor_label="Мон. 2 · 150%",
            active_app="Chrome",
            memory_count=3,
            voice_mode="Голос",
        )
        assert bar.chip_ids() == ["monitor", "app", "memory", "voice"]

        clicked: list[str] = []
        bar.chip_clicked.connect(clicked.append)

        monitor_btn = bar.get_chip_button("monitor")
        assert monitor_btn is not None
        assert monitor_btn.text() == "Мон. 2 · 150%"
        monitor_btn.click()
        assert clicked == ["monitor"]

        assert bar.update_chip("memory", "Память: 5", active=True) is True
        assert bar.update_chip("missing", "X") is False
        memory_btn = bar.get_chip_button("memory")
        assert memory_btn is not None
        assert memory_btn.text() == "Память: 5"

        bar.set_tokens(LIGHT_TOKENS)
        image = _render_widget_to_image(bar, 480, 36)
        assert _count_visible_pixels(image) > 100

    @pytest.mark.parametrize("engine", list(StepEngine))
    def test_step_card_engines_status_and_collapsible_details(
        self,
        qapp: QApplication,
        engine: StepEngine,
    ) -> None:
        """Проверка бейджей движков, обновления статуса, формата времени и сворачивания."""
        card = StepCard(
            step_index=2,
            title="Клик «Сохранить»",
            engine=engine,
            status=StepStatus.RUNNING,
            duration_ms=42,
            details="UIA ControlType=Button Name='Сохранить'",
            tokens=DARK_TOKENS,
        )
        assert card.step_index == 2
        assert card.engine == engine
        assert card.status == StepStatus.RUNNING
        assert card.details_expanded is False

        toggles: list[bool] = []
        card.details_toggled.connect(toggles.append)
        card.toggle_details()
        assert card.details_expanded is True
        assert toggles == [True]

        card.update_status(StepStatus.DONE, duration_ms=1500)
        assert card.status == StepStatus.DONE
        assert format_duration_ms(42) == "42 мс"
        assert format_duration_ms(1500) == "1.5 с"
        assert format_duration_ms(None) == "—"

        card.set_tokens(LIGHT_TOKENS)
        image = _render_widget_to_image(card, 420, 90)
        assert _count_visible_pixels(image) > 100

    def test_agent_mini_bar_step_and_abort_signals(self, qapp: QApplication) -> None:
        """Кнопка [Esc · Стоп] и клавиша Esc испускают abort_requested."""
        mini = AgentMiniBar(tokens=DARK_TOKENS)
        aborts: list[str] = []
        mini.abort_requested.connect(lambda: aborts.append("abort"))

        mini.update_step(2, 5, "Клик «Сохранить»", StepEngine.UIA)
        assert "Шаг 2/5" in mini.step_text()
        assert "Клик «Сохранить»" in mini.step_text()
        assert mini.progress_bar.maximum() == 5
        assert mini.progress_bar.value() == 2
        assert mini.pore_widget.get_state() == PoreState.PROCESSING.value
        assert mini.abort_button.text() == "Esc · Стоп"

        mini.abort_button.click()
        assert len(aborts) == 1

        esc_event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier
        )
        mini.keyPressEvent(esc_event)
        assert len(aborts) == 2

        mini.set_tokens(LIGHT_TOKENS)
        image = _render_widget_to_image(mini, 460, 48)
        assert _count_visible_pixels(image) > 100
