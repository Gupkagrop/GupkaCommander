"""Модульные и интеграционные тесты Фазы 3 GupkaCommander 2.0.

Покрывают:
1. 3-уровневую маршрутизацию ExecutionTier (CASUAL_CHAT, FAST_PATH, AUTONOMOUS_AGENT).
2. Лёгковесный BrowserCDPBridge (HTTP discovery, RFC6455 WebSocket, DOM-манифест, клик/ввод).
3. StepVerifier (dhash/UIA/DOM diff, anti-loop guard, Esc FailSafe, speculative pre-check).
4. ToolRegistry (21 инструмент, усечение <= 2000 символов, Safety Gate, SoM-клик).
5. Сквозной цикл ReActEngine (Observe -> Think -> Act -> Verify, события UI, эскалация,
   сохранение эпизодов в MemoryManager).
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

from core.agent import (
    MAX_OUTPUT_CHARS,
    AgentStepEvent,
    ReActEngine,
    StateSnapshot,
    StepVerifier,
    ToolRegistry,
    truncate_output,
)
from core.brain.llm_client import LocalLLMClient
from core.browser import BrowserCDPBridge, DOMElement
from core.browser.cdp_bridge import MinimalCDPWebSocket
from core.failsafe import FailSafeMonitor
from core.memory import MemoryManager
from core.routing.intent_router import ExecutionTier, Intent, IntentRouter, RouteResult
from core.vision.monitor_manager import MonitorInfo, MonitorManager
from core.vision.screen_capture import ScreenFrame
from core.vision.som_annotator import SoMAnnotator


class TestExecutionTierRouting:
    """Тесты трёхуровневого роутера IntentRouter (CASUAL_CHAT / FAST_PATH / AUTONOMOUS_AGENT)."""

    @pytest.mark.parametrize(
        "query",
        [
            "как дела",
            "расскажи про квантовые компьютеры",
            "поболтай со мной",
            "что такое нейронная сеть",
            "привет",
            "напиши функцию быстрой сортировки",
            "Какая сегодня погода?",
            "Привет, как дела?",
            "Расскажи шутку про программистов",
            "Что ты умеешь делать?",
            "   ",
        ],
    )
    def test_casual_chat_tier(self, query: str) -> None:
        """Разговорные запросы и вопросы без OS-намерения идут в CASUAL_CHAT."""
        router = IntentRouter()
        res = router.route(query)
        assert res.intent == Intent.AI_COGNITIVE
        assert res.tier == ExecutionTier.CASUAL_CHAT

    @pytest.mark.parametrize(
        ("query", "expected_intent"),
        [
            ("открой блокнот", Intent.OPEN_APP),
            ("сделай громче", Intent.VOLUME_CONTROL),
            ("сверни окно", Intent.WINDOW_CONTROL),
            ("найди в youtube rock", Intent.SEARCH_WEB),
            ("открой https://ya.ru", Intent.OPEN_URL),
            ("запомни, что мой редактор это VS Code", Intent.MEMORY),
            ("в окне Блокнот нажми кнопку Сохранить", Intent.UI_AUTOMATION),
        ],
    )
    def test_fast_path_tier(self, query: str, expected_intent: Intent) -> None:
        """Детерминированные одношаговые команды идут в FAST_PATH."""
        router = IntentRouter()
        res = router.route(query)
        assert res.intent == expected_intent
        assert res.tier == ExecutionTier.FAST_PATH

    @pytest.mark.parametrize(
        "query",
        [
            "открой прикольное видео на ютуб и включи на свой вкус",
            "Открой какое нибудь прикольное видео на ютуб на свой вкус",
            "открой антигравити, создай новый проект, выбери модель и напиши прототип",
            "открой браузер и найди погоду в Москве",
            "запусти терминал, перейди в проект, потом запусти тесты",
            "Зайди в браузер, найди погоду в Москве и скажи сколько градусов",
            "Загугли курс доллара и покажи результат",
            "Переключи окно в браузере и прочитай заголовок",
            "Прочитай заметки и скажи что запланировано",
        ],
    )
    def test_autonomous_agent_tier(self, query: str) -> None:
        """Многошаговые и открытые задачи идут в AUTONOMOUS_AGENT."""
        router = IntentRouter()
        res = router.route(query)
        assert res.intent == Intent.AI_COGNITIVE
        assert res.tier == ExecutionTier.AUTONOMOUS_AGENT

    def test_route_result_default_tier_backward_compatible(self) -> None:
        """RouteResult сохраняет дефолт ExecutionTier.FAST_PATH для старых вызовов."""
        res = RouteResult(intent=Intent.OPEN_APP, query="открой telegram", target="telegram")
        assert res.tier == ExecutionTier.FAST_PATH

    @pytest.mark.parametrize(
        "query",
        [
            "давай ещё раз",
            "так, давай ещё раз",
            "Так, давай ещё раз!",
            "давай ещё раз попробуем",
            "попробуй ещё раз",
            "попробуй снова",
            "ещё раз",
            "повтори",
            "сделай ещё раз",
            "попробуй заново",
        ],
    )
    def test_retry_phrases_route_to_autonomous_agent(self, query: str) -> None:
        """Русские фразы ретрая никогда не попадают в CASUAL_CHAT и помечаются флагом is_retry."""
        router = IntentRouter()
        res = router.route(query)
        assert res.intent == Intent.AI_COGNITIVE
        assert res.tier == ExecutionTier.AUTONOMOUS_AGENT
        assert res.matched_rule == "retry_previous_goal"
        assert res.params.get("is_retry") is True
        assert router.is_retry_query(query) is True


class _FakeCDPWebSocket:
    """Управляемый мок WebSocket-клиента для тестирования BrowserCDPBridge."""

    def __init__(self, dom_items: list[dict[str, Any]] | None = None) -> None:
        self.commands: list[tuple[str, dict[str, Any]]] = []
        self.dom_items = dom_items if dom_items is not None else []
        self.closed = False

    def send_command(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = params or {}
        self.commands.append((method, payload))
        if method == "Runtime.evaluate":
            expr = str(payload.get("expression", ""))
            if "window.__gupkaElements" in expr and "document.querySelectorAll" in expr:
                return {"result": {"value": self.dom_items}}
            if "window.location.href" in expr:
                return {"result": {"value": {"url": "https://www.youtube.com", "title": "YouTube"}}}
            if "getBoundingClientRect" in expr:
                return {"result": {"value": {"x": 320, "y": 180}}}
            return {"result": {"value": True}}
        if method == "Page.navigate":
            return {"frameId": "main"}
        return {}

    def close(self) -> None:
        self.closed = True


class TestBrowserCDPBridge:
    """Тесты моста Chrome/Edge DevTools Protocol (HTTP, RFC6455, DOM)."""

    def test_offline_graceful_fallback(self) -> None:
        """При отсутствии отладочного порта is_available() безопасно возвращает False."""
        session = MagicMock(spec=requests.Session)
        session.get.side_effect = requests.ConnectionError("Connection refused")

        bridge = BrowserCDPBridge(port=9222, session=session)
        assert bridge.is_available() is False
        assert bridge.list_pages() == []
        assert bridge.inspect_dom() == []

    def test_inspect_dom_click_type_and_summary(self) -> None:
        """Проверяет компактный DOM-манифест, клик, ввод текста и навигацию."""
        session = MagicMock(spec=requests.Session)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = [
            {
                "type": "page",
                "url": "https://www.youtube.com",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/1",
            }
        ]
        session.get.return_value = resp

        fake_ws = _FakeCDPWebSocket(
            dom_items=[
                {
                    "index": 0,
                    "tag": "button",
                    "text": "Play",
                    "aria_label": "Play video",
                    "rect": [100, 150, 80, 40],
                    "center_x": 140,
                    "center_y": 170,
                },
                {
                    "index": 1,
                    "tag": "input",
                    "text": "",
                    "element_type": "text",
                    "placeholder": "Поиск",
                    "rect": [200, 40, 300, 36],
                    "center_x": 350,
                    "center_y": 58,
                },
            ]
        )

        bridge = BrowserCDPBridge(session=session, ws_factory=lambda _url, _t: fake_ws)
        assert bridge.is_available() is True

        elements = bridge.inspect_dom(max_elements=80)
        assert len(elements) == 2
        assert isinstance(elements[0], DOMElement)

        manifest = bridge.format_manifest(elements)
        assert '[0]<button aria-label="Play video">Play</button>' in manifest
        assert '[1]<input type="text" placeholder="Поиск" />' in manifest

        assert bridge.click_element(0) is True
        mouse_cmds = [c for c in fake_ws.commands if c[0] == "Input.dispatchMouseEvent"]
        assert len(mouse_cmds) == 3

        assert bridge.type_text(1, "lofi hip hop", press_enter=True) is True
        key_cmds = [c for c in fake_ws.commands if c[0] == "Input.dispatchKeyEvent"]
        assert len(key_cmds) == 2

        assert bridge.navigate("youtube.com") is True
        nav_cmds = [c for c in fake_ws.commands if c[0] == "Page.navigate"]
        assert nav_cmds[0][1]["url"] == "https://youtube.com"

        summary = bridge.get_page_summary()
        assert summary.url == "https://www.youtube.com"
        assert summary.title == "YouTube"
        assert summary.elements_count == 2
        assert summary.state_hash

    def test_minimal_rfc6455_websocket_loopback(self) -> None:
        """Проверяет реальный RFC6455 handshake и обмен JSON-фреймами поверх TCP-сокета."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def _server_worker() -> None:
            conn, _ = srv.accept()
            with conn:
                req = conn.recv(4096).decode("ascii")
                ws_key = ""
                for line in req.splitlines():
                    if line.lower().startswith("sec-websocket-key:"):
                        ws_key = line.split(":", 1)[1].strip()
                accept = base64.b64encode(
                    hashlib.sha1(f"{ws_key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11".encode()).digest()
                ).decode("ascii")
                conn.sendall(
                    (
                        "HTTP/1.1 101 Switching Protocols\r\n"
                        "Upgrade: websocket\r\n"
                        "Connection: Upgrade\r\n"
                        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
                    ).encode("ascii")
                )
                # Читаем маскированный клиентский фрейм
                hdr = conn.recv(2)
                length = hdr[1] & 0x7F
                mask = conn.recv(4)
                masked_body = conn.recv(length)
                plain = bytes(b ^ mask[i % 4] for i, b in enumerate(masked_body))
                req_json = json.loads(plain.decode("utf-8"))

                # Отправляем сначала событие без id, затем ответ с нужным id
                for msg in (
                    {"method": "Page.loadEventFired"},
                    {"id": req_json["id"], "result": {"ok": True}},
                ):
                    raw = json.dumps(msg).encode("utf-8")
                    frame = bytes([0x81, len(raw)]) + raw
                    conn.sendall(frame)

        t = threading.Thread(target=_server_worker, daemon=True)
        t.start()
        try:
            ws = MinimalCDPWebSocket(f"ws://127.0.0.1:{port}/devtools/page/1", timeout=2.0)
            res = ws.send_command("Runtime.evaluate", {"expression": "1+1"})
            assert res == {"ok": True}
            ws.close()
        finally:
            t.join(timeout=2.0)
            srv.close()


class TestStepVerifierAndToolRegistry:
    """Тесты StepVerifier (anti-loop, Esc, speculative) и ToolRegistry."""

    def test_step_verifier_dhash_change_and_anti_loop(self) -> None:
        """Детектирует изменение dhash и зацикливание после 2 повторных no-op шагов."""
        fs = FailSafeMonitor()
        verifier = StepVerifier(failsafe_monitor=fs, dhash_threshold=2, max_repeated_noops=2)

        s1 = StateSnapshot(dhash="0000000000000000", dom_signature="aaa")
        s2 = StateSnapshot(dhash="ffffffffffffffff", dom_signature="bbb")
        res_ok = verifier.verify_step("browser_click", {"index": 0}, s1, s2, tool_success=True)
        assert res_ok.state_changed is True
        assert res_ok.loop_detected is False

        # Два повторных no-op клика по одному индексу
        noop1 = verifier.verify_step("browser_click", {"index": 5}, s2, s2, tool_success=False)
        assert noop1.loop_detected is False
        noop2 = verifier.verify_step("browser_click", {"index": 5}, s2, s2, tool_success=False)
        assert noop2.loop_detected is True
        assert noop2.should_escalate is True
        assert noop2.next_engine_hint == "Vision"

    def test_step_verifier_failsafe_esc_abort(self) -> None:
        """Взведённый Esc FailSafe останавливает pre-check, verify_step и check_failsafe."""
        fs = FailSafeMonitor()
        fs.trigger_abort("Escape key pressed")
        verifier = StepVerifier(failsafe_monitor=fs)

        ok_pre, msg = verifier.precheck_speculative_step(
            "open_application", {"app_name": "notepad"}
        )
        assert ok_pre is False
        assert "Esc" in msg

        snap = StateSnapshot(dhash="0000")
        res = verifier.verify_step("open_application", {"app_name": "notepad"}, snap, snap, True)
        assert res.interrupted is True

        with pytest.raises(InterruptedError):
            verifier.check_failsafe()

    def test_speculative_precheck_validates_dom_and_som(self) -> None:
        """precheck_speculative_step отсекает несуществующий DOM-индекс и отсутствующий SoM."""
        bridge = MagicMock(spec=BrowserCDPBridge)
        bridge.is_available.return_value = True
        bridge.last_elements = [DOMElement(index=0, tag="button", text="OK")]

        som = SoMAnnotator()
        verifier = StepVerifier(
            browser_bridge=bridge, som_annotator=som, failsafe_monitor=FailSafeMonitor()
        )

        assert verifier.precheck_speculative_step("browser_click", {"index": 0}) == (True, "ok")
        ok_bad_dom, _ = verifier.precheck_speculative_step("browser_click", {"index": 99})
        assert ok_bad_dom is False

        ok_bad_som, _ = verifier.precheck_speculative_step("click_som", {"mark_id": 1})
        assert ok_bad_som is False

    def test_tool_registry_truncates_output_and_blocks_destructive_commands(self) -> None:
        """ToolRegistry усекает длинный вывод <= 2000 символов и блокирует опасные команды."""
        long_text = "A" * 5000
        assert len(truncate_output(long_text)) <= MAX_OUTPUT_CHARS

        os_ctrl = MagicMock()
        os_ctrl.shell.is_safe_command.return_value = (True, None)
        os_ctrl.shell._normalize_command.return_value = "get-process"
        os_ctrl.run_command.return_value = (0, "START_" + ("X" * 4500) + "_END", "")

        registry = ToolRegistry(
            os_controller=os_ctrl,
            app_finder=MagicMock(),
            failsafe_monitor=FailSafeMonitor(),
        )
        assert len(registry.list_tools()) == 21

        res_long = registry.execute("run_command", {"command": "Get-Process"})
        assert res_long.success is True
        assert len(res_long.output) <= MAX_OUTPUT_CHARS
        assert "START_" in res_long.output
        assert "_END" in res_long.output

        # Проверка Safety Gate для taskkill системного процесса
        os_ctrl.shell._normalize_command.return_value = "taskkill /f /im explorer.exe"
        res_blocked = registry.execute("run_command", {"command": "taskkill /f /im explorer.exe"})
        assert res_blocked.success is False
        assert res_blocked.error == "safety_gate_blocked"

    def test_tool_registry_click_som_uses_global_coords(self) -> None:
        """click_som берёт точные глобальные координаты и monitor_index из SoMAnnotator."""
        som = SoMAnnotator()
        som.build_elements(
            [
                {
                    "name": "Play",
                    "control_type": "ButtonControl",
                    "rect": {"left": -900, "top": 100, "right": -800, "bottom": 140},
                }
            ],
            monitor=MonitorInfo(index=2, left=-1920, top=0, width=1920, height=1080),
        )
        vision = MagicMock()
        vision.click_at.return_value = True

        registry = ToolRegistry(
            os_controller=MagicMock(),
            app_finder=MagicMock(),
            vision_agent=vision,
            som_annotator=som,
            failsafe_monitor=FailSafeMonitor(),
        )
        res = registry.execute("click_som", {"mark_id": 1})
        assert res.success is True
        vision.click_at.assert_called_once_with(
            -850, 120, normalized=False, verify_change=False, monitor_index=2
        )


class TestReActEngineIntegration:
    """Сквозные интеграционные тесты ReActEngine (Observe -> Think -> Act -> Verify)."""

    def test_react_multistep_cdp_and_episode_saving(self, tmp_path: Path) -> None:
        """Многошаговый прогон через Browser CDP + спекулятивный батч + сохранение эпизода."""
        memory = MemoryManager(db_path=tmp_path / "memory.db")
        memory.set_fact("браузер", "Chrome")

        bridge = MagicMock(spec=BrowserCDPBridge)
        bridge.is_available.return_value = True
        bridge.last_elements = [
            DOMElement(index=0, tag="input", placeholder="Введите запрос"),
            DOMElement(index=1, tag="button", text="Найти"),
        ]
        summary_mock = MagicMock()
        summary_mock.url = "https://www.youtube.com"
        summary_mock.title = "YouTube"
        summary_mock.elements_count = 2
        summary_mock.manifest = (
            '[0]<input placeholder="Введите запрос" />\n[1]<button>Найти</button>'
        )
        summary_mock.state_hash = "hash_step_1"
        bridge.get_page_summary.side_effect = [
            summary_mock,
            summary_mock,
            MagicMock(
                url="https://www.youtube.com/watch?v=1",
                title="LoFi Mix",
                elements_count=1,
                manifest="[0]<button>Pause</button>",
                state_hash="hash_step_2",
            ),
            MagicMock(
                url="https://www.youtube.com/watch?v=1",
                title="LoFi Mix",
                elements_count=1,
                manifest="[0]<button>Pause</button>",
                state_hash="hash_step_3",
            ),
            MagicMock(
                url="https://www.youtube.com/watch?v=1",
                title="LoFi Mix",
                elements_count=1,
                manifest="[0]<button>Pause</button>",
                state_hash="hash_step_3",
            ),
        ]
        bridge.type_text.return_value = True
        bridge.click_element.return_value = True

        llm = MagicMock(spec=LocalLLMClient)
        llm.tools = []
        llm.is_healthy.return_value = True
        llm.plan_actions.side_effect = [
            [
                {
                    "tool": "browser_type",
                    "parameters": {"index": 0, "text": "lofi mix", "press_enter": False},
                },
                {"tool": "browser_click", "parameters": {"index": 1}},
            ],
            [
                {
                    "tool": "finish_task",
                    "parameters": {
                        "summary": "<think>готово</think>Включил музыку на YouTube.",
                        "success": True,
                    },
                }
            ],
        ]

        monitor_mgr = MonitorManager(
            monitors=[MonitorInfo(index=1, left=0, top=0, width=1920, height=1080, is_primary=True)]
        )
        fs = FailSafeMonitor()
        registry = ToolRegistry(
            os_controller=MagicMock(),
            app_finder=MagicMock(),
            browser_bridge=bridge,
            memory_manager=memory,
            monitor_manager=monitor_mgr,
            failsafe_monitor=fs,
        )
        engine = ReActEngine(
            llm_client=llm,
            tool_registry=registry,
            memory_manager=memory,
            monitor_manager=monitor_mgr,
            browser_bridge=bridge,
            failsafe_monitor=fs,
            max_steps=8,
        )

        events: list[AgentStepEvent] = []
        summary = engine.run("Включи прикольную музыку на ютуб", on_step_event=events.append)

        assert summary == "Включил музыку на YouTube."
        assert "<think>" not in summary
        assert engine.last_result is not None
        assert engine.last_result.success is True
        assert engine.last_result.steps_executed == 3
        assert any(ev.engine == "CDP" and ev.status == "done" for ev in events)
        assert any(ev.engine == "LLM" for ev in events)

        recalled = memory.recall_episodes("музыку на ютуб", limit=3)
        assert len(recalled) == 1
        assert recalled[0]["success"] is True
        assert len(recalled[0]["steps"]) == 3
        memory.close()

    def test_react_escalates_to_som_vision_on_loop_and_aborts_on_esc(self) -> None:
        """При повторе no-op ReAct эскалирует на SoM Vision, а при Esc прерывается."""
        mon = MonitorInfo(index=1, left=0, top=0, width=100, height=100, is_primary=True)
        monitor_mgr = MonitorManager(monitors=[mon])

        screen_cap = MagicMock()
        frame = ScreenFrame(
            width=100,
            height=100,
            rgb=b"\x00" * (100 * 100 * 3),
            dhash="1111111111111111",
            monitor_index=1,
        )
        screen_cap.capture_frame.return_value = frame

        uia = MagicMock()
        uia.get_active_window_rect.return_value = {"left": 0, "top": 0, "width": 100, "height": 100}
        uia.inspect_window_elements.return_value = [
            {
                "name": "ОК",
                "control_type": "ButtonControl",
                "rect": {"left": 10, "top": 10, "right": 50, "bottom": 30},
            }
        ]
        uia.click_element.return_value = False

        fs = FailSafeMonitor()
        som = SoMAnnotator()
        llm = MagicMock(spec=LocalLLMClient)
        llm.tools = []
        llm.is_healthy.return_value = True

        call_count = 0

        def _plan_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return [{"tool": "click_ui_element", "parameters": {"element_name": "ОК"}}]
            # На 3-м шаге эскалировало в Vision (image_b64 передан) -> имитируем нажатие Esc
            assert kwargs.get("image_b64") is not None
            fs.trigger_abort("Escape key pressed")
            return [{"tool": "click_som", "parameters": {"mark_id": 1}}]

        llm.plan_actions.side_effect = _plan_side_effect

        registry = ToolRegistry(
            os_controller=MagicMock(),
            app_finder=MagicMock(),
            uia_manager=uia,
            vision_agent=MagicMock(),
            som_annotator=som,
            monitor_manager=monitor_mgr,
            failsafe_monitor=fs,
        )
        engine = ReActEngine(
            llm_client=llm,
            tool_registry=registry,
            monitor_manager=monitor_mgr,
            uia_manager=uia,
            screen_capture=screen_cap,
            som_annotator=som,
            failsafe_monitor=fs,
            max_steps=8,
        )

        res = engine.run_task("Нажми ОК в диалоге")
        assert res.aborted is True
        assert res.success is False
        assert "Esc" in res.summary

    def test_react_electron_few_uia_triggers_vision_and_filters_window_chrome(self) -> None:
        """При <= 6 UIA-элементах (Electron) сразу снимается скриншот, а кнопки рамки скрываются."""
        mon = MonitorInfo(index=1, left=0, top=0, width=200, height=200, is_primary=True)
        monitor_mgr = MonitorManager(monitors=[mon])

        screen_cap = MagicMock()
        screen_cap.capture_frame.return_value = ScreenFrame(
            width=200,
            height=200,
            rgb=b"\x10" * (200 * 200 * 3),
            dhash="2222222222222222",
            monitor_index=1,
        )

        uia = MagicMock()
        uia.inspect_window_elements.return_value = [
            {
                "name": label,
                "control_type": "ButtonControl",
                "rect": {"left": 10 + i * 30, "top": 0, "right": 35 + i * 30, "bottom": 25},
            }
            for i, label in enumerate(("Свернуть", "Развернуть", "Восстановить", "Закрыть"))
        ]

        som = SoMAnnotator()
        fs = FailSafeMonitor()
        llm = MagicMock(spec=LocalLLMClient)
        llm.tools = []
        llm.is_healthy.return_value = True

        seen_images: list[str | None] = []
        seen_sys_prompts: list[str] = []

        def _plan_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            seen_images.append(kwargs.get("image_b64"))
            seen_sys_prompts.append(str(kwargs.get("system_prompt", "")))
            if len(seen_images) == 1:
                return [{"tool": "click_som", "parameters": {"mark_id": 1}}]
            return [
                {
                    "tool": "finish_task",
                    "parameters": {
                        "summary": "Проект создан через горячие клавиши.",
                        "success": True,
                    },
                }
            ]

        llm.plan_actions.side_effect = _plan_side_effect

        vision = MagicMock()
        registry = ToolRegistry(
            os_controller=MagicMock(),
            app_finder=MagicMock(),
            uia_manager=uia,
            vision_agent=vision,
            som_annotator=som,
            monitor_manager=monitor_mgr,
            failsafe_monitor=fs,
        )
        engine = ReActEngine(
            llm_client=llm,
            tool_registry=registry,
            monitor_manager=monitor_mgr,
            uia_manager=uia,
            screen_capture=screen_cap,
            som_annotator=som,
            failsafe_monitor=fs,
            max_steps=4,
        )

        res = engine.run_task("Открой Antigravity и создай новый проект")
        assert res.success is True
        assert seen_images[0] is not None
        assert som.last_lookup == {}
        vision.click_at.assert_not_called()
        assert "open_application" in seen_sys_prompts[0]
        assert "click_screen" in seen_sys_prompts[0]
        assert "press_key" in seen_sys_prompts[0]
        assert "Свернуть/Закрыть" in seen_sys_prompts[0]

    def test_react_keeps_window_chrome_when_goal_requests_window_action(self) -> None:
        """Если цель явно просит свернуть или закрыть окно, кнопки рамки остаются в SoM."""
        mon = MonitorInfo(index=1, left=0, top=0, width=200, height=200, is_primary=True)
        monitor_mgr = MonitorManager(monitors=[mon])

        screen_cap = MagicMock()
        screen_cap.capture_frame.return_value = ScreenFrame(
            width=200,
            height=200,
            rgb=b"\x10" * (200 * 200 * 3),
            dhash="3333333333333333",
            monitor_index=1,
        )

        uia = MagicMock()
        uia.inspect_window_elements.return_value = [
            {
                "name": "Свернуть",
                "control_type": "ButtonControl",
                "rect": {"left": 100, "top": 0, "right": 130, "bottom": 25},
            },
            {
                "name": "Закрыть",
                "control_type": "ButtonControl",
                "rect": {"left": 135, "top": 0, "right": 165, "bottom": 25},
            },
        ]

        som = SoMAnnotator()
        fs = FailSafeMonitor()
        llm = MagicMock(spec=LocalLLMClient)
        llm.tools = []
        llm.is_healthy.return_value = True
        llm.plan_actions.return_value = [
            {"tool": "finish_task", "parameters": {"summary": "Окно свернуто.", "success": True}}
        ]

        engine = ReActEngine(
            llm_client=llm,
            tool_registry=ToolRegistry(
                os_controller=MagicMock(),
                app_finder=MagicMock(),
                uia_manager=uia,
                som_annotator=som,
                monitor_manager=monitor_mgr,
                failsafe_monitor=fs,
            ),
            monitor_manager=monitor_mgr,
            uia_manager=uia,
            screen_capture=screen_cap,
            som_annotator=som,
            failsafe_monitor=fs,
            max_steps=2,
        )
        res = engine.run_task("Сверни окно редактора")
        assert res.success is True
        assert len(som.last_lookup) == 2
        assert som.last_lookup[1].name == "Свернуть"
