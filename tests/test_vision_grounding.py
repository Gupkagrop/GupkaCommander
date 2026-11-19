"""Набор модульных тестов для подсистемы Vision Grounding (ScreenCapture и VisionAgent)."""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.failsafe import FailSafeMonitor
from core.vision.screen_capture import (
    ScreenCapture,
    ScreenFrame,
    compute_dhash,
    hamming_distance,
    norm_to_screen,
    screen_to_norm,
    set_dpi_awareness,
)
from core.vision.vision_agent import ActionFailedError, VisionAgent


class MockSctImage:
    """Мок объекта захваченного изображения MSS."""

    def __init__(self, width: int = 100, height: int = 100, pattern: int = 128) -> None:
        self.size = (width, height)
        # 3 байта на пиксель (RGB)
        self.rgb = bytes([pattern] * (width * height * 3))


class MockMSSContext:
    """Мок контекстного менеджера MSS."""

    def __init__(self, sct_img: MockSctImage | None = None) -> None:
        self.monitors = [
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
        ]
        self._sct_img = sct_img or MockSctImage()

    def __enter__(self) -> MockMSSContext:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

    def grab(self, monitor: dict[str, Any]) -> MockSctImage:
        return self._sct_img


# --- Тесты преобразования координат ---


def test_norm_to_screen_standard() -> None:
    """Проверка стандартного маппинга нормализованных координат в пиксели экрана."""
    # Центр экрана 1920x1080
    x, y = norm_to_screen(500, 500, 1920, 1080, norm_max=1000)
    assert x == 960
    assert y == 540

    # Левый верхний угол
    assert norm_to_screen(0, 0, 1920, 1080) == (0, 0)
    # Правый нижний угол
    assert norm_to_screen(1000, 1000, 1920, 1080) == (1920, 1080)


def test_norm_to_screen_clamping() -> None:
    """Проверка отсечения координат за пределами допустимого диапазона."""
    assert norm_to_screen(-100, -200, 1920, 1080) == (0, 0)
    assert norm_to_screen(2000, 1500, 1920, 1080) == (1920, 1080)


def test_norm_to_screen_invalid_params() -> None:
    """Проверка генерации исключений при некорректных аргументах геометрии."""
    with pytest.raises(ValueError, match="norm_max"):
        norm_to_screen(100, 100, 1920, 1080, norm_max=0)

    with pytest.raises(ValueError, match="screen_width"):
        norm_to_screen(100, 100, 0, 1080)


def test_screen_to_norm_standard() -> None:
    """Проверка обратного преобразования физических координат в нормализованный диапазон."""
    assert screen_to_norm(960, 540, 1920, 1080) == (500, 500)
    assert screen_to_norm(0, 0, 1920, 1080) == (0, 0)
    assert screen_to_norm(1920, 1080, 1920, 1080) == (1000, 1000)


def test_screen_to_norm_clamping_and_validation() -> None:
    """Проверка отсечения и валидации для функции screen_to_norm."""
    assert screen_to_norm(-50, -100, 1920, 1080) == (0, 0)
    assert screen_to_norm(3000, 2000, 1920, 1080) == (1000, 1000)

    with pytest.raises(ValueError):
        screen_to_norm(100, 100, 1920, 0)


# --- Тесты хеширования и dhash ---


def test_compute_dhash_and_hamming() -> None:
    """Проверка детерминированности dhash и расстояния Хэмминга."""
    w, h = 64, 64
    # Кадр 1: горизонтальный градиент
    img1 = bytearray()
    for row in range(h):
        for col in range(w):
            img1.extend([col * 3, col * 3, col * 3])
    hash1 = compute_dhash(bytes(img1), w, h)

    # Повторный расчет того же изображения должен давать идентичный хеш
    hash1_repeat = compute_dhash(bytes(img1), w, h)
    assert hash1 == hash1_repeat
    assert hamming_distance(hash1, hash1_repeat) == 0

    # Кадр 2: инвертированный градиент
    img2 = bytearray()
    for row in range(h):
        for col in range(w):
            img2.extend([255 - col * 3, 255 - col * 3, 255 - col * 3])
    hash2 = compute_dhash(bytes(img2), w, h)

    assert hash1 != hash2
    assert hamming_distance(hash1, hash2) > 0


def test_compute_dhash_empty_and_corrupt() -> None:
    """Проверка устойчивости dhash к пустым или поврежденным данным."""
    zero_hash = compute_dhash(b"", 0, 0)
    assert zero_hash == "0000000000000000"
    assert hamming_distance("", "1234") == 64
    assert hamming_distance("invalid_hex", "1234") == 64


# --- Тесты ScreenFrame ---


def test_screen_frame_properties() -> None:
    """Проверка контейнера ScreenFrame и ленивой генерации Base64."""
    sample_rgb = bytes([100] * (16 * 16 * 3))
    frame = ScreenFrame(width=16, height=16, rgb=sample_rgb, dhash="abcdef0123456789")

    assert frame.size == (16, 16)
    assert frame.dhash == "abcdef0123456789"
    assert len(frame.png_bytes) > 0
    assert len(frame.b64) > 0
    assert frame.base64_image == frame.b64

    # Декодирование base64 должно возвращать валидные PNG байты
    decoded = base64.b64decode(frame.b64)
    assert decoded == frame.png_bytes


# --- Тесты ScreenCapture ---


def test_set_dpi_awareness_mocked() -> None:
    """Проверка активации DPI-осведомленности с моками ctypes."""
    with patch("os.name", "nt"), patch("ctypes.windll") as mock_windll:
        mock_windll.shcore.SetProcessDpiAwareness.return_value = 0
        assert set_dpi_awareness() is True

    # Fallback на user32 при сбое shcore
    with patch("os.name", "nt"), patch("ctypes.windll") as mock_windll:
        mock_windll.shcore.SetProcessDpiAwareness.side_effect = AttributeError("No shcore")
        mock_windll.user32.SetProcessDPIAware.return_value = 1
        assert set_dpi_awareness() is True

    # Не-Windows платформа
    with patch("os.name", "posix"):
        assert set_dpi_awareness() is False


def test_screen_capture_mocked_flow() -> None:
    """Проверка захвата экрана через мок MSS."""
    mock_sct = MockMSSContext(MockSctImage(pattern=200))
    capture = ScreenCapture(mss_factory=lambda: mock_sct, auto_dpi=False)

    size = capture.get_screen_size()
    assert size == (1920, 1080)

    frame = capture.capture_frame(include_base64=True)
    assert frame.width == 100
    assert frame.height == 100
    assert len(frame.dhash) == 16
    assert len(frame.b64) > 0

    b64_str = capture.capture_base64()
    assert len(b64_str) > 0


def test_screen_capture_fallback_on_error() -> None:
    """Проверка отказоустойчивости захвата экрана при исключении драйвера дисплея."""

    class FailingMSSContext:
        monitors = [{"width": 1920, "height": 1080}, {"width": 1920, "height": 1080}]

        def __enter__(self) -> FailingMSSContext:
            return self

        def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            pass

        def grab(self, mon: Any) -> Any:
            raise RuntimeError("GDI BitBlt failed")

    capture = ScreenCapture(mss_factory=FailingMSSContext, auto_dpi=False)
    frame = capture.capture_frame()
    # Возвращается резервный кадр без падения приложения
    assert frame.width == 1920
    assert frame.height == 1080
    assert frame.rgb == b"\x00" * (1920 * 1080 * 3)


# --- Тесты VisionAgent ---


def test_vision_agent_move_to_smooth() -> None:
    """Проверка плавного перемещения курсора мыши."""
    mock_pyautogui = MagicMock()
    mock_pyautogui.position.return_value = (100, 100)

    monitor = FailSafeMonitor()
    agent = VisionAgent(
        monitor=monitor,
        pyautogui_module=mock_pyautogui,
        mouse_move_duration=0.01,
        mouse_move_steps=4,
    )

    agent.move_to(500, 500, smooth=True)
    # Проверяем, что moveTo вызывался несколько раз для сглаживания
    assert mock_pyautogui.moveTo.call_count == 4
    # Финальный вызов перемещает в целевую точку
    assert mock_pyautogui.moveTo.call_args[0] == (500, 500)


def test_vision_agent_move_to_direct() -> None:
    """Проверка прямого (мгновенного) перемещения курсора без сглаживания."""
    mock_pyautogui = MagicMock()
    agent = VisionAgent(pyautogui_module=mock_pyautogui)

    agent.move_to(250, 350, smooth=False)
    mock_pyautogui.moveTo.assert_called_once_with(250, 350)


def test_vision_agent_click_at_without_verify() -> None:
    """Проверка одиночного клика без постпроверки изменения экрана."""
    mock_pyautogui = MagicMock()
    mock_capture = MagicMock()
    mock_capture.get_screen_size.return_value = (1000, 1000)
    mock_capture.norm_to_screen.return_value = (500, 500)

    agent = VisionAgent(
        screen_capture=mock_capture,
        pyautogui_module=mock_pyautogui,
    )

    result = agent.click_at(500, 500, verify_change=False)
    assert result is True
    mock_pyautogui.click.assert_called_once_with(500, 500, button="left", clicks=1)


def test_vision_agent_click_at_with_verify_success() -> None:
    """Проверка успешного клика при фиксации изменения хеша экрана."""
    mock_pyautogui = MagicMock()
    mock_capture = MagicMock()
    mock_capture.get_screen_size.return_value = (1920, 1080)
    mock_capture.norm_to_screen.return_value = (960, 540)

    # До клика хеш "1111", после клика хеш "2222"
    frame_before = ScreenFrame(width=100, height=100, rgb=b"", dhash="1111")
    frame_after = ScreenFrame(width=100, height=100, rgb=b"", dhash="2222")
    mock_capture.capture_frame.side_effect = [frame_before, frame_after]

    agent = VisionAgent(
        screen_capture=mock_capture,
        pyautogui_module=mock_pyautogui,
        post_click_delay_s=0.0,
    )

    result = agent.click_at(500, 500, verify_change=True, max_attempts=2)
    assert result is True
    mock_pyautogui.click.assert_called_once()


def test_vision_agent_click_at_failure_raises_action_failed() -> None:
    """Проверка выброса ActionFailedError, если экран не изменился за лимит попыток."""
    mock_pyautogui = MagicMock()
    mock_capture = MagicMock()
    mock_capture.get_screen_size.return_value = (1920, 1080)
    mock_capture.norm_to_screen.return_value = (960, 540)

    # Кадр экрана не меняется (хеш всегда идентичен)
    stale_frame = ScreenFrame(width=100, height=100, rgb=b"", dhash="static_hash")
    mock_capture.capture_frame.return_value = stale_frame

    agent = VisionAgent(
        screen_capture=mock_capture,
        pyautogui_module=mock_pyautogui,
        max_click_attempts=3,
        post_click_delay_s=0.0,
    )

    with pytest.raises(ActionFailedError, match="Интерфейс не отреагировал"):
        agent.click_at(500, 500, verify_change=True)

    # Должно быть выполнено ровно 3 попытки клика
    assert mock_pyautogui.click.call_count == 3


def test_vision_agent_failsafe_abort() -> None:
    """Проверка немедленного аварийного прерывания при срабатывании FailSafe."""
    mock_pyautogui = MagicMock()
    monitor = FailSafeMonitor()
    agent = VisionAgent(monitor=monitor, pyautogui_module=mock_pyautogui)

    # Взводим аварийный сигнал
    monitor.trigger_abort("Escape key pressed")

    with pytest.raises(InterruptedError, match="FailSafe abort"):
        agent.move_to(100, 100)

    with pytest.raises(InterruptedError, match="FailSafe abort"):
        agent.type_text("test")


def test_vision_agent_type_text_and_keys() -> None:
    """Проверка вызова методов ввода текста и нажатия функциональных клавиш."""
    mock_pyautogui = MagicMock()
    agent = VisionAgent(pyautogui_module=mock_pyautogui)

    agent.type_text("Hello World")
    mock_pyautogui.write.assert_called_once_with("Hello World", interval=0.0)

    agent.press_key("enter", presses=2)
    mock_pyautogui.press.assert_called_once_with("enter", presses=2)

    agent.hotkey("ctrl", "shift", "esc")
    mock_pyautogui.hotkey.assert_called_once_with("ctrl", "shift", "esc")

    # Пустые параметры не вызывают pyautogui
    mock_pyautogui.reset_mock()
    agent.type_text("")
    agent.press_key("")
    agent.hotkey()
    assert mock_pyautogui.write.call_count == 0
    assert mock_pyautogui.press.call_count == 0
    assert mock_pyautogui.hotkey.call_count == 0


def test_vision_agent_type_text_cyrillic_clipboard() -> None:
    """Проверка ввода кириллицы через буфер обмена и комбинацию Ctrl+V."""
    mock_pyautogui = MagicMock()
    mock_clipboard = MagicMock(return_value=True)
    agent = VisionAgent(pyautogui_module=mock_pyautogui, clipboard_fn=mock_clipboard)

    agent.type_text("Привет мир")
    mock_clipboard.assert_called_once_with("Привет мир")
    mock_pyautogui.hotkey.assert_called_once_with("ctrl", "v")
    assert mock_pyautogui.write.call_count == 0


def test_vision_agent_type_text_cyrillic_clipboard_failure_fallback() -> None:
    """Проверка отката на pyautogui.write при сбое буфера обмена для кириллицы."""
    mock_pyautogui = MagicMock()
    mock_clipboard = MagicMock(return_value=False)
    agent = VisionAgent(pyautogui_module=mock_pyautogui, clipboard_fn=mock_clipboard)

    agent.type_text("Тестовый текст", interval=0.05)
    mock_clipboard.assert_called_once_with("Тестовый текст")
    mock_pyautogui.hotkey.assert_not_called()
    mock_pyautogui.write.assert_called_once_with("Тестовый текст", interval=0.05)


def test_vision_agent_type_text_ascii_direct_write() -> None:
    """Проверка прямого ввода ASCII-текста через pyautogui.write без буфера обмена."""
    mock_pyautogui = MagicMock()
    mock_clipboard = MagicMock()
    agent = VisionAgent(pyautogui_module=mock_pyautogui, clipboard_fn=mock_clipboard)

    agent.type_text("Hello World 123!", interval=0.01)
    mock_clipboard.assert_not_called()
    mock_pyautogui.hotkey.assert_not_called()
    mock_pyautogui.write.assert_called_once_with("Hello World 123!", interval=0.01)


def test_vision_agent_type_text_clipboard_exception_fallback() -> None:
    """Проверка отката на pyautogui.write при исключении в функции буфера обмена."""
    mock_pyautogui = MagicMock()
    mock_clipboard = MagicMock(side_effect=RuntimeError("Clipboard lock error"))
    agent = VisionAgent(pyautogui_module=mock_pyautogui, clipboard_fn=mock_clipboard)

    agent.type_text("Привет мир")
    mock_pyautogui.hotkey.assert_not_called()
    mock_pyautogui.write.assert_called_once_with("Привет мир", interval=0.0)


def test_vision_agent_click_at_unnormalized() -> None:
    """Проверка клика по прямым экранным координатам без нормализации."""
    mock_pyautogui = MagicMock()
    mock_capture = MagicMock()
    agent = VisionAgent(screen_capture=mock_capture, pyautogui_module=mock_pyautogui)

    agent.click_at(450, 600, verify_change=False, normalized=False)
    mock_capture.norm_to_screen.assert_not_called()
    mock_pyautogui.click.assert_called_once_with(450, 600, button="left", clicks=1)


def test_vision_agent_coordinate_sync() -> None:
    """Проверка синхронизации физических экранных и нормализованных [0, 1000] координат."""
    mock_capture = MagicMock()
    mock_capture.get_screen_size.return_value = (1920, 1080)
    mock_capture.norm_to_screen.side_effect = norm_to_screen
    mock_capture.screen_to_norm.side_effect = screen_to_norm

    agent = VisionAgent(screen_capture=mock_capture)

    # Проверка преобразований
    assert agent.norm_to_screen(500, 500) == (960, 540)
    assert agent.screen_to_norm(960, 540) == (500, 500)


def test_vision_agent_inspect_screen() -> None:
    """Проверка инспекции экрана для мультимодальной нейросети."""
    mock_capture = MagicMock()
    frame = ScreenFrame(
        width=1920,
        height=1080,
        rgb=b"",
        dhash="dhash123",
        _b64="data_base64_sample",
    )
    mock_capture.capture_frame.return_value = frame

    agent = VisionAgent(screen_capture=mock_capture)
    info = agent.inspect_screen()

    assert info["width"] == 1920
    assert info["height"] == 1080
    assert info["dhash"] == "dhash123"
    assert info["image_b64"] == "data_base64_sample"
    assert info["base64_image"] == "data_base64_sample"
    assert "timestamp" in info
