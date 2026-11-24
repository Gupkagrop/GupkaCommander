"""Верификатор шагов автономного ReAct-агента (StepVerifier).

Отслеживает снимки состояния (dhash экрана, UIA-дерево, Browser CDP DOM) до и после
действия, детектирует зацикливание (повторные no-op вызовы), проверяет FailSafe (Esc)
и валидирует спекулятивные батчи (pre-check перед шагом k > 0 в стиле UFO2).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import time
from typing import Any

from core.browser.cdp_bridge import BrowserCDPBridge
from core.failsafe import FailSafeMonitor, get_failsafe_monitor
from core.uia.uia_controller import UIAutomationManager
from core.vision.monitor_manager import MonitorManager
from core.vision.screen_capture import ScreenCapture, hamming_distance
from core.vision.som_annotator import SoMAnnotator

logger = logging.getLogger(__name__)

_NON_VISUAL_TOOLS: frozenset[str] = frozenset({"store_user_fact", "add_user_note", "finish_task"})


@dataclasses.dataclass(frozen=True)
class StateSnapshot:
    """Снимок состояния среды выполнения на границе шага ReAct."""

    dhash: str = ""
    uia_signature: str = ""
    dom_signature: str = ""
    active_window: str = ""
    monitor_index: int = 1
    timestamp: float = 0.0


@dataclasses.dataclass(frozen=True)
class VerificationResult:
    """Результат постпроверки выполненного шага."""

    state_changed: bool
    loop_detected: bool
    interrupted: bool
    dhash_distance: int = 0
    should_escalate: bool = False
    next_engine_hint: str = ""
    reason: str = ""


class StepVerifier:
    """Контролёр изменений интерфейса, детектор циклов и превентивный pre-checker."""

    def __init__(
        self,
        screen_capture: ScreenCapture | None = None,
        uia_manager: UIAutomationManager | None = None,
        browser_bridge: BrowserCDPBridge | None = None,
        monitor_manager: MonitorManager | None = None,
        som_annotator: SoMAnnotator | None = None,
        failsafe_monitor: FailSafeMonitor | None = None,
        dhash_threshold: int = 2,
        max_repeated_noops: int = 2,
    ) -> None:
        """Инициализация верификатора шагов."""
        self._screen_capture = screen_capture
        self._uia_manager = uia_manager
        self._browser_bridge = browser_bridge
        self._monitor_manager = monitor_manager
        self._som_annotator = som_annotator
        self._failsafe_monitor: FailSafeMonitor = failsafe_monitor or get_failsafe_monitor()
        self._dhash_threshold = max(1, dhash_threshold)
        self._max_repeated_noops = max(2, max_repeated_noops)
        self._history: list[tuple[str, str, bool]] = []

    def reset(self) -> None:
        """Сбрасывает историю шагов перед новым запуском ReAct."""
        self._history.clear()

    def check_failsafe(self) -> None:
        """Проверяет флаг прерывания Esc / Mouse Shake и выбрасывает InterruptedError."""
        if self._failsafe_monitor.is_interrupted():
            self._failsafe_monitor.check_abort()
            raise InterruptedError("FailSafe abort: прерывание работы агента")

    def _resolve_monitor_index(self, monitor_index: int | None) -> int:
        """Определяет индекс целевого монитора."""
        if monitor_index is not None:
            return monitor_index
        if self._monitor_manager is not None:
            try:
                return self._monitor_manager.get_active_monitor().index
            except Exception:
                return 1
        return 1

    def _capture_dhash(self, target_monitor: int) -> str:
        """Безопасно снимает разностный хеш кадра целевого монитора."""
        if self._screen_capture is None:
            return ""
        try:
            frame = self._screen_capture.capture_frame(
                monitor_index=target_monitor,
                include_base64=False,
            )
            return str(getattr(frame, "dhash", "") or "")
        except Exception as exc:
            logger.debug("Не удалось снять dhash монитора %d: %s", target_monitor, exc)
            return ""

    def _capture_uia_signature(self) -> tuple[str, str]:
        """Возвращает (active_window_sig, uia_elements_sig) через UIAutomationManager."""
        if self._uia_manager is None:
            return ("", "")
        win_sig = ""
        elements_sig = ""
        try:
            rect = self._uia_manager.get_active_window_rect()
            if rect:
                win_sig = json.dumps(rect, sort_keys=True)
        except Exception:
            pass
        try:
            items = self._uia_manager.inspect_window_elements(window_title=None, max_depth=4)
            compact = [
                f"{el.get('control_type', '')}:{el.get('name', '')}:{el.get('value', '')}"
                for el in items[:25]
            ]
            if compact:
                elements_sig = hashlib.sha1("|".join(compact).encode("utf-8")).hexdigest()[:16]
        except Exception:
            pass
        return (win_sig, elements_sig)

    def _capture_dom_signature(self) -> str:
        """Возвращает хеш состояния DOM активной вкладки браузера при доступности CDP."""
        if self._browser_bridge is None:
            return ""
        try:
            if not self._browser_bridge.is_available():
                return ""
            summary = self._browser_bridge.get_page_summary(max_elements=40)
            return (
                summary.state_hash
                or hashlib.sha1(f"{summary.url}|{summary.manifest}".encode()).hexdigest()[:16]
            )
        except Exception:
            return ""

    def capture_snapshot(
        self,
        monitor_index: int | None = None,
        include_uia: bool = True,
        include_dom: bool = True,
    ) -> StateSnapshot:
        """Снимает срез состояния экрана, UIA и DOM для сравнения до/после шага."""
        target_mon = self._resolve_monitor_index(monitor_index)
        dhash = self._capture_dhash(target_mon)
        win_sig, uia_sig = self._capture_uia_signature() if include_uia else ("", "")
        dom_sig = self._capture_dom_signature() if include_dom else ""
        return StateSnapshot(
            dhash=dhash,
            uia_signature=uia_sig,
            dom_signature=dom_sig,
            active_window=win_sig,
            monitor_index=target_mon,
            timestamp=time.monotonic(),
        )

    @staticmethod
    def _canonical_target_key(params: dict[str, Any]) -> str:
        """Строит канонический ключ параметров шага для детектора повторов."""
        if not params:
            return ""
        try:
            return json.dumps(params, sort_keys=True, ensure_ascii=False)
        except TypeError:
            return str(params)

    @staticmethod
    def _suggest_next_engine(tool_name: str) -> str:
        """Подсказывает следующий уровень лестницы эскалации (UFO2)."""
        if tool_name.startswith("browser_"):
            return "Vision"
        if tool_name in ("click_ui_element", "type_into_ui_element", "inspect_ui_elements"):
            return "Vision"
        if tool_name in ("open_application", "open_url", "search_web", "window_control"):
            return "UIA"
        return "Vision"

    def _has_repeated_noop(self, tool_name: str, target_key: str) -> bool:
        """Проверяет серию однотипных шагов без прогресса."""
        if len(self._history) < self._max_repeated_noops:
            return False
        tail = self._history[-self._max_repeated_noops :]
        return all(t == tool_name and k == target_key and not changed for t, k, changed in tail)

    def verify_step(
        self,
        tool_name: str,
        params: dict[str, Any],
        before: StateSnapshot,
        after: StateSnapshot,
        tool_success: bool,
    ) -> VerificationResult:
        """Сравнивает снимки состояния до и после шага, детектирует зацикливание и Esc."""
        if self._failsafe_monitor.is_interrupted():
            return VerificationResult(
                state_changed=False,
                loop_detected=False,
                interrupted=True,
                reason="Прервано пользователем (FailSafe Esc)",
            )

        distance = (
            hamming_distance(before.dhash, after.dhash) if (before.dhash and after.dhash) else 0
        )
        visual_or_tree_changed = (
            distance >= self._dhash_threshold
            or before.uia_signature != after.uia_signature
            or before.dom_signature != after.dom_signature
            or before.active_window != after.active_window
        )
        effective_progress = visual_or_tree_changed or (
            tool_success and tool_name in _NON_VISUAL_TOOLS
        )

        target_key = self._canonical_target_key(params)
        self._history.append((tool_name, target_key, effective_progress))

        loop_detected = self._has_repeated_noop(tool_name, target_key)
        should_escalate = loop_detected or (not tool_success and not visual_or_tree_changed)
        next_hint = self._suggest_next_engine(tool_name) if should_escalate else ""

        if loop_detected:
            reason = (
                f"Обнаружено зацикливание на '{tool_name}' ({self._max_repeated_noops} "
                f"повторов без изменения состояния)."
            )
        elif effective_progress:
            reason = "Состояние интерфейса изменилось."
        else:
            reason = "Изменений состояния не зафиксировано."

        return VerificationResult(
            state_changed=effective_progress,
            loop_detected=loop_detected,
            interrupted=False,
            dhash_distance=distance,
            should_escalate=should_escalate,
            next_engine_hint=next_hint,
            reason=reason,
        )

    def precheck_speculative_step(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> tuple[bool, str]:
        """Локальный pre-check перед выполнением шага k > 0 в спекулятивном батче (UFO2)."""
        if self._failsafe_monitor.is_interrupted():
            return False, "Прервано пользователем (FailSafe Esc)"

        if tool_name in ("browser_click", "browser_type"):
            if self._browser_bridge is None or not self._browser_bridge.is_available():
                return False, "Browser CDP недоступен для спекулятивного шага"
            idx = int(params.get("index", -1))
            if idx < 0:
                return False, f"Некорректный индекс DOM: {idx}"
            cached = self._browser_bridge.last_elements
            if cached and not any(el.index == idx for el in cached):
                return False, f"Элемент DOM [{idx}] отсутствует в актуальном снимке"
            return True, "ok"

        if tool_name in ("click_ui_element", "type_into_ui_element"):
            if self._uia_manager is None:
                return False, "UIA менеджер недоступен"
            win_title = str(params.get("window_title", "")).strip()
            if win_title and self._uia_manager.find_window(win_title) is None:
                return False, f"Окно '{win_title}' не найдено перед спекулятивным шагом"
            return True, "ok"

        if tool_name == "click_som":
            if self._som_annotator is None:
                return False, "SoMAnnotator не инициализирован"
            mark_id = int(params.get("mark_id", 0))
            if self._som_annotator.get_element(mark_id) is None:
                return False, f"Метка SoM [{mark_id}] не найдена"
            return True, "ok"

        return True, "ok"
