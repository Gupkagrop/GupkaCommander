"""
> [!NOTE]
> Модуль gui.agent_widgets: Компоненты Command Deck и Live Agent Mini-Bar для GupkaCommander 2.0.
> Включает ContextChipBar (кликабельные контекстные чипы), StepCard (карточка шага ReAct
> с бейджем движка и сворачиваемыми деталями) и AgentMiniBar с кнопкой [Esc · Стоп].
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import override

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gui.sponge_pore_widget import PoreState, SpongePoreWidget
from gui.theme_manager import DARK_TOKENS, ThemeTokens


class StepEngine(StrEnum):
    """Движок выполнения шага в каскаде GupkaCommander 2.0."""

    FAST_PATH = "Fast-Path"
    UIA = "UIA"
    CDP = "CDP"
    VISION = "Vision"


class StepStatus(StrEnum):
    """Текущий статус выполнения отдельного шага ReAct."""

    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass(frozen=True)
class ContextChip:
    """Модель одного компактного чипа контекста в ContextChipBar."""

    chip_id: str
    label: str
    tooltip: str = ""
    active: bool = False


def coerce_step_engine(engine: StepEngine | str) -> StepEngine:
    """Нормализует строковое или enum-имя движка к StepEngine."""
    if isinstance(engine, StepEngine):
        return engine
    norm = engine.strip().lower()
    lookup: dict[str, StepEngine] = {
        "fast-path": StepEngine.FAST_PATH,
        "fast_path": StepEngine.FAST_PATH,
        "fastpath": StepEngine.FAST_PATH,
        "uia": StepEngine.UIA,
        "cdp": StepEngine.CDP,
        "vision": StepEngine.VISION,
    }
    return lookup.get(norm, StepEngine.UIA)


def coerce_step_status(status: StepStatus | str) -> StepStatus:
    """Нормализует строковое или enum-состояние шага к StepStatus."""
    if isinstance(status, StepStatus):
        return status
    norm = status.strip().lower()
    try:
        return StepStatus(norm)
    except ValueError:
        return StepStatus.RUNNING


def format_duration_ms(duration_ms: int | None) -> str:
    """Форматирует длительность шага в компактный русскоязычный вид (мс / с)."""
    if duration_ms is None or duration_ms < 0:
        return "—"
    if duration_ms < 1000:
        return f"{duration_ms} мс"
    return f"{duration_ms / 1000.0:.1f} с"


def _engine_badge_colors(engine: StepEngine, tokens: ThemeTokens) -> tuple[str, str, str]:
    """Возвращает кортеж (fg, bg, border) для бейджа конкретного движка."""
    if engine == StepEngine.FAST_PATH:
        return (tokens.success, tokens.success_soft, tokens.success_border)
    if engine == StepEngine.UIA:
        return (tokens.accent, tokens.accent_soft, tokens.accent_border)
    if engine == StepEngine.CDP:
        return (tokens.sponge_ochre, tokens.sponge_ochre_soft, tokens.sponge_ochre_border)
    return (tokens.danger, tokens.danger_soft, tokens.danger_border)


def _status_colors(status: StepStatus, tokens: ThemeTokens) -> tuple[str, str]:
    """Возвращает (цвет индикатора, русская подпись) для статуса шага."""
    if status == StepStatus.DONE:
        return (tokens.success, "Готово")
    if status == StepStatus.ERROR:
        return (tokens.danger, "Ошибка")
    return (tokens.sponge_ochre, "Выполняется")


class ContextChipBar(QWidget):
    """
    Компактная полоса кликабельных контекстных чипов Command Deck.

    Отображает активный монитор/DPI (`Мон. 2 · 150%`), активное окно,
    счётчик фактов памяти и режим голоса.
    """

    chip_clicked = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        tokens: ThemeTokens = DARK_TOKENS,
    ) -> None:
        """Инициализирует горизонтальный контейнер для чипов контекста."""
        super().__init__(parent)
        self.setObjectName("contextChipBar")
        self._tokens: ThemeTokens = tokens
        self._chips: list[ContextChip] = []
        self._buttons: dict[str, QPushButton] = {}

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._layout.addStretch(1)

    def chip_ids(self) -> list[str]:
        """Возвращает упорядоченный список идентификаторов текущих чипов."""
        return [chip.chip_id for chip in self._chips]

    def set_tokens(self, tokens: ThemeTokens) -> None:
        """Обновляет оформление всех чипов при смене темы Dark/Light."""
        self._tokens = tokens
        self._apply_button_styles()

    def set_chips(self, chips: list[ContextChip]) -> None:
        """Полностью пересобирает набор чипов в полосе контекста."""
        self._clear_buttons()
        self._chips = list(chips)

        for idx, chip in enumerate(self._chips):
            btn = self._create_chip_button(chip)
            self._buttons[chip.chip_id] = btn
            self._layout.insertWidget(idx, btn)

        self._apply_button_styles()

    def set_context(
        self,
        *,
        monitor_label: str = "Мон. 1 · 100%",
        active_app: str = "Рабочий стол",
        memory_count: int = 0,
        voice_mode: str = "Голос",
        voice_active: bool = True,
    ) -> None:
        """Устанавливает стандартный набор из четырёх чипов контекста Concept B."""
        chips = [
            ContextChip("monitor", monitor_label, "Активный монитор и масштаб DPI"),
            ContextChip("app", active_app, "Активное целевое приложение"),
            ContextChip(
                "memory", f"Память: {max(0, memory_count)}", "Записи долговременной памяти"
            ),
            ContextChip("voice", voice_mode, "Режим голосового управления", active=voice_active),
        ]
        self.set_chips(chips)

    def update_chip(
        self,
        chip_id: str,
        label: str,
        *,
        active: bool | None = None,
    ) -> bool:
        """Точечно обновляет текст и состояние активности существующего чипа."""
        btn = self._buttons.get(chip_id)
        if btn is None:
            return False

        btn.setText(label)
        updated_chips: list[ContextChip] = []
        for chip in self._chips:
            if chip.chip_id != chip_id:
                updated_chips.append(chip)
                continue
            next_active = chip.active if active is None else bool(active)
            updated_chips.append(ContextChip(chip.chip_id, label, chip.tooltip, active=next_active))
            btn.setProperty("active", next_active)

        self._chips = updated_chips
        self._apply_button_styles()
        return True

    def get_chip_button(self, chip_id: str) -> QPushButton | None:
        """Возвращает виджет кнопки чипа по идентификатору (или None)."""
        return self._buttons.get(chip_id)

    def _create_chip_button(self, chip: ContextChip) -> QPushButton:
        """Создаёт и настраивает одну кликабельную кнопку-пилюлю."""
        btn = QPushButton(chip.label, self)
        btn.setObjectName("contextChip")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFlat(True)
        btn.setToolTip(chip.tooltip)
        btn.setProperty("active", chip.active)
        btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        btn.clicked.connect(lambda _checked=False, cid=chip.chip_id: self.chip_clicked.emit(cid))
        return btn

    def _clear_buttons(self) -> None:
        """Безопасно удаляет все текущие кнопки из layout."""
        for btn in self._buttons.values():
            self._layout.removeWidget(btn)
            btn.setParent(None)
            btn.deleteLater()
        self._buttons.clear()

    def _apply_button_styles(self) -> None:
        """Применяет актуальные токены темы ко всем кнопкам-чипам."""
        t = self._tokens
        for chip in self._chips:
            btn = self._buttons.get(chip.chip_id)
            if btn is None:
                continue
            if chip.active:
                bg, fg, border = t.sponge_ochre_soft, t.sponge_ochre, t.sponge_ochre_border
            else:
                bg, fg, border = t.surface_chip, t.text_secondary, t.border_subtle
            btn.setStyleSheet(
                "QPushButton#contextChip {"
                f"  background-color: {bg}; color: {fg};"
                f"  border: 1px solid {border}; border-radius: 11px;"
                "  padding: 3px 10px; font-size: 11px; font-weight: 600;"
                "}"
                "QPushButton#contextChip:hover {"
                f"  background-color: {t.surface_chip_hover}; color: {t.text};"
                f"  border: 1px solid {t.border};"
                "}"
            )


class StepCard(QFrame):
    """
    Карточка шага ReAct с бейджем движка (Fast-Path / UIA / CDP / Vision),
    индикатором статуса, длительностью в мс и сворачиваемыми подробностями.
    """

    details_toggled = Signal(bool)

    def __init__(
        self,
        step_index: int,
        title: str,
        engine: StepEngine | str = StepEngine.UIA,
        *,
        status: StepStatus | str = StepStatus.RUNNING,
        duration_ms: int | None = None,
        details: str = "",
        tokens: ThemeTokens = DARK_TOKENS,
        parent: QWidget | None = None,
    ) -> None:
        """Инициализирует карточку шага с заданными метаданными."""
        super().__init__(parent)
        self.setObjectName("stepCard")
        self._step_index: int = max(1, int(step_index))
        self._title: str = title
        self._engine: StepEngine = coerce_step_engine(engine)
        self._status: StepStatus = coerce_step_status(status)
        self._duration_ms: int | None = duration_ms
        self._details_text: str = details
        self._details_expanded: bool = False
        self._tokens: ThemeTokens = tokens

        self._build_ui()
        self._refresh_view()

    @property
    def step_index(self) -> int:
        """Порядковый номер шага ReAct."""
        return self._step_index

    @property
    def engine(self) -> StepEngine:
        """Движок, выполняющий шаг."""
        return self._engine

    @property
    def status(self) -> StepStatus:
        """Текущий статус шага."""
        return self._status

    @property
    def details_expanded(self) -> bool:
        """Возвращает True, если секция подробностей развёрнута."""
        return self._details_expanded

    def update_status(
        self,
        status: StepStatus | str,
        *,
        duration_ms: int | None = None,
        details: str | None = None,
    ) -> None:
        """Обновляет статус, длительность и текст подробностей шага."""
        self._status = coerce_step_status(status)
        if duration_ms is not None:
            self._duration_ms = duration_ms
        if details is not None:
            self._details_text = details
        self._refresh_view()

    def set_tokens(self, tokens: ThemeTokens) -> None:
        """Обновляет палитру карточки при переключении Dark/Light темы."""
        self._tokens = tokens
        self._refresh_view()

    def set_details_expanded(self, expanded: bool) -> None:
        """Явно разворачивает или сворачивает блок подробностей шага."""
        target = bool(expanded)
        if target == self._details_expanded:
            return
        self._details_expanded = target
        self._details_label.setVisible(target and bool(self._details_text))
        self._update_toggle_text()
        self.details_toggled.emit(target)

    def toggle_details(self) -> None:
        """Переключает видимость блока подробностей."""
        self.set_details_expanded(not self._details_expanded)

    def _build_ui(self) -> None:
        """Создаёт структуру виджетов карточки шага."""
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 9, 12, 9)
        root.setSpacing(6)
        root.addLayout(self._build_header_row())

        self._details_label = QLabel(self)
        self._details_label.setObjectName("stepDetailsLabel")
        self._details_label.setWordWrap(True)
        self._details_label.setVisible(False)
        root.addWidget(self._details_label)

    def _build_header_row(self) -> QHBoxLayout:
        """Создаёт верхнюю строку карточки шага со статусом, бейджем и временем."""
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        self._status_dot = QLabel(self)
        self._status_dot.setObjectName("stepStatusDot")
        self._status_dot.setFixedSize(8, 8)
        header.addWidget(self._status_dot)

        self._index_label = QLabel(f"#{self._step_index}", self)
        self._index_label.setObjectName("stepIndexLabel")
        header.addWidget(self._index_label)

        self._title_label = QLabel(self._title, self)
        self._title_label.setObjectName("stepTitleLabel")
        self._title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        header.addWidget(self._title_label, 1)

        self._engine_badge = QLabel(self._engine.value, self)
        self._engine_badge.setObjectName("stepEngineBadge")
        header.addWidget(self._engine_badge)

        self._duration_label = QLabel(self)
        self._duration_label.setObjectName("stepDurationLabel")
        header.addWidget(self._duration_label)

        self._toggle_btn = QPushButton(self)
        self._toggle_btn.setObjectName("stepDetailsToggle")
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setFlat(True)
        self._toggle_btn.clicked.connect(self.toggle_details)
        header.addWidget(self._toggle_btn)
        return header

    def _update_toggle_text(self) -> None:
        """Синхронизирует текст и видимость кнопки раскрытия деталей."""
        has_details = bool(self._details_text.strip())
        self._toggle_btn.setVisible(has_details)
        self._toggle_btn.setText("Скрыть ▴" if self._details_expanded else "Детали ▾")

    def _refresh_view(self) -> None:
        """Применяет актуальные данные и токены темы ко всем элементам карточки."""
        t = self._tokens
        status_color, status_ru = _status_colors(self._status, t)
        eng_fg, eng_bg, eng_border = _engine_badge_colors(self._engine, t)

        self.setStyleSheet(
            f"QFrame#stepCard {{ background-color: {t.surface_card}; "
            f"border: 1px solid {t.border_subtle}; border-radius: 12px; }}"
        )
        self._status_dot.setToolTip(status_ru)
        self._status_dot.setStyleSheet(
            f"background-color: {status_color}; border: none; border-radius: 4px;"
        )
        self._index_label.setStyleSheet(
            f"background: transparent; border: none; color: {t.text_muted}; "
            "font-size: 11px; font-weight: 700;"
        )
        self._title_label.setText(self._title)
        self._title_label.setStyleSheet(
            f"background: transparent; border: none; color: {t.text}; "
            "font-size: 12px; font-weight: 600;"
        )
        self._engine_badge.setText(self._engine.value)
        self._engine_badge.setStyleSheet(
            f"color: {eng_fg}; background-color: {eng_bg}; "
            f"border: 1px solid {eng_border}; border-radius: 8px; "
            "padding: 2px 7px; font-size: 10px; font-weight: 700;"
        )
        self._duration_label.setText(format_duration_ms(self._duration_ms))
        self._duration_label.setStyleSheet(
            f"background: transparent; border: none; color: {t.text_secondary}; "
            "padding: 0px 2px; font-size: 11px; font-weight: 500;"
        )
        self._toggle_btn.setStyleSheet(
            f"QPushButton#stepDetailsToggle {{ color: {t.text_secondary}; border: none; "
            "background: transparent; font-size: 11px; padding: 2px 4px; }"
            f"QPushButton#stepDetailsToggle:hover {{ color: {t.text}; }}"
        )
        self._details_label.setText(self._details_text)
        self._details_label.setStyleSheet(
            f"color: {t.text_secondary}; background-color: {t.surface_chip}; "
            f"border: 1px solid {t.border_subtle}; border-radius: 8px; "
            "padding: 6px 8px; font-size: 11px;"
        )
        self._details_label.setVisible(self._details_expanded and bool(self._details_text.strip()))
        self._update_toggle_text()


class AgentMiniBar(QFrame):
    """
    Компактная однострочная панель Live Agent Mini-Bar для режима ReAct Computer-Use.

    Содержит живой индикатор SpongePoreWidget, заголовок текущего шага,
    бейдж движка, полосу прогресса и кнопку экстренной остановки [Esc · Стоп].
    """

    abort_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        tokens: ThemeTokens = DARK_TOKENS,
    ) -> None:
        """Инициализирует компактную полосу сопровождения активного агента."""
        super().__init__(parent)
        self.setObjectName("agentMiniBar")
        self._tokens: ThemeTokens = tokens
        self._engine: StepEngine = StepEngine.UIA

        self._build_ui()
        self._apply_styles()

    @property
    def pore_widget(self) -> SpongePoreWidget:
        """Возвращает встроенный виджет микропористой матрицы."""
        return self._pore

    @property
    def abort_button(self) -> QPushButton:
        """Возвращает кнопку экстренного прерывания [Esc · Стоп]."""
        return self._abort_btn

    @property
    def progress_bar(self) -> QProgressBar:
        """Возвращает встроенный индикатор прогресса шагов."""
        return self._progress

    def step_text(self) -> str:
        """Возвращает текущий текст заголовка шага."""
        return self._step_label.text()

    def set_tokens(self, tokens: ThemeTokens) -> None:
        """Применяет новые дизайн-токены ко всем элементам мини-бара."""
        self._tokens = tokens
        self._pore.set_tokens(tokens)
        self._apply_styles()

    def set_state(self, state: PoreState | str) -> None:
        """Передаёт состояние конвейера встроенному SpongePoreWidget."""
        self._pore.set_state(state)

    def update_step(
        self,
        step_index: int,
        total_steps: int,
        title: str,
        engine: StepEngine | str = StepEngine.UIA,
        *,
        state: PoreState | str = PoreState.PROCESSING,
    ) -> None:
        """Обновляет отображаемый шаг, бейдж движка, прогресс и состояние индикатора."""
        self._engine = coerce_step_engine(engine)
        safe_total = max(0, int(total_steps))
        safe_step = max(0, int(step_index))

        prefix = f"Шаг {safe_step}/{safe_total}" if safe_total > 0 else f"Шаг {safe_step}"
        self._step_label.setText(f"{prefix} · {title}")
        self._engine_badge.setText(self._engine.value)

        if safe_total > 0:
            self._progress.setRange(0, safe_total)
            self._progress.setValue(min(safe_step, safe_total))
        else:
            self._progress.setRange(0, 0)

        self._pore.set_state(state)
        self._apply_styles()

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Поддерживает мгновенное прерывание агента по клавише Esc."""
        if event.key() == Qt.Key.Key_Escape:
            self.abort_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _build_ui(self) -> None:
        """Собирает однострочный компактный макет Live Agent Mini-Bar."""
        self.setMinimumHeight(44)
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 6, 10, 6)
        root.setSpacing(10)

        self._pore = SpongePoreWidget(self, tokens=self._tokens, size=28)
        self._pore.setFixedSize(28, 28)
        root.addWidget(self._pore)
        root.addLayout(self._build_center_column(), 1)

        self._abort_btn = QPushButton("Esc · Стоп", self)
        self._abort_btn.setObjectName("miniAbortBtn")
        self._abort_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._abort_btn.clicked.connect(self.abort_requested.emit)
        root.addWidget(self._abort_btn)

    def _build_center_column(self) -> QVBoxLayout:
        """Создаёт центральную колонку с названием шага, бейджем и прогрессом."""
        center = QVBoxLayout()
        center.setContentsMargins(0, 0, 0, 0)
        center.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        self._step_label = QLabel("Готов к выполнению", self)
        self._step_label.setObjectName("miniStepLabel")
        self._step_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        top_row.addWidget(self._step_label, 1)

        self._engine_badge = QLabel(self._engine.value, self)
        self._engine_badge.setObjectName("miniEngineBadge")
        top_row.addWidget(self._engine_badge)
        center.addLayout(top_row)

        self._progress = QProgressBar(self)
        self._progress.setObjectName("miniProgressBar")
        self._progress.setTextVisible(False)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedHeight(4)
        center.addWidget(self._progress)
        return center

    def _apply_styles(self) -> None:
        """Обновляет QSS мини-бара, бейджа движка, прогресса и кнопки остановки."""
        t = self._tokens
        eng_fg, eng_bg, eng_border = _engine_badge_colors(self._engine, t)

        self.setStyleSheet(
            f"QFrame#agentMiniBar {{ background-color: {t.surface_elevated}; "
            f"border: 1px solid {t.border}; border-radius: 14px; }}"
        )
        self._step_label.setStyleSheet(
            f"background: transparent; border: none; color: {t.text}; "
            "font-size: 12px; font-weight: 600;"
        )
        self._engine_badge.setStyleSheet(
            f"color: {eng_fg}; background-color: {eng_bg}; "
            f"border: 1px solid {eng_border}; border-radius: 7px; "
            "padding: 1px 6px; font-size: 10px; font-weight: 700;"
        )
        self._progress.setStyleSheet(
            f"QProgressBar#miniProgressBar {{ background-color: {t.surface_chip}; "
            "border: none; border-radius: 2px; }"
            f"QProgressBar#miniProgressBar::chunk {{ background-color: {t.sponge_ochre}; "
            "border-radius: 2px; }}"
        )
        self._abort_btn.setStyleSheet(
            f"QPushButton#miniAbortBtn {{ background-color: {t.danger_soft}; "
            f"color: {t.danger}; border: 1px solid {t.danger_border}; "
            "border-radius: 11px; padding: 4px 11px; font-size: 11px; font-weight: 700; }"
            f"QPushButton#miniAbortBtn:hover {{ background-color: {t.danger}; color: #FFFFFF; }}"
        )
