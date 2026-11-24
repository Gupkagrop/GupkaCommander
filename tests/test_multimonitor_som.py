# ruff: noqa: N802, N803
"""Модульные тесты Фазы 1: мультимониторность, Per-Monitor DPI v2, UIA фильтрация и SoM."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from core.uia.uia_controller import CONTROL_TYPE_MAP, UIAutomationManager
from core.vision.monitor_manager import MonitorInfo, MonitorManager
from core.vision.screen_capture import (
    ScreenCapture,
    ScreenFrame,
    norm_to_screen,
    screen_to_norm,
    set_dpi_awareness,
)
from core.vision.som_annotator import SoMAnnotator, SoMElement
from core.vision.vision_agent import ActionFailedError, VisionAgent


class MultiMockSctImage:
    """Мок кадра MSS с различимым цветовым паттерном для конкретного монитора."""

    def __init__(self, width: int, height: int, fill_byte: int) -> None:
        self.size = (width, height)
        # Создаем горизонтальный градиент на базе fill_byte, чтобы dhash отличался при изменении
        row = bytes((x + fill_byte) % 256 for x in range(width) for _ in range(3))
        self.rgb = row * height


class MultiMonitorMSSContext:
    """Мок MSS-контекста с настраиваемой конфигурацией 1/2/3+ мониторов."""

    def __init__(
        self,
        monitors: list[dict[str, Any]],
        frame_bytes_by_index: dict[int, list[int]] | None = None,
    ) -> None:
        self.monitors = monitors
        self.grabbed_monitors: list[dict[str, Any]] = []
        self._patterns = frame_bytes_by_index or {}
        self._grab_counts: dict[tuple[int, int], int] = {}

    def __enter__(self) -> MultiMonitorMSSContext:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

    def grab(self, monitor: dict[str, Any]) -> MultiMockSctImage:
        self.grabbed_monitors.append(dict(monitor))
        key = (int(monitor.get("left", 0)), int(monitor.get("top", 0)))
        count = self._grab_counts.get(key, 0)
        self._grab_counts[key] = count + 1

        mon_idx = 1
        for idx, raw in enumerate(self.monitors):
            if idx == 0 and len(self.monitors) > 1:
                continue
            if int(raw.get("left", 0)) == key[0] and int(raw.get("top", 0)) == key[1]:
                mon_idx = idx
                break

        pattern_seq = self._patterns.get(mon_idx, [64])
        fill_val = pattern_seq[min(count, len(pattern_seq) - 1)]
        return MultiMockSctImage(int(monitor["width"]), int(monitor["height"]), fill_val)


class _MockRect:
    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom

    def width(self) -> int:
        return self.right - self.left

    def height(self) -> int:
        return self.bottom - self.top


class _MockControl:
    def __init__(
        self,
        name: str = "",
        automation_id: str = "",
        control_type: int = 50000,
        control_type_name: str = "ButtonControl",
        rect: _MockRect | None = None,
        children: list[_MockControl] | None = None,
        is_enabled: bool = True,
        is_offscreen: bool = False,
    ) -> None:
        self.Name = name
        self.AutomationId = automation_id
        self.ControlType = control_type
        self.ControlTypeName = control_type_name
        self.BoundingRectangle = rect or _MockRect(0, 0, 100, 40)
        self.children = children or []
        self.IsEnabled = is_enabled
        self.IsOffscreen = is_offscreen

    def GetChildren(self) -> list[_MockControl]:
        return self.children

    def GetPattern(self, _pattern_id: int) -> Any | None:
        return None


class _MockPatternId:
    InvokePattern = 10000
    ValuePattern = 10002
    TogglePattern = 10015


class _MockAuto:
    def __init__(self, fg_window: _MockControl) -> None:
        self.PatternId = _MockPatternId()
        self._fg = fg_window

    def GetForegroundControl(self) -> _MockControl:
        return self._fg

    def GetRootControl(self) -> _MockControl:
        return self._fg

    def WalkControl(
        self, parent: Any, includeTop: bool = False, maxDepth: int = 6
    ) -> Iterator[tuple[Any, int]]:
        if includeTop:
            yield parent, 0

        def _walk(node: Any, depth: int) -> Iterator[tuple[Any, int]]:
            if depth > maxDepth:
                return
            for child in node.GetChildren():
                yield child, depth
                yield from _walk(child, depth + 1)

        yield from _walk(parent, 1)


# --- 1. Тесты MonitorManager (1/2/3 монитора, отрицательные координаты, mixed DPI) ---


def test_monitor_manager_single_monitor() -> None:
    """Корректное обнаружение одного основного монитора."""
    mss_ctx = MultiMonitorMSSContext(
        monitors=[
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
        ]
    )
    mgr = MonitorManager(mss_factory=lambda: mss_ctx, dpi_provider=lambda *_: 1.0)
    monitors = mgr.get_monitors()

    assert len(monitors) == 1
    assert monitors[0].index == 1
    assert monitors[0].is_primary is True
    assert monitors[0].rect == (0, 0, 1920, 1080)
    assert mgr.norm_to_global(500, 500, monitors[0]) == (960, 540)
    assert mgr.global_to_norm(960, 540, monitors[0]) == (500, 500)


def test_monitor_manager_two_monitors_negative_left_and_mixed_dpi() -> None:
    """Поддержка левого монитора с left=-1920 и смешанного DPI (1.5 и 1.0)."""
    mss_ctx = MultiMonitorMSSContext(
        monitors=[
            {"left": -1920, "top": 0, "width": 4480, "height": 1440},
            {"left": 0, "top": 0, "width": 2560, "height": 1440},
            {"left": -1920, "top": 0, "width": 1920, "height": 1080},
        ]
    )

    def dpi_lookup(left: int, _top: int, _w: int, _h: int) -> float:
        return 1.5 if left < 0 else 1.0

    mgr = MonitorManager(mss_factory=lambda: mss_ctx, dpi_provider=dpi_lookup)
    monitors = mgr.get_monitors()

    assert len(monitors) == 2
    mon1, mon2 = monitors[0], monitors[1]
    assert mon1.is_primary is True
    assert mon1.dpi_scale == 1.0
    assert mon2.is_primary is False
    assert mon2.left == -1920
    assert mon2.dpi_scale == 1.5

    assert mgr.get_monitor_for_point(-960, 540).index == 2
    assert mgr.get_monitor_for_point(1280, 720).index == 1

    # Центр левого монитора (-1920..0, 0..1080) -> (-960, 540)
    assert mgr.norm_to_global(500, 500, mon2) == (-960, 540)
    assert mgr.norm_to_global(0, 0, mon2) == (-1920, 0)
    assert mgr.global_to_norm(-960, 540, mon2) == (500, 500)
    assert mgr.global_to_norm(-1920, 0, mon2) == (0, 0)


def test_monitor_manager_three_monitors_and_active_resolution() -> None:
    """Конфигурация из 3 мониторов (отрицательные left и top) и поиск активного монитора."""
    monitors_data = [
        MonitorInfo(
            index=1, left=0, top=0, width=2560, height=1440, dpi_scale=1.0, is_primary=True
        ),
        MonitorInfo(index=2, left=-1920, top=0, width=1920, height=1080, dpi_scale=1.25),
        MonitorInfo(index=3, left=2560, top=-360, width=1920, height=1080, dpi_scale=1.5),
    ]

    # Активное окно на 3-м мониторе (top < 0)
    mgr_fg = MonitorManager(
        monitors=monitors_data,
        foreground_rect_fn=lambda: (2700, -300, 3800, 500),
        cursor_pos_fn=lambda: (100, 100),
    )
    active_fg = mgr_fg.get_active_monitor(prefer="foreground")
    assert active_fg.index == 3
    assert mgr_fg.norm_to_global(500, 500, active_fg) == (3520, 180)
    assert mgr_fg.global_to_norm(3520, 180, active_fg) == (500, 500)

    # При prefer="cursor" выбирается монитор под курсором (монитор 2)
    mgr_cur = MonitorManager(
        monitors=monitors_data,
        foreground_rect_fn=lambda: (2700, -300, 3800, 500),
        cursor_pos_fn=lambda: (-500, 300),
    )
    assert mgr_cur.get_active_monitor(prefer="cursor").index == 2

    # Свернутое окно (-32000, -32000) игнорируется с откатом на курсор
    mgr_min = MonitorManager(
        monitors=monitors_data,
        foreground_rect_fn=lambda: (-32000, -32000, -31840, -31972),
        cursor_pos_fn=lambda: (-1000, 500),
    )
    assert mgr_min.get_active_monitor().index == 2

    # Точка вне границ мониторов притягивается к ближайшему монитору
    assert mgr_min.get_monitor_for_point(-2500, 500).index == 2
    assert mgr_min.get_monitor_for_point(5000, -500).index == 3


def test_monitor_manager_mss_failure_fallback() -> None:
    """При сбое MSS MonitorManager возвращает безопасный основной монитор 1920x1080."""

    def broken_mss() -> Any:
        raise RuntimeError("MSS unavailable")

    mgr = MonitorManager(mss_factory=broken_mss, dpi_provider=lambda *_: 1.0)
    monitors = mgr.get_monitors()
    assert len(monitors) == 1
    assert monitors[0].index == 1
    assert monitors[0].size if hasattr(monitors[0], "size") else monitors[0].width == 1920


# --- 2. Тесты Per-Monitor DPI v2 и ScreenCapture ---


def test_set_dpi_awareness_v2_and_fallbacks() -> None:
    """Проверка цепочки SetProcessDpiAwarenessContext(-4) -> shcore -> user32."""
    with patch("os.name", "nt"), patch("ctypes.windll") as mock_windll:
        mock_windll.user32.SetProcessDpiAwarenessContext.return_value = True
        assert set_dpi_awareness() is True
        mock_windll.user32.SetProcessDpiAwarenessContext.assert_called_once()

    # Fallback на shcore при False от SetProcessDpiAwarenessContext
    with patch("os.name", "nt"), patch("ctypes.windll") as mock_windll:
        mock_windll.user32.SetProcessDpiAwarenessContext.return_value = False
        mock_windll.shcore.SetProcessDpiAwareness.return_value = 0
        assert set_dpi_awareness() is True
        mock_windll.shcore.SetProcessDpiAwareness.assert_called_once_with(2)

    # Fallback на SetProcessDPIAware при сбое Context и shcore
    with patch("os.name", "nt"), patch("ctypes.windll") as mock_windll:
        mock_windll.user32.SetProcessDpiAwarenessContext.side_effect = AttributeError("old win")
        mock_windll.shcore.SetProcessDpiAwareness.side_effect = OSError("no shcore")
        mock_windll.user32.SetProcessDPIAware.return_value = 1
        assert set_dpi_awareness() is True
        mock_windll.user32.SetProcessDPIAware.assert_called_once()


def test_screen_capture_second_monitor_metadata_and_offsets() -> None:
    """Захват 2-го монитора сохраняет его index, left/top, dpi_scale и габариты."""
    mss_ctx = MultiMonitorMSSContext(
        monitors=[
            {"left": -1920, "top": 0, "width": 3840, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": -1920, "top": 0, "width": 1920, "height": 1080},
        ]
    )
    mon_mgr = MonitorManager(
        mss_factory=lambda: mss_ctx,
        dpi_provider=lambda left, *_: 1.5 if left < 0 else 1.0,
        foreground_rect_fn=lambda: (-1500, 100, -500, 700),
    )
    capture = ScreenCapture(mss_factory=lambda: mss_ctx, auto_dpi=False, monitor_manager=mon_mgr)

    frame_m2 = capture.capture_frame(monitor_index=2)
    assert frame_m2.monitor_index == 2
    assert frame_m2.left == -1920
    assert frame_m2.top == 0
    assert frame_m2.width == 1920
    assert frame_m2.height == 1080
    assert frame_m2.dpi_scale == 1.5
    assert frame_m2.rect == (-1920, 0, 1920, 1080)

    # Захват активного монитора автоматически выбирает 2-й монитор
    active_frame = capture.capture_active_monitor()
    assert active_frame.monitor_index == 2
    assert active_frame.left == -1920

    # Проверка смещений в norm_to_screen / screen_to_norm
    assert norm_to_screen(500, 500, 1920, 1080, left=-1920, top=-360) == (-960, 180)
    assert screen_to_norm(-960, 180, 1920, 1080, left=-1920, top=-360) == (500, 500)


# --- 3. Тесты VisionAgent на 2-м мониторе ---


def test_vision_agent_click_and_dhash_on_monitor_2() -> None:
    """Клик на 2-м мониторе бьет в отрицательные координаты и проверяет dhash того же монитора."""
    # Монитор 1 не меняется (static 10), Монитор 2 меняется после клика (20 -> 180)
    mss_ctx = MultiMonitorMSSContext(
        monitors=[
            {"left": -1920, "top": 0, "width": 3840, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": -1920, "top": 0, "width": 1920, "height": 1080},
        ],
        frame_bytes_by_index={1: [10, 10], 2: [20, 180]},
    )
    mon_mgr = MonitorManager(
        mss_factory=lambda: mss_ctx,
        dpi_provider=lambda left, *_: 1.5 if left < 0 else 1.0,
        foreground_rect_fn=lambda: (100, 100, 500, 500),  # Активен монитор 1, но кликаем в 2!
    )
    capture = ScreenCapture(mss_factory=lambda: mss_ctx, auto_dpi=False, monitor_manager=mon_mgr)
    mock_pyautogui = MagicMock()
    mock_pyautogui.position.return_value = (0, 0)

    agent = VisionAgent(
        screen_capture=capture,
        pyautogui_module=mock_pyautogui,
        mouse_move_duration=0.0,
        mouse_move_steps=1,
        post_click_delay_s=0.0,
    )

    assert agent.norm_to_screen(500, 500, monitor_index=2) == (-960, 540)
    assert agent.click_at(500, 500, verify_change=True, monitor_index=2) is True

    mock_pyautogui.click.assert_called_once_with(-960, 540, button="left", clicks=1)
    # Оба захвата для dhash были сняты именно со 2-го монитора (left=-1920)
    assert len(mss_ctx.grabbed_monitors) == 2
    assert all(m["left"] == -1920 for m in mss_ctx.grabbed_monitors)


def test_vision_agent_unnormalized_click_resolves_monitor_dhash_and_inspect() -> None:
    """Глобальный клик по (-960, 540) проверяет dhash монитора 2 и метаданные inspect_screen."""
    mss_ctx = MultiMonitorMSSContext(
        monitors=[
            {"left": -1920, "top": 0, "width": 3840, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": -1920, "top": 0, "width": 1920, "height": 1080},
        ],
        frame_bytes_by_index={1: [10, 10], 2: [50, 50]},
    )
    mon_mgr = MonitorManager(
        mss_factory=lambda: mss_ctx,
        dpi_provider=lambda left, *_: 1.5 if left < 0 else 1.0,
        foreground_rect_fn=lambda: (-1500, 100, -500, 700),
    )
    capture = ScreenCapture(mss_factory=lambda: mss_ctx, auto_dpi=False, monitor_manager=mon_mgr)
    mock_pyautogui = MagicMock()
    mock_pyautogui.position.return_value = (-1000, 500)

    agent = VisionAgent(
        screen_capture=capture,
        pyautogui_module=mock_pyautogui,
        mouse_move_duration=0.0,
        mouse_move_steps=1,
        max_click_attempts=1,
        post_click_delay_s=0.0,
    )

    # На мониторе 2 хеш не изменился (50 -> 50) -> ActionFailedError
    with pytest.raises(ActionFailedError):
        agent.click_at(-960, 540, normalized=False, verify_change=True)
    assert all(m["left"] == -1920 for m in mss_ctx.grabbed_monitors)

    # inspect_screen(monitor_index=2) возвращает расширенную геометрию
    info = agent.inspect_screen(monitor_index=2)
    assert info["monitor_index"] == 2
    assert info["left"] == -1920
    assert info["top"] == 0
    assert info["dpi_scale"] == 1.5
    assert info["width"] == 1920
    assert info["height"] == 1080


# --- 4. Тесты UIAutomationManager (get_active_window_rect и фильтрация по монитору) ---


def test_uia_get_active_window_rect_and_monitor_filtering() -> None:
    """Фильтрация видимых интерактивных контролов на целевом мониторе и нормализация [0..1000]."""
    mon2 = MonitorInfo(index=2, left=-1920, top=0, width=1920, height=1080, dpi_scale=1.5)

    # Глубоко вложенная кнопка (depth=5, как в Electron/WinUI) внутри монитора 2
    deep_btn = _MockControl(
        name="Отправить",
        automation_id="btn_send",
        control_type=CONTROL_TYPE_MAP["button"],
        control_type_name="ButtonControl",
        rect=_MockRect(-1160, 440, -760, 640),  # центр (-960, 540) -> norm (500, 500)
    )
    nested_pane = _MockControl(
        name="L4",
        control_type=CONTROL_TYPE_MAP["pane"],
        control_type_name="PaneControl",
        children=[deep_btn],
    )
    l3 = _MockControl(name="L3", control_type=CONTROL_TYPE_MAP["pane"], children=[nested_pane])
    l2 = _MockControl(name="L2", control_type=CONTROL_TYPE_MAP["pane"], children=[l3])

    # Контрол на другом мониторе (monitor 1) — должен быть отфильтрован
    other_mon_btn = _MockControl(
        name="На главном",
        control_type=CONTROL_TYPE_MAP["button"],
        control_type_name="ButtonControl",
        rect=_MockRect(200, 200, 400, 250),
    )
    # Отключенный, скрытый за экраном, нулевой и неинтерактивный Text контролы
    disabled_btn = _MockControl(
        name="Недоступно",
        control_type=CONTROL_TYPE_MAP["button"],
        rect=_MockRect(-1800, 100, -1600, 150),
        is_enabled=False,
    )
    offscreen_btn = _MockControl(
        name="Скрыто",
        control_type=CONTROL_TYPE_MAP["button"],
        rect=_MockRect(-1800, 200, -1600, 250),
        is_offscreen=True,
    )
    zero_edit = _MockControl(
        name="Нулевое поле",
        control_type=CONTROL_TYPE_MAP["edit"],
        rect=_MockRect(-1500, 200, -1500, 200),
    )
    static_text = _MockControl(
        name="Подпись",
        control_type=CONTROL_TYPE_MAP["text"],
        control_type_name="TextControl",
        rect=_MockRect(-1800, 300, -1600, 330),
    )

    fg_win = _MockControl(
        name="AppWindow",
        control_type=CONTROL_TYPE_MAP["window"],
        control_type_name="WindowControl",
        rect=_MockRect(-1900, 20, -20, 1040),
        children=[l2, other_mon_btn, disabled_btn, offscreen_btn, zero_edit, static_text],
    )
    mock_auto = _MockAuto(fg_win)
    uia = UIAutomationManager(auto_module=mock_auto)

    # Проверяем get_active_window_rect с откатом на UIA BoundingRectangle
    with patch("core.uia.uia_controller.win32gui.GetForegroundWindow", return_value=0):
        rect = uia.get_active_window_rect()
    assert rect == {
        "left": -1900,
        "top": 20,
        "right": -20,
        "bottom": 1040,
        "width": 1880,
        "height": 1020,
    }

    # Инспекция с max_depth=6 и фильтром по mon2
    elements = uia.inspect_window_elements(monitor=mon2, max_depth=6)
    assert len(elements) == 1
    el = elements[0]
    assert el["name"] == "Отправить"
    assert el["global_center"] == (-960, 540)
    assert el["norm_center"] == (500, 500)
    assert el["monitor_index"] == 2
    assert len(el["norm_bbox"]) == 4


# --- 5. Тесты SoMAnnotator ---


def test_som_annotator_draws_tags_and_builds_manifest_and_lookup() -> None:
    """SoMAnnotator рисует метки на кадре, строит манифест LLM и словарь id -> SoMElement."""
    mon2 = MonitorInfo(index=2, left=-1920, top=0, width=800, height=600, dpi_scale=1.5)
    blank = Image.new("RGB", (800, 600), (20, 20, 20))
    frame = ScreenFrame(
        width=800,
        height=600,
        rgb=blank.tobytes(),
        monitor_index=2,
        left=-1920,
        top=0,
        dpi_scale=1.5,
    )

    uia_elements = [
        {
            "name": "Сохранить",
            "control_type": "ButtonControl",
            "automation_id": "btn_save",
            "rect": {"left": -1520, "top": 250, "right": -1320, "bottom": 350},
            "monitor_index": 2,
        },
        {
            "name": "Поиск",
            "control_type": "EditControl",
            "automation_id": "txt_search",
            "rect": {"left": -1820, "top": 60, "right": -1420, "bottom": 120},
            "monitor_index": 2,
        },
    ]

    annotator = SoMAnnotator()
    result = annotator.annotate_frame(frame, uia_elements, monitor=mon2)

    assert len(result.elements) == 2
    el1 = result.lookup[1]
    assert isinstance(el1, SoMElement)
    assert el1.mark_id == 1
    assert el1.name == "Сохранить"
    assert el1.control_type == "Button"
    assert el1.global_x == -1420
    assert el1.global_y == 300
    assert el1.monitor_index == 2
    assert annotator.get_element(2) == result.lookup[2]

    # Манифест для LLM содержит номера, типы и имена элементов
    assert '[1] Button "Сохранить"' in result.manifest
    assert '[2] Edit "Поиск"' in result.manifest

    # Изображение реально изменено (нарисованы рамки и бейджи)
    assert result.frame.rgb != frame.rgb
    assert result.frame.dhash != frame.dhash
    assert result.image.size == (800, 600)


def test_som_annotator_empty_and_max_elements_limit() -> None:
    """Граничные случаи SoMAnnotator: пустой список и ограничение max_elements."""
    annotator = SoMAnnotator(max_elements=2)
    img = Image.new("RGB", (400, 300), (0, 0, 0))

    empty_res = annotator.annotate_image(img, [])
    assert empty_res.elements == []
    assert empty_res.lookup == {}
    assert "не найдены" in empty_res.manifest

    many = [
        {
            "name": f"Btn {i}",
            "control_type": "ButtonControl",
            "rect": {"left": 10 * i, "top": 10, "right": 10 * i + 40, "bottom": 40},
        }
        for i in range(5)
    ]
    capped = annotator.annotate_image(img, many)
    assert len(capped.elements) == 2
    assert list(capped.lookup.keys()) == [1, 2]
