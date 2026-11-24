"""Автономный цикл рассуждения и действий ReActEngine для GupkaCommander 2.0.

Реализует цикл Observe -> Think -> Act -> Verify:
- Observe: активный MonitorInfo, Browser CDP DOM или Microsoft UIA, эскалация на SoM Vision.
- Think: LocalLLMClient с контекстом MemoryManager (факты, заметки, прошлые эпизоды).
- Act: ToolRegistry с эскалацией Fast-Path -> UIA/CDP -> SoM Vision и батчами UFO2.
- Verify: StepVerifier (dhash/UIA/DOM diff, anti-loop guard, Esc FailSafe).
"""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Callable
from typing import Any, Literal

from core.agent.step_verifier import StateSnapshot, StepVerifier
from core.agent.tool_registry import EngineType, ToolExecutionResult, ToolRegistry
from core.brain.llm_client import LocalLLMClient, strip_thinking_tags
from core.browser.cdp_bridge import BrowserCDPBridge
from core.failsafe import FailSafeMonitor, get_failsafe_monitor
from core.memory.memory_manager import MemoryManager
from core.uia.uia_controller import UIAutomationManager
from core.vision.monitor_manager import MonitorInfo, MonitorManager
from core.vision.screen_capture import ScreenCapture
from core.vision.som_annotator import SoMAnnotator

logger = logging.getLogger(__name__)

StepStatus = Literal["running", "done", "error"]

_DEFAULT_MAX_STEPS = 8
_DEFAULT_MAX_BATCH = 3

_BASE_REACT_SYSTEM_PROMPT = (
    "Ты автономный оператор Windows 11 GupkaCommander (режим ReAct). "
    "На каждом шаге выбирай 1–3 последовательных инструмента через function calling. "
    "Приоритет каналов: 1) Fast-Path (open_application, open_url, search_web, run_command), "
    "2) Browser CDP (browser_inspect, browser_click, browser_type, browser_navigate), "
    "3) Windows UIA (inspect_ui_elements, click_ui_element, type_into_ui_element), "
    "4) SoM Vision (click_som, click_screen, type_text, press_key). "
    "Когда цель достигнута, обязательно вызови finish_task с кратким итогом на русском языке."
)


@dataclasses.dataclass(frozen=True)
class AgentStepEvent:
    """Событие прогресса выполнения шага для карточек StepCard / AgentMiniBar."""

    step_index: int
    max_steps: int
    engine: EngineType
    title: str
    status: StepStatus
    duration_ms: float = 0.0
    detail: str = ""


@dataclasses.dataclass(frozen=True)
class ReActRunResult:
    """Подробный итог автономного прогона ReActEngine."""

    summary: str
    success: bool
    steps: list[dict[str, Any]]
    steps_executed: int
    aborted: bool = False
    duration_ms: float = 0.0


@dataclasses.dataclass
class _Observation:
    """Внутреннее представление текущего наблюдения среды."""

    monitor: MonitorInfo
    summary_text: str
    app_context: str
    image_b64: str | None = None
    uia_elements: list[dict[str, Any]] = dataclasses.field(default_factory=list)


class ReActEngine:
    """Автономный ReAct-движок компьютерного управления с эскалацией и эпизодической памятью."""

    def __init__(
        self,
        llm_client: LocalLLMClient,
        tool_registry: ToolRegistry,
        step_verifier: StepVerifier | None = None,
        memory_manager: MemoryManager | None = None,
        monitor_manager: MonitorManager | None = None,
        browser_bridge: BrowserCDPBridge | None = None,
        uia_manager: UIAutomationManager | None = None,
        screen_capture: ScreenCapture | None = None,
        som_annotator: SoMAnnotator | None = None,
        failsafe_monitor: FailSafeMonitor | None = None,
        max_steps: int = _DEFAULT_MAX_STEPS,
        max_speculative_batch: int = _DEFAULT_MAX_BATCH,
        always_capture_vision: bool = False,
        on_step_event: Callable[[AgentStepEvent], None] | None = None,
    ) -> None:
        """Инициализация ReAct-движка."""
        self.llm_client = llm_client
        self.tool_registry = tool_registry
        self.memory_manager = memory_manager or tool_registry.memory_manager
        self.monitor_manager = monitor_manager or tool_registry.monitor_manager
        self.browser_bridge = browser_bridge or tool_registry.browser_bridge
        self.uia_manager = uia_manager or tool_registry.uia_manager
        self.screen_capture = screen_capture
        self.som_annotator = som_annotator or tool_registry.som_annotator
        self.failsafe_monitor: FailSafeMonitor = (
            failsafe_monitor or tool_registry.failsafe_monitor or get_failsafe_monitor()
        )
        self.step_verifier = step_verifier or StepVerifier(
            screen_capture=self.screen_capture,
            uia_manager=self.uia_manager,
            browser_bridge=self.browser_bridge,
            monitor_manager=self.monitor_manager,
            som_annotator=self.som_annotator,
            failsafe_monitor=self.failsafe_monitor,
        )
        self.max_steps = max(1, max_steps)
        self.max_speculative_batch = max(1, min(4, max_speculative_batch))
        self.always_capture_vision = always_capture_vision
        self.on_step_event = on_step_event
        self.last_result: ReActRunResult | None = None

        self._sync_llm_tools()

    def _sync_llm_tools(self) -> None:
        """Регистрирует расширенные инструменты ToolRegistry в клиенте LLM."""
        if hasattr(self.llm_client, "tools") and isinstance(
            getattr(self.llm_client, "tools", None), list
        ):
            self.llm_client.tools = self.tool_registry.get_openai_tools()

    def _emit_event(
        self,
        callback: Callable[[AgentStepEvent], None] | None,
        event: AgentStepEvent,
    ) -> None:
        """Безопасно отправляет событие прогресса шага в UI/подписчика."""
        target_cb = callback or self.on_step_event
        if target_cb is None:
            return
        try:
            target_cb(event)
        except Exception as exc:
            logger.debug("Ошибка в callback on_step_event: %s", exc)

    def _get_active_monitor(self) -> MonitorInfo:
        """Возвращает активный MonitorInfo с безопасным дефолтом."""
        try:
            return self.monitor_manager.get_active_monitor()
        except Exception:
            return MonitorInfo(index=1, left=0, top=0, width=1920, height=1080, is_primary=True)

    def _observe_browser(self) -> tuple[str, str]:
        """Снимает компактный DOM-манифест из BrowserCDPBridge, если он доступен."""
        if self.browser_bridge is None:
            return ("", "")
        try:
            if not self.browser_bridge.is_available():
                return ("", "")
            summary = self.browser_bridge.get_page_summary(max_elements=80)
            block = (
                f"Browser CDP: {summary.title} ({summary.url})\n"
                f"DOM элементы ({summary.elements_count}):\n{summary.manifest[:1400]}"
            )
            return (block, summary.title or summary.url)
        except Exception as exc:
            logger.debug("Ошибка наблюдения Browser CDP: %s", exc)
            return ("", "")

    def _observe_uia(self, monitor: MonitorInfo) -> tuple[str, list[dict[str, Any]]]:
        """Снимает компактный список интерактивных элементов активного окна через UIA."""
        if self.uia_manager is None:
            return ("", [])
        try:
            items = self.uia_manager.inspect_window_elements(
                window_title=None,
                max_depth=6,
                monitor=monitor,
            )
            if not items:
                return ("", [])
            lines = [
                f"[{i}] {el.get('control_type', 'Element')} "
                f'"{el.get("name") or el.get("automation_id")}" '
                f"@ {el.get('norm_center', el.get('global_center', (0, 0)))}"
                for i, el in enumerate(items[:40], start=1)
            ]
            return ("UIA элементы:\n" + "\n".join(lines), items)
        except Exception as exc:
            logger.debug("Ошибка наблюдения UIA: %s", exc)
            return ("", [])

    def _observe_vision_som(
        self,
        monitor: MonitorInfo,
        uia_elements: list[dict[str, Any]],
    ) -> tuple[str | None, str]:
        """Захватывает активный монитор и накладывает SoM-метки [1..N] без мутации оригинала."""
        if self.screen_capture is None:
            return (None, "")
        try:
            frame = self.screen_capture.capture_frame(
                monitor_index=monitor.index,
                include_base64=True,
            )
            if uia_elements:
                annotated = self.som_annotator.annotate_frame(
                    frame,
                    uia_elements,
                    monitor=monitor,
                )
                return (annotated.frame.b64, f"SoM элементы:\n{annotated.manifest[:1200]}")
            return (frame.b64, "")
        except Exception as exc:
            logger.debug("Ошибка захвата SoM Vision: %s", exc)
            return (None, "")

    def _observe(self, escalate_to_vision: bool) -> _Observation:
        """Выполняет фазу Observe с эскалацией каналов (CDP/UIA -> SoM Vision)."""
        monitor = self._get_active_monitor()
        parts: list[str] = [
            f"Активный монитор #{monitor.index} ({monitor.width}x{monitor.height}, "
            f"offset={monitor.left},{monitor.top})"
        ]

        cdp_block, cdp_ctx = self._observe_browser()
        if cdp_block:
            parts.append(cdp_block)

        uia_block, uia_elements = self._observe_uia(monitor)
        if uia_block:
            parts.append(uia_block)

        need_vision = (
            escalate_to_vision or self.always_capture_vision or (not cdp_block and not uia_block)
        )
        image_b64: str | None = None
        if need_vision:
            image_b64, som_block = self._observe_vision_som(monitor, uia_elements)
            if som_block:
                parts.append(som_block)

        app_ctx = cdp_ctx or f"Monitor #{monitor.index}"
        return _Observation(
            monitor=monitor,
            summary_text="\n\n".join(parts),
            app_context=app_ctx,
            image_b64=image_b64,
            uia_elements=uia_elements,
        )

    def _build_system_prompt(self, goal: str) -> str:
        """Формирует системный промпт планировщика с фактами, заметками и прошлым опытом."""
        if self.memory_manager is None:
            return _BASE_REACT_SYSTEM_PROMPT
        try:
            planner_ctx = self.memory_manager.build_planner_context(goal, episode_limit=3)
        except Exception as exc:
            logger.debug("Ошибка чтения контекста памяти: %s", exc)
            planner_ctx = ""
        if not planner_ctx:
            return _BASE_REACT_SYSTEM_PROMPT
        return f"{_BASE_REACT_SYSTEM_PROMPT}\n\n{planner_ctx}"

    @staticmethod
    def _format_step_history(executed_steps: list[dict[str, Any]]) -> str:
        """Сжимает прошлые шаги в компактный текстовый лог для экономии 8K контекста."""
        if not executed_steps:
            return "Предыдущих шагов ещё нет."
        lines: list[str] = []
        for item in executed_steps[-6:]:
            status = "OK" if item.get("success") else "ERR"
            lines.append(
                f"Шаг {item.get('step')}: [{item.get('engine')}] {item.get('tool')} "
                f"-> {status}: {str(item.get('output', ''))[:160]}"
            )
        return "\n".join(lines)

    @staticmethod
    def _build_step_prompt(
        goal: str,
        step_index: int,
        max_steps: int,
        obs: _Observation,
        executed_steps: list[dict[str, Any]],
        escalate_hint: str,
    ) -> str:
        """Собирает пользовательский промпт текущего шага ReAct."""
        sections = [
            f"Цель пользователя: {goal}",
            f"Текущий шаг: {step_index}/{max_steps}",
            f"Наблюдение среды:\n{obs.summary_text}",
            f"История шагов:\n{ReActEngine._format_step_history(executed_steps)}",
        ]
        if escalate_hint:
            sections.append(f"Подсказка эскалации: {escalate_hint}")
        return "\n\n".join(sections)

    @staticmethod
    def _format_action_title(tool_name: str, params: dict[str, Any]) -> str:
        """Формирует короткий человекочитаемый заголовок шага для UI-карточки."""
        if tool_name == "open_application":
            return f"Запуск {params.get('app_name', '')}".strip()
        if tool_name == "search_web":
            return f"Поиск: {params.get('query', '')}".strip()
        if tool_name in ("browser_click", "browser_type", "browser_navigate"):
            return f"Браузер: {tool_name}"
        if tool_name in ("click_ui_element", "type_into_ui_element", "inspect_ui_elements"):
            return f"UIA: {params.get('element_name', tool_name)}"
        if tool_name == "click_som":
            return f"SoM клик [{params.get('mark_id', '')}]"
        if tool_name == "finish_task":
            return "Завершение задачи"
        return tool_name

    def _is_llm_healthy(self) -> bool:
        """Безопасная проверка доступности LLM-сервера."""
        try:
            return bool(self.llm_client.is_healthy())
        except Exception:
            return False

    def _fallback_summary(self, goal: str, executed_steps: list[dict[str, Any]]) -> str:
        """Строит чистый русский итог при отсутствии явного finish_task."""
        if executed_steps:
            last_out = str(executed_steps[-1].get("output", "")).strip()
            if last_out:
                return strip_thinking_tags(last_out)
        if self._is_llm_healthy():
            try:
                reply = self.llm_client.generate_response(goal)
                cleaned = strip_thinking_tags(reply)
                if cleaned:
                    return cleaned
            except Exception as exc:
                logger.debug("Ошибка fallback generate_response: %s", exc)
        return strip_thinking_tags(self.llm_client.fallback_engine.respond(goal))

    def _save_episode_safe(
        self,
        goal: str,
        steps: list[dict[str, Any]],
        outcome: str,
        success: bool,
        app_context: str,
    ) -> None:
        """Сохраняет завершённую траекторию в эпизодическую память SQLite."""
        if self.memory_manager is None or not goal.strip():
            return
        try:
            self.memory_manager.save_episode(
                goal=goal,
                steps=steps,
                outcome=outcome,
                success=success,
                app_context=app_context,
            )
        except Exception as exc:
            logger.warning("Не удалось сохранить эпизод в память: %s", exc)

    def _execute_action_item(
        self,
        step_index: int,
        action: dict[str, Any],
        monitor: MonitorInfo,
        callback: Callable[[AgentStepEvent], None] | None,
    ) -> tuple[ToolExecutionResult, StateSnapshot, StateSnapshot, float]:
        """Исполняет одно действие со снимками до/после и отправкой событий UI."""
        tool_name = str(action.get("tool", "")).strip()
        raw_params = action.get("parameters", {})
        params = dict(raw_params) if isinstance(raw_params, dict) else {}
        engine = self.tool_registry.infer_engine(tool_name)
        title = self._format_action_title(tool_name, params)

        self._emit_event(
            callback,
            AgentStepEvent(
                step_index=step_index,
                max_steps=self.max_steps,
                engine=engine,
                title=title,
                status="running",
            ),
        )
        t0 = time.perf_counter()
        before = self.step_verifier.capture_snapshot(monitor_index=monitor.index)
        result = self.tool_registry.execute(tool_name, params)
        after = self.step_verifier.capture_snapshot(monitor_index=monitor.index)
        duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        self._emit_event(
            callback,
            AgentStepEvent(
                step_index=step_index,
                max_steps=self.max_steps,
                engine=engine,
                title=title,
                status="done" if result.success else "error",
                duration_ms=duration_ms,
                detail=result.output,
            ),
        )
        return result, before, after, duration_ms

    def _plan_step_actions(
        self,
        goal: str,
        step_idx: int,
        obs: _Observation,
        executed_steps: list[dict[str, Any]],
        escalate_hint: str,
        system_prompt: str,
        callback: Callable[[AgentStepEvent], None] | None,
    ) -> list[dict[str, Any]]:
        """Фаза Think: запрос действий у LocalLLMClient с отправкой событий в UI."""
        step_prompt = self._build_step_prompt(
            goal, step_idx, self.max_steps, obs, executed_steps, escalate_hint
        )
        self._emit_event(
            callback,
            AgentStepEvent(
                step_index=step_idx,
                max_steps=self.max_steps,
                engine="LLM",
                title=f"Планирование шага {step_idx}",
                status="running",
            ),
        )
        t0 = time.perf_counter()
        actions = self.llm_client.plan_actions(
            prompt=step_prompt,
            image_b64=obs.image_b64,
            system_prompt=system_prompt,
        )
        think_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        self._emit_event(
            callback,
            AgentStepEvent(
                step_index=step_idx,
                max_steps=self.max_steps,
                engine="LLM",
                title=f"План шага {step_idx} готов ({len(actions)})",
                status="done" if actions else "error",
                duration_ms=think_ms,
            ),
        )
        return actions

    def _execute_speculative_batch(
        self,
        step_idx: int,
        actions: list[dict[str, Any]],
        monitor: MonitorInfo,
        executed_steps: list[dict[str, Any]],
        callback: Callable[[AgentStepEvent], None] | None,
    ) -> tuple[bool, bool, str, bool, bool, str]:
        """Исполняет 1..3 спекулятивных действия с pre-check и верификацией каждого шага.

        Returns:
            (finished, task_success, final_summary, escalate_to_vision, loop_hit, escalate_hint)
        """
        for batch_pos, action in enumerate(actions[: self.max_speculative_batch]):
            self.step_verifier.check_failsafe()
            tool_name = str(action.get("tool", "")).strip()
            raw_params = action.get("parameters", {})
            params = dict(raw_params) if isinstance(raw_params, dict) else {}

            if batch_pos > 0:
                ok_pre, pre_reason = self.step_verifier.precheck_speculative_step(tool_name, params)
                if not ok_pre:
                    return (
                        False,
                        False,
                        "",
                        False,
                        False,
                        f"Спекулятивный шаг '{tool_name}' отменён: {pre_reason}",
                    )

            result, before, after, dur_ms = self._execute_action_item(
                step_idx, action, monitor, callback
            )
            verify = self.step_verifier.verify_step(
                tool_name, params, before, after, result.success
            )
            executed_steps.append(
                {
                    "step": step_idx,
                    "tool": tool_name,
                    "params": params,
                    "engine": result.engine,
                    "success": result.success,
                    "output": result.output,
                    "duration_ms": dur_ms,
                    "state_changed": verify.state_changed,
                }
            )
            if verify.interrupted:
                raise InterruptedError(verify.reason or "Прервано FailSafe")
            if result.finished:
                return True, result.success, strip_thinking_tags(result.output), False, False, ""
            if verify.loop_detected:
                hint = f"{verify.reason} Переключись на {verify.next_engine_hint or 'Vision'}."
                return False, False, "", True, True, hint
            if verify.should_escalate:
                return False, False, "", verify.next_engine_hint == "Vision", False, verify.reason

        return False, False, "", False, False, ""

    def run(
        self,
        goal: str,
        on_step_event: Callable[[AgentStepEvent], None] | None = None,
    ) -> str:
        """Запускает цикл ReAct и возвращает чистый русский итог для TTS/Chat."""
        return self.run_task(goal, on_step_event=on_step_event).summary

    def run_task(
        self,
        goal: str,
        on_step_event: Callable[[AgentStepEvent], None] | None = None,
    ) -> ReActRunResult:
        """Полный прогон цикла Observe -> Think -> Act -> Verify со структурированным итогом."""
        clean_goal = goal.strip()
        run_start = time.perf_counter()
        self.step_verifier.reset()

        if not clean_goal:
            empty = ReActRunResult(
                summary="Пустой запрос.", success=False, steps=[], steps_executed=0
            )
            self.last_result = empty
            return empty

        system_prompt = self._build_system_prompt(clean_goal)
        executed_steps: list[dict[str, Any]] = []
        escalate_to_vision, escalate_hint, consecutive_loops = False, "", 0
        final_summary, task_success, aborted, last_app_ctx = "", False, False, ""

        try:
            for step_idx in range(1, self.max_steps + 1):
                self.step_verifier.check_failsafe()
                obs = self._observe(escalate_to_vision)
                last_app_ctx = obs.app_context

                actions = self._plan_step_actions(
                    clean_goal,
                    step_idx,
                    obs,
                    executed_steps,
                    escalate_hint,
                    system_prompt,
                    on_step_event,
                )
                if not actions:
                    final_summary = self._fallback_summary(clean_goal, executed_steps)
                    task_success = bool(executed_steps and executed_steps[-1].get("success"))
                    break

                done, ok, summary, esc_vis, loop_hit, esc_hint = self._execute_speculative_batch(
                    step_idx, actions, obs.monitor, executed_steps, on_step_event
                )
                escalate_to_vision, escalate_hint = esc_vis, esc_hint
                consecutive_loops = (consecutive_loops + 1) if loop_hit else 0
                if done:
                    final_summary, task_success = summary, ok
                    break
                if consecutive_loops >= 2:
                    final_summary = (
                        "Задача остановлена: интерфейс перестал реагировать на повторные действия."
                    )
                    task_success = False
                    break
                if not self._is_llm_healthy() and executed_steps:
                    final_summary = self._fallback_summary(clean_goal, executed_steps)
                    task_success = bool(executed_steps[-1].get("success"))
                    break
        except InterruptedError:
            aborted, task_success = True, False
            final_summary = "Выполнение остановлено по сигналу безопасности (Esc)."

        if not final_summary:
            final_summary = self._fallback_summary(clean_goal, executed_steps)
            task_success = (
                bool(executed_steps and executed_steps[-1].get("success")) and not aborted
            )

        final_summary = strip_thinking_tags(final_summary)
        self._save_episode_safe(
            clean_goal, executed_steps, final_summary, task_success, last_app_ctx
        )
        run_res = ReActRunResult(
            summary=final_summary,
            success=task_success,
            steps=executed_steps,
            steps_executed=len(executed_steps),
            aborted=aborted,
            duration_ms=round((time.perf_counter() - run_start) * 1000.0, 2),
        )
        self.last_result = run_res
        return run_res
