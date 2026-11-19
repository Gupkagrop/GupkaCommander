# ruff: noqa: N802, N803
"""Модульные тесты для контроллера Microsoft UI Automation (core/uia/uia_controller.py).

Проверяет детерминированное управление элементами Windows:
- Клик по кнопкам и элементам через InvokePattern, TogglePattern и Click.
- Ввод текста через ValuePattern и SendKeys с очисткой и дописыванием.
- Управление состоянием флажков и переключателей (TogglePattern).
- Инспекцию дерева интерактивных элементов окна.
- Безопасную обработку отсутствующих окон и элементов (без исключений).
- Мгновенное аварийное прерывание по сигналам FailSafeMonitor.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from core.failsafe import FailSafeMonitor
from core.uia.uia_controller import CONTROL_TYPE_MAP, UIAutomationManager


class MockRect:
    """Мок прямоугольника BoundingRectangle элемента UI Automation."""

    def __init__(
        self,
        left: int = 100,
        top: int = 200,
        right: int = 300,
        bottom: int = 250,
    ) -> None:
        """Инициализация координат прямоугольника."""
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom

    def width(self) -> int:
        """Ширина прямоугольника."""
        return self.right - self.left

    def height(self) -> int:
        """Высота прямоугольника."""
        return self.bottom - self.top


class MockInvokePattern:
    """Мок паттерна InvokePattern (UIA ID 10000)."""

    def __init__(self) -> None:
        """Инициализация мока InvokePattern."""
        self.invoked = False

    def Invoke(self) -> None:
        """Эмуляция вызова действия контрола."""
        self.invoked = True


class MockValuePattern:
    """Мок паттерна ValuePattern (UIA ID 10002)."""

    def __init__(self, initial_value: str = "", is_read_only: bool = False) -> None:
        """Инициализация мока ValuePattern."""
        self.Value = initial_value
        self.IsReadOnly = is_read_only
        self.set_values: list[str] = []

    def SetValue(self, val: str) -> None:
        """Эмуляция установки текстового значения."""
        if self.IsReadOnly:
            raise RuntimeError("Element is read-only")
        self.Value = val
        self.set_values.append(val)


class MockTogglePattern:
    """Мок паттерна TogglePattern (UIA ID 10015)."""

    def __init__(self, initial_state: int = 0) -> None:
        """Инициализация мока TogglePattern (0=Off, 1=On, 2=Indeterminate)."""
        self.ToggleState = initial_state
        self.toggled_count = 0

    def Toggle(self) -> None:
        """Эмуляция циклического переключения состояния."""
        self.toggled_count += 1
        self.ToggleState = 1 if self.ToggleState == 0 else 0

    def SetToggleState(self, state: int) -> None:
        """Эмуляция прямой установки состояния флажка."""
        self.ToggleState = state


class MockLegacyIAccessiblePattern:
    """Мок устаревшего паттерна LegacyIAccessible."""

    def __init__(self) -> None:
        """Инициализация мока LegacyIAccessible."""
        self.default_action_called = False

    def DoDefaultAction(self) -> None:
        """Эмуляция вызова стандартного действия."""
        self.default_action_called = True


class MockControl:
    """Мок элемента управления интерфейса Windows UI Automation."""

    def __init__(
        self,
        name: str = "",
        automation_id: str = "",
        control_type: int = 50000,
        control_type_name: str = "ButtonControl",
        children: list[MockControl] | None = None,
        rect: MockRect | None = None,
        is_enabled: bool = True,
        is_offscreen: bool = False,
        can_click: bool = True,
        legacy_pattern: Any | None = None,
    ) -> None:
        """Инициализация мок-контрола."""
        self.Name = name
        self.AutomationId = automation_id
        self.ControlType = control_type
        self.ControlTypeName = control_type_name
        self.children: list[MockControl] = children or []
        self.BoundingRectangle = rect or MockRect()
        self.IsEnabled = is_enabled
        self.IsOffscreen = is_offscreen
        self._legacy_pattern = legacy_pattern

        self._patterns: dict[int, Any] = {}
        self.clicked = False
        self.focused = False

        if can_click:
            self.Click = self._click_impl

    def _click_impl(self) -> None:
        """Эмуляция прямого клика по координатам элемента."""
        self.clicked = True

    def GetLegacyIAccessiblePattern(self) -> Any | None:
        """Получение устаревшего паттерна LegacyIAccessible."""
        return self._legacy_pattern

    def add_pattern(self, pattern_id: int, pattern_obj: Any) -> MockControl:
        """Регистрация UIA паттерна в мок-контроле."""
        self._patterns[pattern_id] = pattern_obj
        return self

    def GetPattern(self, pattern_id: int) -> Any | None:
        """Получение зарегистрированного паттерна элемента."""
        return self._patterns.get(pattern_id)

    def GetChildren(self) -> list[MockControl]:
        """Возвращает список дочерних элементов."""
        return self.children

    def SetFocus(self) -> None:
        """Эмуляция передачи фокуса ввода элементу."""
        self.focused = True


class MockPatternId:
    """Числовые константы идентификаторов паттернов UIA."""

    InvokePattern = 10000
    ValuePattern = 10002
    TogglePattern = 10015


class MockToggleState:
    """Константы состояний переключателей UIA."""

    Off = 0
    On = 1
    Indeterminate = 2


class MockAutoModule:
    """Мок главного модуля библиотеки uiautomation."""

    def __init__(self, root: MockControl | None = None) -> None:
        """Инициализация мок-модуля uiautomation."""
        self.PatternId = MockPatternId()
        self.ToggleState = MockToggleState()
        self.root_control = root or MockControl(name="DesktopRoot", control_type=50033)
        self.foreground_control: MockControl | None = None
        self.sent_keys: list[str] = []
        self.global_timeout: float = 2.0

    def SetGlobalSearchTimeout(self, timeout: float) -> None:
        """Установка глобального таймаута поиска."""
        self.global_timeout = timeout

    def GetRootControl(self) -> MockControl:
        """Получение корневого узла рабочего стола."""
        return self.root_control

    def GetForegroundControl(self) -> MockControl | None:
        """Получение активного окна."""
        return self.foreground_control

    def WalkControl(
        self, parent: Any, includeTop: bool = False, maxDepth: int = 12
    ) -> Iterator[tuple[Any, int]]:
        """Обход иерархического дерева элементов в глубину."""
        if includeTop:
            yield parent, 0

        def _traverse(node: Any, depth: int) -> Iterator[tuple[Any, int]]:
            if depth > maxDepth:
                return
            children = node.GetChildren() if hasattr(node, "GetChildren") else []
            for child in children:
                yield child, depth
                yield from _traverse(child, depth + 1)

        yield from _traverse(parent, 1)

    def SendKeys(self, text: str) -> None:
        """Эмуляция отправки клавиатурных символов."""
        self.sent_keys.append(text)


@pytest.fixture
def mock_tree() -> tuple[MockAutoModule, MockControl, MockControl]:
    """Фикстура с типовым деревом окон и элементов интерфейса."""
    # Создаем кнопку
    button = MockControl(
        name="Сохранить",
        automation_id="btn_save",
        control_type=CONTROL_TYPE_MAP["button"],
        control_type_name="ButtonControl",
    )
    inv_pat = MockInvokePattern()
    button.add_pattern(MockPatternId.InvokePattern, inv_pat)

    # Создаем поле ввода
    edit = MockControl(
        name="Имя файла",
        automation_id="txt_filename",
        control_type=CONTROL_TYPE_MAP["edit"],
        control_type_name="EditControl",
    )
    val_pat = MockValuePattern(initial_value="doc1")
    edit.add_pattern(MockPatternId.ValuePattern, val_pat)

    # Создаем флажок
    checkbox = MockControl(
        name="Автосохранение",
        automation_id="chk_autosave",
        control_type=CONTROL_TYPE_MAP["checkbox"],
        control_type_name="CheckBoxControl",
    )
    tog_pat = MockTogglePattern(initial_state=0)
    checkbox.add_pattern(MockPatternId.TogglePattern, tog_pat)

    # Создаем главное окно
    window = MockControl(
        name="Блокнот",
        automation_id="win_notepad",
        control_type=CONTROL_TYPE_MAP["window"],
        control_type_name="WindowControl",
        children=[button, edit, checkbox],
    )

    # Корневой узел рабочего стола
    root = MockControl(
        name="Desktop",
        control_type=CONTROL_TYPE_MAP["pane"],
        children=[window],
    )

    mock_auto = MockAutoModule(root=root)
    mock_auto.foreground_control = window

    return mock_auto, window, button


class TestUIAutomationManagerUnit:
    """Модульные тесты для класса UIAutomationManager."""

    def test_find_window_exact_and_fuzzy(self, mock_tree: tuple[Any, Any, Any]) -> None:
        """Поиск окна по точному и нечеткому названию."""
        mock_auto, window, _ = mock_tree
        manager = UIAutomationManager(auto_module=mock_auto)

        # Точное совпадение
        found = manager.find_window("Блокнот")
        assert found is window

        # Нечеткое / подстрочное совпадение
        found_fuzzy = manager.find_window("блокно")
        assert found_fuzzy is window

        # Активное окно при пустой строке
        found_active = manager.find_window("")
        assert found_active is window

    def test_find_window_not_found(self, mock_tree: tuple[Any, Any, Any]) -> None:
        """Поиск несуществующего окна корректно возвращает None."""
        mock_auto, _, _ = mock_tree
        manager = UIAutomationManager(auto_module=mock_auto)

        found = manager.find_window("НесуществующееОкно12345")
        assert found is None

    def test_click_element_invoke_pattern(self, mock_tree: tuple[Any, Any, Any]) -> None:
        """Успешный клик по кнопке через паттерн InvokePattern."""
        mock_auto, window, button = mock_tree
        manager = UIAutomationManager(auto_module=mock_auto)

        inv_pat: MockInvokePattern = button.GetPattern(MockPatternId.InvokePattern)
        assert not inv_pat.invoked

        res = manager.click_element("Сохранить", window_title="Блокнот", control_type="button")
        assert res is True
        assert inv_pat.invoked is True

    def test_click_element_fallback_to_click(self, mock_tree: tuple[Any, Any, Any]) -> None:
        """Клик по элементу через метод Click() при отсутствии InvokePattern."""
        mock_auto, _, _ = mock_tree
        simple_btn = MockControl(
            name="Применить",
            control_type=CONTROL_TYPE_MAP["button"],
        )
        mock_auto.root_control.children[0].children.append(simple_btn)

        manager = UIAutomationManager(auto_module=mock_auto)
        res = manager.click_element("Применить", window_title="Блокнот")
        assert res is True
        assert simple_btn.clicked is True

    def test_click_element_fallback_to_legacy(self, mock_tree: tuple[Any, Any, Any]) -> None:
        """Клик по элементу через LegacyIAccessiblePattern."""
        mock_auto, _, _ = mock_tree
        legacy_pat = MockLegacyIAccessiblePattern()
        legacy_btn = MockControl(
            name="Печать",
            control_type=CONTROL_TYPE_MAP["button"],
            can_click=False,
            legacy_pattern=legacy_pat,
        )
        mock_auto.root_control.children[0].children.append(legacy_btn)

        manager = UIAutomationManager(auto_module=mock_auto)
        res = manager.click_element("Печать", window_title="Блокнот")
        assert res is True
        assert legacy_pat.default_action_called is True

    def test_type_into_element_value_pattern(self, mock_tree: tuple[Any, Any, Any]) -> None:
        """Ввод текста в текстовое поле через ValuePattern."""
        mock_auto, _, _ = mock_tree
        manager = UIAutomationManager(auto_module=mock_auto)

        # Дописывание текста
        res_append = manager.type_into_element("Имя файла", "_updated", window_title="Блокнот")
        assert res_append is True
        edit_ctrl = mock_auto.root_control.children[0].children[1]
        val_pat: MockValuePattern = edit_ctrl.GetPattern(MockPatternId.ValuePattern)
        assert val_pat.Value == "doc1_updated"

        # Замена текста с флагом clear_first
        res_replace = manager.type_into_element(
            "Имя файла", "new_name", window_title="Блокнот", clear_first=True
        )
        assert res_replace is True
        assert val_pat.Value == "new_name"

    def test_type_into_element_fallback_to_sendkeys(self, mock_tree: tuple[Any, Any, Any]) -> None:
        """Ввод текста через SendKeys при отсутствии ValuePattern или read-only поле."""
        mock_auto, _, _ = mock_tree
        edit_no_pattern = MockControl(
            name="Строка поиска",
            control_type=CONTROL_TYPE_MAP["edit"],
        )
        mock_auto.root_control.children[0].children.append(edit_no_pattern)

        manager = UIAutomationManager(auto_module=mock_auto)
        res = manager.type_into_element(
            "Строка поиска", "поисковый запрос", window_title="Блокнот", clear_first=True
        )
        assert res is True
        assert edit_no_pattern.focused is True
        assert "{Ctrl}a{Delete}" in mock_auto.sent_keys
        assert "поисковый запрос" in mock_auto.sent_keys

    def test_set_toggle_state_checkbox(self, mock_tree: tuple[Any, Any, Any]) -> None:
        """Переключение состояния флажка CheckBox через TogglePattern."""
        mock_auto, _, _ = mock_tree
        manager = UIAutomationManager(auto_module=mock_auto)

        chk_ctrl = mock_auto.root_control.children[0].children[2]
        tog_pat: MockTogglePattern = chk_ctrl.GetPattern(MockPatternId.TogglePattern)
        assert tog_pat.ToggleState == 0

        # Включение флажка
        res_on = manager.set_toggle_state("Автосохранение", state=True, window_title="Блокнот")
        assert res_on is True
        assert tog_pat.ToggleState == 1

        # Повторное включение (уже включен) -> мгновенный True
        res_already_on = manager.set_toggle_state(
            "Автосохранение", state=True, window_title="Блокнот"
        )
        assert res_already_on is True

        # Выключение флажка
        res_off = manager.set_toggle_state("Автосохранение", state=False, window_title="Блокнот")
        assert res_off is True
        assert tog_pat.ToggleState == 0

    def test_inspect_window_elements(self, mock_tree: tuple[Any, Any, Any]) -> None:
        """Инспекция и извлечение структурированного списка интерактивных элементов окна."""
        mock_auto, _, _ = mock_tree
        manager = UIAutomationManager(auto_module=mock_auto)

        elements = manager.inspect_window_elements(window_title="Блокнот", max_depth=3)
        assert len(elements) == 3

        names = [el["name"] for el in elements]
        assert "Сохранить" in names
        assert "Имя файла" in names
        assert "Автосохранение" in names

        # Проверка структуры элемента
        save_btn = next(el for el in elements if el["name"] == "Сохранить")
        assert save_btn["automation_id"] == "btn_save"
        assert save_btn["control_type"] == "ButtonControl"
        assert save_btn["is_enabled"] is True
        assert save_btn["rect"]["width"] == 200
        assert save_btn["rect"]["height"] == 50

    def test_missing_window_and_element_safe_handling(
        self, mock_tree: tuple[Any, Any, Any]
    ) -> None:
        """Обработка несуществующих окон и элементов без необработанных исключений."""
        mock_auto, _, _ = mock_tree
        manager = UIAutomationManager(auto_module=mock_auto)

        # Несуществующее окно
        assert (
            manager.click_element("Кнопка", window_title="НесуществующееОкно12345") is False
        )
        assert (
            manager.type_into_element(
                "Поле", "текст", window_title="НесуществующееОкно12345"
            )
            is False
        )
        assert (
            manager.set_toggle_state(
                "Флажок", True, window_title="НесуществующееОкно12345"
            )
            is False
        )
        assert manager.inspect_window_elements(window_title="НесуществующееОкно12345") == []

        # Несуществующий элемент в существующем окне
        assert manager.click_element("НесуществующаяКнопка", window_title="Блокнот") is False
        assert (
            manager.type_into_element(
                "НесуществующееПоле", "текст", window_title="Блокнот"
            )
            is False
        )
        assert (
            manager.set_toggle_state(
                "НесуществующийЧекбокс", True, window_title="Блокнот"
            )
            is False
        )

    def test_failsafe_interruption(self, mock_tree: tuple[Any, Any, Any]) -> None:
        """Прерывание операций по взведению флага FailSafeMonitor."""
        mock_auto, _, _ = mock_tree
        monitor = FailSafeMonitor()
        manager = UIAutomationManager(auto_module=mock_auto, monitor=monitor)

        # Взводим аварийный сигнал безопасности
        monitor.trigger_abort("Пользователь нажал клавишу Escape")

        # Все операции должны немедленно выбрасывать InterruptedError
        with pytest.raises(InterruptedError, match="FailSafe abort"):
            manager.find_window("Блокнот")

        with pytest.raises(InterruptedError, match="FailSafe abort"):
            manager.click_element("Сохранить", window_title="Блокнот")

        with pytest.raises(InterruptedError, match="FailSafe abort"):
            manager.type_into_element("Имя файла", "текст", window_title="Блокнот")

        with pytest.raises(InterruptedError, match="FailSafe abort"):
            manager.set_toggle_state("Автосохранение", True, window_title="Блокнот")

        with pytest.raises(InterruptedError, match="FailSafe abort"):
            manager.inspect_window_elements(window_title="Блокнот")
