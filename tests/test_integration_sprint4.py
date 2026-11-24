"""
> [!NOTE]
> Интеграционные тесты Спринта 4 (tests.test_integration_sprint4).
> Проверяют:
> 1. Маршрутизацию команд: Fast-Path vs LLM Brain vs Vision Grounding.
> 2. Масштабирование экранных координат и безопасное выполнение с прерыванием FailSafe.
> 3. Сквозной цикл (End-to-end) от команды пользователя до выполнения запланированных действий.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtWidgets import QApplication

from core.app_finder import AppFinder, AppInfo
from core.brain.llm_client import LocalLLMClient
from core.failsafe import FailSafeMonitor
from core.os_controller import DangerousCommandError, OSController
from core.routing.intent_router import Intent, IntentRouter
from core.vision.screen_capture import ScreenCapture, ScreenFrame, norm_to_screen, screen_to_norm
from core.vision.vision_agent import ActionFailedError, VisionAgent
from core.voice.voice_pipeline import VoicePipeline
from gui.hotkey_manager import HotkeyManager
from gui.spotlight_bar import SpotlightBar
from gui.tray_icon import TrayManager
from main import GupkaCommanderApp


@dataclass
class AppBundle:
    """Контейнер связанных мок-компонентов приложения для типобезопасного тестирования."""

    app: GupkaCommanderApp
    mock_os: MagicMock
    mock_voice: MagicMock
    mock_finder: MagicMock
    mock_llm: MagicMock
    mock_vision_agent: VisionAgent


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Синглтон приложения QApplication для headless-тестирования."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def mock_failsafe_monitor() -> FailSafeMonitor:
    """Фикстура изолированного монитора аварийного прерывания FailSafe."""
    return FailSafeMonitor()


@pytest.fixture
def mock_screen_capture() -> MagicMock:
    """Фикстура мока захвата экрана с фиксированным разрешением 1920x1080."""
    capture = MagicMock(spec=ScreenCapture)
    capture.get_screen_size.return_value = (1920, 1080)
    capture.norm_to_screen.side_effect = lambda x, y, w, h, n=1000: norm_to_screen(
        x, y, w, h, norm_max=n
    )
    capture.screen_to_norm.side_effect = lambda x, y, w, h, n=1000: screen_to_norm(
        x, y, w, h, norm_max=n
    )
    fake_frame = ScreenFrame(
        width=1920,
        height=1080,
        rgb=b"",
        dhash="0000ffff0000ffff",
        _b64="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
    )
    capture.capture_frame.return_value = fake_frame
    return capture


@pytest.fixture
def mock_vision_agent(
    mock_screen_capture: MagicMock,
    mock_failsafe_monitor: FailSafeMonitor,
) -> VisionAgent:
    """Фикстура визуального агента с мок-автоматизацией pyautogui."""
    mock_pyautogui = MagicMock()
    mock_pyautogui.position.return_value = (100, 100)
    return VisionAgent(
        screen_capture=mock_screen_capture,
        monitor=mock_failsafe_monitor,
        pyautogui_module=mock_pyautogui,
        mouse_move_duration=0.001,
        mouse_move_steps=2,
        post_click_delay_s=0.0,
    )


@pytest.fixture
def app_bundle(
    qapp: QApplication,
    mock_failsafe_monitor: FailSafeMonitor,
    mock_vision_agent: VisionAgent,
) -> Generator[AppBundle, None, None]:
    """Фикстура экземпляра GupkaCommanderApp с моками для интеграционного тестирования."""
    mock_os = MagicMock(spec=OSController)
    mock_os.resolve_alias.return_value = None
    mock_os.run_command.return_value = (0, "Success output", "")
    mock_voice = MagicMock(spec=VoicePipeline)
    mock_finder = MagicMock(spec=AppFinder)
    mock_finder.find.return_value = []
    mock_bar = SpotlightBar(hide_on_focus_loss=False)
    mock_tray = TrayManager()
    mock_hotkey = HotkeyManager()

    mock_llm = MagicMock(spec=LocalLLMClient)
    mock_llm.is_healthy.return_value = True
    mock_llm.plan_actions.return_value = []
    mock_llm.generate_response.return_value = "Тестовый ответ локального LLM"

    router = IntentRouter()

    app_instance = GupkaCommanderApp(
        app=qapp,
        app_finder=mock_finder,
        os_controller=mock_os,
        voice_pipeline=mock_voice,
        spotlight_bar=mock_bar,
        tray_manager=mock_tray,
        hotkey_manager=mock_hotkey,
        intent_router=router,
        llm_client=mock_llm,
        vision_agent=mock_vision_agent,
        enable_voice=True,
        enable_hotkey=False,
        enable_tray=False,
    )

    yield AppBundle(
        app=app_instance,
        mock_os=mock_os,
        mock_voice=mock_voice,
        mock_finder=mock_finder,
        mock_llm=mock_llm,
        mock_vision_agent=mock_vision_agent,
    )

    app_instance.shutdown()
    mock_bar.deleteLater()
    mock_tray.deleteLater()


class TestCommandRoutingTiers:
    """Тестирование разграничения уровней маршрутизации: Fast-Path vs LLM vs Vision."""

    def test_fast_path_volume_routing(self) -> None:
        """Проверка маршрутизации команд регулировки системной громкости."""
        router = IntentRouter()
        queries = {
            "громче": "up",
            "погромче": "up",
            "прибавь громкость": "up",
            "громкость выше": "up",
            "прибавь звук": "up",
            "тише": "down",
            "потише": "down",
            "убавь громкость": "down",
            "громкость ниже": "down",
            "убавь звук": "down",
            "выключи звук": "mute",
            "без звука": "mute",
            "мут": "mute",
        }
        for query, expected_action in queries.items():
            result = router.route(query)
            assert result.intent == Intent.VOLUME_CONTROL, f"Запрос '{query}' не распознан"
            assert result.params.get("action") == expected_action

    def test_fast_path_window_routing(self) -> None:
        """Проверка маршрутизации команд управления окнами Windows."""
        router = IntentRouter()
        queries = {
            "сверни окно": "minimize",
            "разверни окно": "maximize",
            "закрой окно": "close",
            "восстанови окно": "restore",
            "сверни все окна": "minimize_all",
        }
        for query, expected_action in queries.items():
            result = router.route(query)
            assert result.intent == Intent.WINDOW_CONTROL, f"Запрос '{query}' не распознан"
            assert result.params.get("action") == expected_action

    def test_fast_path_web_and_app_routing(self) -> None:
        """Проверка открытия сайтов и приложений через быстрый путь."""
        router = IntentRouter()
        res_app = router.route("открой telegram")
        assert res_app.intent == Intent.OPEN_APP
        assert res_app.target == "telegram"

        res_url = router.route("открой ютуб")
        assert res_url.intent == Intent.OPEN_URL
        assert "youtube" in res_url.target

        res_search = router.route("найди в youtube rock")
        assert res_search.intent == Intent.SEARCH_WEB
        assert res_search.params.get("engine") == "youtube"
        assert res_search.target == "rock"

    def test_fast_path_terminal_command_routing(self) -> None:
        """Проверка маршрутизации команд терминала."""
        router = IntentRouter()
        res_term = router.route("терминал")
        assert res_term.intent == Intent.RUN_COMMAND

        res_pwsh = router.route("запусти powershell ls")
        assert res_pwsh.intent == Intent.RUN_COMMAND
        assert res_pwsh.params.get("shell") == "powershell"
        assert res_pwsh.params.get("command") == "ls"

    def test_vision_grounding_routing(self) -> None:
        """Проверка распознавания визуальных команд взаимодействия с экраном."""
        router = IntentRouter()
        res_inspect = router.route("что на экране")
        assert res_inspect.intent == Intent.VISION_INTERACT
        assert res_inspect.params.get("action") == "inspect_screen"

        res_click = router.route("кликни по кнопке отправить")
        assert res_click.intent == Intent.VISION_INTERACT
        assert res_click.params.get("action") == "click_screen"
        assert "отправить" in res_click.target

        res_type = router.route("введи текст Привет мир")
        assert res_type.intent == Intent.VISION_INTERACT
        assert res_type.params.get("action") == "type_text"
        assert "Привет мир" in res_type.target

    def test_ai_cognitive_fallback_routing(self) -> None:
        """Проверка перенаправления свободных запросов в когнитивное ядро LLM."""
        router = IntentRouter()
        complex_queries = [
            "расскажи кратко принцип работы квантового компьютера",
            "напиши скрипт на Python для парсинга HTML",
            "почему в сутках 24 часа",
        ]
        for query in complex_queries:
            result = router.route(query)
            assert result.intent == Intent.AI_COGNITIVE
            assert result.target == query


class TestScreenCoordinateScalingAndFailSafe:
    """Тестирование масштабирования координат и прерывания через FailSafe."""

    @pytest.mark.parametrize(
        ("width", "height"),
        [
            (1920, 1080),
            (2560, 1440),
            (3840, 2160),
            (1280, 720),
        ],
    )
    def test_coordinate_scaling_various_resolutions(self, width: int, height: int) -> None:
        """Проверка точности прямого и обратного масштабирования координат."""
        top_left = norm_to_screen(0, 0, width, height)
        assert top_left == (0, 0)

        bottom_right = norm_to_screen(1000, 1000, width, height)
        assert bottom_right == (width, height)

        center = norm_to_screen(500, 500, width, height)
        assert center == (width // 2, height // 2)

        # Проверка обратного преобразования
        norm_back = screen_to_norm(center[0], center[1], width, height)
        assert abs(norm_back[0] - 500) <= 2
        assert abs(norm_back[1] - 500) <= 2

    def test_coordinate_clamping_out_of_bounds(self) -> None:
        """Проверка ограничения координат за пределами диапазона [0, 1000]."""
        clamped_low = norm_to_screen(-100, -50, 1920, 1080)
        assert clamped_low == (0, 0)

        clamped_high = norm_to_screen(1500, 2000, 1920, 1080)
        assert clamped_high == (1920, 1080)

    def test_failsafe_abort_interrupts_vision_agent_click(
        self,
        mock_screen_capture: MagicMock,
        mock_failsafe_monitor: FailSafeMonitor,
    ) -> None:
        """Проверка немедленного прерывания клика мыши при взведенном флаге FailSafe."""
        mock_pyautogui = MagicMock()
        agent = VisionAgent(
            screen_capture=mock_screen_capture,
            monitor=mock_failsafe_monitor,
            pyautogui_module=mock_pyautogui,
        )

        mock_failsafe_monitor.trigger_abort("Escape key pressed by user")
        assert mock_failsafe_monitor.is_aborted() is True

        with pytest.raises(InterruptedError) as exc_info:
            agent.click_at(500, 500)
        assert "FailSafe abort" in str(exc_info.value)
        mock_pyautogui.click.assert_not_called()

    def test_failsafe_abort_interrupts_vision_agent_movement(
        self,
        mock_screen_capture: MagicMock,
        mock_failsafe_monitor: FailSafeMonitor,
    ) -> None:
        """Проверка прерывания перемещения курсора мыши при срабатывании FailSafe."""
        mock_pyautogui = MagicMock()
        mock_pyautogui.position.return_value = (0, 0)
        agent = VisionAgent(
            screen_capture=mock_screen_capture,
            monitor=mock_failsafe_monitor,
            pyautogui_module=mock_pyautogui,
            mouse_move_steps=5,
        )

        mock_failsafe_monitor.trigger_abort("Mouse shake detected")
        with pytest.raises(InterruptedError):
            agent.move_to(800, 600, smooth=True)

    def test_failsafe_abort_caught_safely_in_application(
        self,
        app_bundle: AppBundle,
        mock_failsafe_monitor: FailSafeMonitor,
    ) -> None:
        """Проверка безопасной изоляции InterruptedError внутри execute_command."""
        mock_failsafe_monitor.trigger_abort("Emergency operator stop")

        response = app_bundle.app.execute_command("кликни на экран")
        assert "FailSafe" in response
        assert "экстренно прервана" in response

        # Проверяем отображение в виджете диалога ChatDrawer
        drawer = app_bundle.app.spotlight_bar.chat_drawer
        assert drawer.get_messages_count() >= 2
        last_card = drawer._cards[-1]
        assert "FailSafe" in last_card.get_text()


class TestEndToEndExecutionFlow:
    """Тестирование сквозного контура исполнения команд и вызовов инструментов."""

    def test_fast_path_direct_volume_execution(self, app_bundle: AppBundle) -> None:
        """Проверка прямого вызова изменения громкости без обращения к LLM."""
        response = app_bundle.app.execute_command("сделай громче")
        assert "увеличена" in response
        app_bundle.mock_os.volume_up.assert_called_once()
        app_bundle.mock_voice.speak.assert_called_once_with(response, blocking=False)

    def test_fast_path_window_minimize_execution(self, app_bundle: AppBundle) -> None:
        """Проверка прямого вызова сворачивания окна."""
        response = app_bundle.app.execute_command("сверни окно")
        assert "свернуто" in response
        app_bundle.mock_os.minimize_window.assert_called_once_with("")

    def test_fast_path_app_launch_execution(self, app_bundle: AppBundle) -> None:
        """Проверка поиска и запуска установленной программы через AppFinder."""
        fake_app = AppInfo(name="Telegram Desktop", path=r"C:\Apps\Telegram.exe", source="test")
        app_bundle.mock_finder.find.return_value = [fake_app]

        with patch.object(app_bundle.app, "_launch_app", return_value=True) as mock_launch:
            response = app_bundle.app.execute_command("открой telegram")
            assert "Telegram Desktop" in response
            mock_launch.assert_called_once_with(fake_app.path)

    def test_llm_brain_action_planning_flow(self, app_bundle: AppBundle) -> None:
        """Проверка выполнения последовательности системных действий, спланированных LLM."""
        planned_actions: list[dict[str, Any]] = [
            {
                "tool": "window_control",
                "parameters": {"action": "minimize_all"},
            },
            {
                "tool": "search_web",
                "parameters": {"query": "новости космоса", "engine": "google"},
            },
        ]
        app_bundle.mock_llm.plan_actions.side_effect = [
            planned_actions,
            [
                {
                    "tool": "finish_task",
                    "parameters": {"summary": "Все окна свернуты, выполнен поиск новости космоса."},
                }
            ],
        ]

        response = app_bundle.app.execute_command("сверни все окна и найди новости космоса")

        app_bundle.mock_os.minimize_all.assert_called_once()
        app_bundle.mock_os.search.assert_called_once_with(query="новости космоса", engine="google")
        assert "свернуты" in response
        assert "новости космоса" in response

    def test_vision_grounding_inspect_and_click_flow(
        self,
        app_bundle: AppBundle,
    ) -> None:
        """Проверка сквозного визуального клика через инспекцию экрана и планировщик."""
        planned_actions = [
            {
                "tool": "click_screen",
                "parameters": {"x": 500, "y": 400, "button": "left"},
            }
        ]
        app_bundle.mock_llm.plan_actions.return_value = planned_actions

        with patch.object(app_bundle.app.vision_agent, "click_at", return_value=True) as mock_click:
            response = app_bundle.app.execute_command("кликни на синюю кнопку")
            mock_click.assert_called_once_with(500, 400, button="left")
            assert "500" in response and "400" in response

    def test_vision_grounding_action_failed_recovery(
        self,
        app_bundle: AppBundle,
    ) -> None:
        """Проверка перехвата ActionFailedError при отсутствии реакции интерфейса."""
        with patch.object(
            app_bundle.app.vision_agent,
            "click_at",
            side_effect=ActionFailedError("Интерфейс не отреагировал на клик"),
        ):
            response = app_bundle.app.execute_command("кликни по координатам 100 200")
            assert "Не удалось выполнить визуальное действие" in response
            assert "не отреагировал" in response

    def test_dangerous_command_blocked_by_safety(self, app_bundle: AppBundle) -> None:
        """Проверка блокировки потенциально опасной терминальной команды."""
        app_bundle.mock_os.run_command.side_effect = DangerousCommandError(
            "Обнаружен деструктивный шаблон"
        )
        response = app_bundle.app.execute_command("выполни команду format c:")
        assert "заблокирована системой безопасности" in response
        assert "деструктивный шаблон" in response
