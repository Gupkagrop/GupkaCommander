"""Модуль управления элементами интерфейса Windows через Microsoft UI Automation.

Предоставляет UIAutomationManager для детерминированного поиска, кликов, ввода текста
и инспекции дерева элементов в окнах Win32, WPF, WinUI и Electron.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import uiautomation as auto
import win32gui  # type: ignore[import-untyped]
from rapidfuzz import fuzz

from core.failsafe import FailSafeMonitor, get_failsafe_monitor, safe_execution

logger = logging.getLogger(__name__)

# Сопоставление текстовых названий типов контролов с числовыми константами UIA ControlType
CONTROL_TYPE_MAP: dict[str, int] = {
    "appbar": 50040,
    "button": 50000,
    "calendar": 50001,
    "checkbox": 50002,
    "combobox": 50003,
    "custom": 50025,
    "datagrid": 50028,
    "dataitem": 50029,
    "document": 50030,
    "edit": 50004,
    "group": 50026,
    "header": 50034,
    "headeritem": 50035,
    "hyperlink": 50005,
    "image": 50006,
    "list": 50008,
    "listitem": 50007,
    "menu": 50009,
    "menubar": 50010,
    "menuitem": 50011,
    "pane": 50033,
    "progressbar": 50012,
    "radiobutton": 50013,
    "scrollbar": 50014,
    "semanticzoom": 50039,
    "separator": 50038,
    "slider": 50015,
    "spinner": 50016,
    "splitbutton": 50031,
    "statusbar": 50017,
    "tab": 50018,
    "tabitem": 50019,
    "table": 50036,
    "text": 50020,
    "thumb": 50027,
    "titlebar": 50037,
    "toolbar": 50021,
    "tooltip": 50022,
    "tree": 50023,
    "treeitem": 50024,
    "window": 50032,
}


class UIAutomationManager:
    """Менеджер взаимодействия с пользовательским интерфейсом через Microsoft UI Automation."""

    def __init__(
        self,
        timeout: float = 2.0,
        monitor: FailSafeMonitor | None = None,
        auto_module: Any | None = None,
    ) -> None:
        """Инициализация менеджера UI Automation.

        Args:
            timeout: Максимальное время поиска элементов в секундах.
            monitor: Монитор безопасности FailSafe (Esc / потряхивание мыши).
            auto_module: Модуль uiautomation (для внедрения мок-объектов в тестах).
        """
        self.timeout: float = max(0.1, timeout)
        self.monitor: FailSafeMonitor = monitor or get_failsafe_monitor()
        self._auto: Any = auto_module or auto

        if hasattr(self._auto, "Logger") and hasattr(self._auto.Logger, "SetLogFile"):
            try:
                self._auto.Logger.SetLogFile("")
            except Exception:
                pass

        if hasattr(self._auto, "SetGlobalSearchTimeout"):
            try:
                self._auto.SetGlobalSearchTimeout(self.timeout)
            except Exception as exc:
                logger.debug("Не удалось установить глобальный таймаут uiautomation: %s", exc)

    def _resolve_control_type(self, control_type: str | int | None) -> int | None:
        """Разрешает текстовое или числовое имя типа контрола в UIA ControlType ID."""
        if control_type is None:
            return None
        if isinstance(control_type, int):
            return control_type
        normalized = control_type.strip().lower().replace("_", "")
        if normalized.endswith("control"):
            normalized = normalized[:-7]
        return CONTROL_TYPE_MAP.get(normalized)

    def _get_pattern(self, ctrl: Any, pattern_id: int) -> Any | None:
        """Извлекает UIA паттерн элемента по его числовому идентификатору."""
        if not hasattr(ctrl, "GetPattern"):
            return None
        try:
            return ctrl.GetPattern(pattern_id)
        except Exception as exc:
            logger.debug("Ошибка получения паттерна %d: %s", pattern_id, exc)
            return None

    def _walk_control_safe(self, parent: Any, max_depth: int) -> Iterator[tuple[Any, int]]:
        """Безопасный обход дерева элементов с ограничением глубины и перехватом ошибок."""
        if hasattr(self._auto, "WalkControl"):
            try:
                for child, depth in self._auto.WalkControl(
                    parent, includeTop=False, maxDepth=max_depth
                ):
                    yield child, depth
                return
            except Exception as exc:
                logger.debug("WalkControl вызвал ошибку, fallback на GetChildren: %s", exc)

        queue: list[tuple[Any, int]] = [(parent, 0)]
        while queue:
            curr, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            try:
                children = curr.GetChildren() if hasattr(curr, "GetChildren") else []
            except Exception:
                children = []
            for child in children:
                yield child, depth + 1
                queue.append((child, depth + 1))

    def _get_windows_via_win32(self) -> list[Any]:
        """Вспомогательный метод перечисления верхнеуровневых окон через Win32 API."""
        windows: list[Any] = []
        try:

            def enum_callback(hwnd: int, extra: list[Any]) -> bool:
                if win32gui.IsWindowVisible(hwnd):
                    text = win32gui.GetWindowText(hwnd)
                    if text.strip() and hasattr(self._auto, "ControlFromHandle"):
                        try:
                            ctrl = self._auto.ControlFromHandle(hwnd)
                            if ctrl is not None:
                                extra.append(ctrl)
                        except Exception:
                            pass
                return True

            win32gui.EnumWindows(enum_callback, windows)
        except Exception as exc:
            logger.debug("Не удалось перечислить окна через win32gui: %s", exc)
        return windows

    def _get_top_windows(self) -> list[Any]:
        """Возвращает список всех доступных верхнеуровневых окон."""
        windows: list[Any] = []
        try:
            root = self._auto.GetRootControl()
            if root is not None and hasattr(root, "GetChildren"):
                windows = list(root.GetChildren())
        except Exception as exc:
            logger.debug("Не удалось получить окна из GetRootControl: %s", exc)

        if not windows:
            windows = self._get_windows_via_win32()
        return windows

    def _get_active_or_root_window(self) -> Any:
        """Возвращает активное foreground-окно или корневой узел десктопа."""
        try:
            active = self._auto.GetForegroundControl()
            if active is not None:
                return active
        except Exception:
            pass

        try:
            hwnd = win32gui.GetForegroundWindow()
            if hwnd and hasattr(self._auto, "ControlFromHandle"):
                ctrl = self._auto.ControlFromHandle(hwnd)
                if ctrl is not None:
                    return ctrl
        except Exception:
            pass

        try:
            return self._auto.GetRootControl()
        except Exception:
            return None

    def _search_window_control_fallback(self, pattern: str) -> Any | None:
        """Запасной поиск окна через стандартный WindowControl uiautomation."""
        if not hasattr(self._auto, "WindowControl"):
            return None
        try:
            win_ctrl = self._auto.WindowControl(
                SubName=pattern, searchDepth=1, searchTime=min(self.timeout, 0.5)
            )
            if hasattr(win_ctrl, "Exists") and win_ctrl.Exists(
                maxSearchSeconds=min(self.timeout, 0.5), searchIntervalSeconds=0.1
            ):
                return win_ctrl
        except Exception as exc:
            logger.debug("Ошибка в WindowControl fallback: %s", exc)
        return None

    def find_window(self, title_pattern: str, fuzzy: bool = True) -> Any | None:
        """Поиск верхнеуровневого окна по названию или подстроке.

        Args:
            title_pattern: Искомый заголовок окна. Пустая строка возвращает активное окно.
            fuzzy: Использовать нечеткое сопоставление заголовков.

        Returns:
            Объект окна или None при отсутствии совпадений.
        """
        with safe_execution(self.monitor) as safe:
            safe.check()

            pattern = title_pattern.strip()
            if not pattern:
                return self._get_active_or_root_window()

            top_windows = self._get_top_windows()
            best_window: Any | None = None
            best_score: float = 0.0
            pattern_lower = pattern.lower()

            for win in top_windows:
                safe.check()
                name = str(getattr(win, "Name", "") or "").strip()
                if not name:
                    continue
                name_lower = name.lower()

                if pattern_lower == name_lower:
                    return win

                if pattern_lower in name_lower and best_score < 90.0:
                    best_window = win
                    best_score = 90.0
                    continue

                if fuzzy and fuzz is not None:
                    score = float(fuzz.ratio(pattern_lower, name_lower))
                    if score >= 50.0 and score > best_score:
                        best_window = win
                        best_score = score

            if best_window is not None:
                return best_window

            return self._search_window_control_fallback(pattern)

    def _find_target_element(
        self,
        parent: Any,
        element_name: str,
        control_type: str | int | None = None,
        max_depth: int = 12,
    ) -> Any | None:
        """Поиск элемента в дереве окна по имени и типу контрола."""
        target_name = element_name.strip().lower()
        if not target_name:
            return None

        expected_type_id = self._resolve_control_type(control_type)
        start_time = time.monotonic()
        best_candidate: Any | None = None
        best_score: float = 0.0

        for child, _ in self._walk_control_safe(parent, max_depth):
            self.monitor.check_abort()
            if time.monotonic() - start_time > self.timeout:
                logger.debug("Превышен таймаут поиска элемента '%s'", element_name)
                break

            try:
                c_type = getattr(child, "ControlType", None)
                if expected_type_id is not None and c_type != expected_type_id:
                    continue

                name = str(getattr(child, "Name", "") or "").strip().lower()
                aid = str(getattr(child, "AutomationId", "") or "").strip().lower()

                if name == target_name or aid == target_name:
                    return child

                if target_name in name and best_score < 90.0:
                    best_candidate = child
                    best_score = 90.0
                    continue

                if fuzz is not None and name:
                    score = float(fuzz.ratio(target_name, name))
                    if score >= 75.0 and score > best_score:
                        best_candidate = child
                        best_score = score
            except Exception:
                continue

        return best_candidate

    def _execute_click_or_invoke(self, ctrl: Any) -> bool:
        """Вызывает клик по элементу через InvokePattern, TogglePattern или прямой Click."""
        inv_id = getattr(self._auto.PatternId, "InvokePattern", 10000)
        invoke_pattern = self._get_pattern(ctrl, inv_id)
        if invoke_pattern and hasattr(invoke_pattern, "Invoke"):
            try:
                invoke_pattern.Invoke()
                return True
            except Exception as exc:
                logger.debug("InvokePattern завершился ошибкой: %s, пробуем Click", exc)

        tog_id = getattr(self._auto.PatternId, "TogglePattern", 10015)
        toggle_pattern = self._get_pattern(ctrl, tog_id)
        if toggle_pattern and hasattr(toggle_pattern, "Toggle"):
            try:
                toggle_pattern.Toggle()
                return True
            except Exception as exc:
                logger.debug("TogglePattern завершился ошибкой: %s, пробуем Click", exc)

        if hasattr(ctrl, "Click"):
            try:
                ctrl.Click()
                return True
            except Exception as exc:
                logger.debug("Click() завершился ошибкой: %s", exc)

        if hasattr(ctrl, "GetLegacyIAccessiblePattern"):
            try:
                legacy = ctrl.GetLegacyIAccessiblePattern()
                if legacy and hasattr(legacy, "DoDefaultAction"):
                    legacy.DoDefaultAction()
                    return True
            except Exception as exc:
                logger.debug("DoDefaultAction завершился ошибкой: %s", exc)

        return False

    def click_element(
        self,
        element_name: str,
        window_title: str | None = None,
        control_type: str | None = None,
    ) -> bool:
        """Поиск интерактивного элемента по имени в окне и вызов Click или InvokePattern.

        Args:
            element_name: Имя, AutomationId или текст элемента (кнопка, пункт меню и т.д.).
            window_title: Заголовок целевого окна. Если None, используется активное окно.
            control_type: Желаемый тип контрола ('button', 'menuitem', 'hyperlink' и др.).

        Returns:
            True при успешном клике, False если элемент или окно не найдены.
        """
        with safe_execution(self.monitor) as safe:
            safe.check()

            window = (
                self.find_window(window_title)
                if window_title is not None
                else self._get_active_or_root_window()
            )
            if window is None:
                logger.warning("Окно не найдено для клика по элементу '%s'", element_name)
                return False

            ctrl = self._find_target_element(window, element_name, control_type)
            if ctrl is None:
                logger.warning("Элемент '%s' не найден в окне", element_name)
                return False

            safe.check()
            return self._execute_click_or_invoke(ctrl)

    def type_into_element(
        self,
        element_name: str,
        text: str,
        window_title: str | None = None,
        clear_first: bool = False,
    ) -> bool:
        """Поиск текстового поля, установка фокуса и ввод текста через ValuePattern или SendKeys.

        Args:
            element_name: Имя или AutomationId поля ввода.
            text: Текст для ввода.
            window_title: Заголовок целевого окна. Если None, используется активное окно.
            clear_first: Очищать ли предварительно содержимое поля ввода.

        Returns:
            True при успешном вводе текста, False при ошибке.
        """
        with safe_execution(self.monitor) as safe:
            safe.check()

            window = (
                self.find_window(window_title)
                if window_title is not None
                else self._get_active_or_root_window()
            )
            if window is None:
                logger.warning("Окно не найдено для ввода текста в '%s'", element_name)
                return False

            ctrl = self._find_target_element(window, element_name, control_type="edit")
            if ctrl is None:
                ctrl = self._find_target_element(window, element_name, control_type=None)
            if ctrl is None:
                logger.warning("Поле ввода '%s' не найдено", element_name)
                return False

            safe.check()
            if hasattr(ctrl, "SetFocus"):
                try:
                    ctrl.SetFocus()
                except Exception:
                    pass

            val_id = getattr(self._auto.PatternId, "ValuePattern", 10002)
            val_pattern = self._get_pattern(ctrl, val_id)
            if val_pattern is not None and not getattr(val_pattern, "IsReadOnly", False):
                try:
                    if clear_first:
                        val_pattern.SetValue(text)
                    else:
                        current_val = str(getattr(val_pattern, "Value", "") or "")
                        val_pattern.SetValue(current_val + text)
                    return True
                except Exception as exc:
                    logger.debug("SetValue вызвал исключение: %s, fallback на SendKeys", exc)

            safe.check()
            if clear_first and hasattr(self._auto, "SendKeys"):
                self._auto.SendKeys("{Ctrl}a{Delete}")
                safe.check()

            if hasattr(self._auto, "SendKeys"):
                self._auto.SendKeys(text)
                return True

            return False

    def set_toggle_state(
        self,
        element_name: str,
        state: bool,
        window_title: str | None = None,
    ) -> bool:
        """Установка состояния переключателя (CheckBox, RadioButton).

        Args:
            element_name: Имя или AutomationId переключателя.
            state: True для включения (On/Checked), False для выключения (Off/Unchecked).
            window_title: Заголовок целевого окна. Если None, используется активное окно.

        Returns:
            True при успешной смене состояния, False при ошибке.
        """
        with safe_execution(self.monitor) as safe:
            safe.check()

            window = (
                self.find_window(window_title)
                if window_title is not None
                else self._get_active_or_root_window()
            )
            if window is None:
                logger.warning("Окно не найдено для переключения '%s'", element_name)
                return False

            ctrl = self._find_target_element(window, element_name, control_type="checkbox")
            if ctrl is None:
                ctrl = self._find_target_element(window, element_name, control_type="radiobutton")
            if ctrl is None:
                ctrl = self._find_target_element(window, element_name, control_type=None)
            if ctrl is None:
                logger.warning("Переключатель '%s' не найден", element_name)
                return False

            safe.check()
            on_val = getattr(getattr(self._auto, "ToggleState", None), "On", 1)
            off_val = getattr(getattr(self._auto, "ToggleState", None), "Off", 0)
            target_val = on_val if state else off_val

            toggle_id = getattr(self._auto.PatternId, "TogglePattern", 10015)
            toggle = self._get_pattern(ctrl, toggle_id)
            if toggle is not None:
                current_state = getattr(toggle, "ToggleState", None)
                if current_state == target_val:
                    return True
                if hasattr(toggle, "SetToggleState"):
                    try:
                        toggle.SetToggleState(target_val)
                        return True
                    except Exception:
                        pass
                if hasattr(toggle, "Toggle"):
                    try:
                        toggle.Toggle()
                        return True
                    except Exception:
                        pass

            if hasattr(ctrl, "Click"):
                try:
                    ctrl.Click()
                    return True
                except Exception:
                    pass

            return False

    def _extract_element_info(self, ctrl: Any) -> dict[str, Any] | None:
        """Извлекает структурированную информацию об интерактивном элементе интерфейса."""
        try:
            name = str(getattr(ctrl, "Name", "") or "").strip()
            aid = str(getattr(ctrl, "AutomationId", "") or "").strip()
            c_type_id = int(getattr(ctrl, "ControlType", 0))
            c_type_name = str(getattr(ctrl, "ControlTypeName", "") or "")

            rect_obj = getattr(ctrl, "BoundingRectangle", None)
            if rect_obj is not None:
                w_fn = getattr(rect_obj, "width", 0)
                h_fn = getattr(rect_obj, "height", 0)
                rect_dict = {
                    "left": int(getattr(rect_obj, "left", 0)),
                    "top": int(getattr(rect_obj, "top", 0)),
                    "right": int(getattr(rect_obj, "right", 0)),
                    "bottom": int(getattr(rect_obj, "bottom", 0)),
                    "width": int(w_fn() if callable(w_fn) else w_fn),
                    "height": int(h_fn() if callable(h_fn) else h_fn),
                }
            else:
                rect_dict = {
                    "left": 0,
                    "top": 0,
                    "right": 0,
                    "bottom": 0,
                    "width": 0,
                    "height": 0,
                }

            val_id = getattr(self._auto.PatternId, "ValuePattern", 10002)
            val_p = self._get_pattern(ctrl, val_id)
            value_str = getattr(val_p, "Value", None) if val_p else None

            tog_id = getattr(self._auto.PatternId, "TogglePattern", 10015)
            tog_p = self._get_pattern(ctrl, tog_id)
            toggle_state = getattr(tog_p, "ToggleState", None) if tog_p else None

            return {
                "name": name,
                "control_type": c_type_name,
                "control_type_id": c_type_id,
                "automation_id": aid,
                "rect": rect_dict,
                "is_enabled": bool(getattr(ctrl, "IsEnabled", True)),
                "is_offscreen": bool(getattr(ctrl, "IsOffscreen", False)),
                "value": value_str,
                "toggle_state": toggle_state,
            }
        except Exception as exc:
            logger.debug("Ошибка извлечения данных элемента: %s", exc)
            return None

    def inspect_window_elements(
        self,
        window_title: str | None = None,
        max_depth: int = 3,
    ) -> list[dict[str, Any]]:
        """Инспекция и сбор списка интерактивных элементов в окне приложения.

        Args:
            window_title: Заголовок целевого окна. Если None, используется активное окно.
            max_depth: Максимальная глубина обхода иерархического дерева элементов.

        Returns:
            Список словарей с описанием найденных элементов (имя, тип, координаты, состояние).
        """
        with safe_execution(self.monitor) as safe:
            safe.check()

            window = (
                self.find_window(window_title)
                if window_title is not None
                else self._get_active_or_root_window()
            )
            if window is None:
                return []

            elements: list[dict[str, Any]] = []
            start_time = time.monotonic()
            effective_depth = max(1, max_depth)

            for child, _ in self._walk_control_safe(window, effective_depth):
                safe.check()
                if time.monotonic() - start_time > self.timeout:
                    logger.debug("Таймаут инспекции элементов окна: %.2f с", self.timeout)
                    break

                info = self._extract_element_info(child)
                if info is not None and (info["name"] or info["automation_id"]):
                    elements.append(info)

            return elements
