"""
> [!NOTE]
> Модуль gui.chat_drawer: Выдвижная панель истории команд и ответов ассистента (ChatDrawer).
> Содержит стилизованные стеклянные карточки сообщений (MessageCard) для ролей
> user, action и assistant с плавной анимацией раскрытия и автоматической прокруткой.
"""

from __future__ import annotations

from typing import override

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gui.styles import PALETTE

_ROLE_TITLES: dict[str, str] = {
    "user": "Вы",
    "assistant": "Губка",
    "action": "Действие",
}

_ROLE_DOT_COLORS: dict[str, str] = {
    "user": PALETTE.accent,
    "assistant": PALETTE.success,
    "action": PALETTE.warning,
}

_STATUS_BADGE_TEXTS: dict[str, str] = {
    "running": "Выполняется...",
    "error": "Ошибка",
}


class MessageCard(QFrame):
    """Стилизованная стеклянная карточка отдельного сообщения в истории диалога."""

    def __init__(
        self,
        role: str,
        text: str,
        status: str = "done",
        parent: QWidget | None = None,
    ) -> None:
        """
        Инициализация карточки сообщения.

        Args:
            role: Роль отправителя ('user', 'assistant' или 'action').
            text: Содержимое сообщения.
            status: Статус выполнения ('done', 'running', 'error').
            parent: Родительский виджет.
        """
        super().__init__(parent)
        self._role: str = role.strip().lower()
        self._text: str = text
        self._status: str = status.strip().lower()

        self.setObjectName("messageCard")
        self.setProperty("role", self._role)

        self._init_ui()
        self._apply_status_style()

    def _init_ui(self) -> None:
        """Построение внутренней разметки карточки без громоздких эмодзи."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 11)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(7)

        dot_color = _ROLE_DOT_COLORS.get(self._role, PALETTE.secondary)
        self._role_dot = QFrame(self)
        self._role_dot.setFixedSize(7, 7)
        self._role_dot.setStyleSheet(
            f"background-color: {dot_color}; border-radius: 3px; border: none;"
        )
        header.addWidget(self._role_dot)

        role_title = _ROLE_TITLES.get(self._role, self._role.capitalize())
        self._role_label = QLabel(role_title, self)
        self._role_label.setObjectName("cardRoleLabel")
        self._role_label.setStyleSheet(f"color: {dot_color}; font-size: 11px; font-weight: 600;")
        header.addWidget(self._role_label)

        header.addStretch(1)

        self._status_label = QLabel(self)
        self._status_label.setObjectName("cardStatusBadge")
        header.addWidget(self._status_label)

        layout.addLayout(header)

        self._text_label = QLabel(self._text, self)
        self._text_label.setObjectName("cardTextLabel")
        self._text_label.setWordWrap(True)
        self._text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._text_label)

    def _apply_status_style(self) -> None:
        """Обновляет видимость и стиль индикатора статуса (скрыт в обычном состоянии done)."""
        badge_text = _STATUS_BADGE_TEXTS.get(self._status)
        if not badge_text:
            self._status_label.setText("")
            self._status_label.setVisible(False)
            return

        self._status_label.setVisible(True)
        self._status_label.setText(badge_text)
        if self._status == "running":
            self._status_label.setStyleSheet(
                f"color: {PALETTE.accent}; background-color: rgba(96, 205, 255, 0.16); "
                "border: 1px solid rgba(96, 205, 255, 0.35); border-radius: 8px; padding: 2px 8px;"
            )
            return

        if self._status == "error":
            self._status_label.setStyleSheet(
                f"color: {PALETTE.error}; background-color: rgba(255, 69, 58, 0.16); "
                "border: 1px solid rgba(255, 69, 58, 0.35); border-radius: 8px; padding: 2px 8px;"
            )

    def get_role(self) -> str:
        """Возвращает роль отправителя сообщения."""
        return self._role

    def get_text(self) -> str:
        """Возвращает текущий текст сообщения."""
        return self._text

    def get_status(self) -> str:
        """Возвращает текущий статус карточки."""
        return self._status

    def set_text(self, text: str) -> None:
        """
        Обновляет отображаемый текст карточки (используется при потоковом выводе LLM).

        Args:
            text: Новый текст сообщения.
        """
        self._text = text
        self._text_label.setText(text)

    def set_status(self, status: str) -> None:
        """
        Обновляет статус карточки ('done', 'running', 'error').

        Args:
            status: Новый статус выполнения.
        """
        self._status = status.strip().lower()
        self._apply_status_style()


class ChatDrawer(QWidget):
    """Выдвижная панель истории диалога с плавной анимацией раскрытия."""

    DEFAULT_TARGET_HEIGHT: int = 340
    expanded_changed = Signal(bool)

    def __init__(
        self,
        target_height: int = DEFAULT_TARGET_HEIGHT,
        parent: QWidget | None = None,
    ) -> None:
        """
        Инициализация выдвижной панели ChatDrawer.

        Args:
            target_height: Целевая высота панели в раскрытом состоянии.
            parent: Родительский виджет.
        """
        super().__init__(parent)
        self._target_height: int = max(120, target_height)
        self._expanded: bool = False
        self._cards: list[MessageCard] = []
        self._rows: list[QWidget] = []

        self.setObjectName("chatDrawer")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMaximumHeight(0)

        self._animation = QPropertyAnimation(self, b"maximumHeight", self)
        self._animation.setDuration(220)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._init_ui()

    def _init_ui(self) -> None:
        """Создание области прокрутки и контейнера карточек."""
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._scroll_area = QScrollArea(self)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        self._container = QWidget(self._scroll_area)
        self._cards_layout = QVBoxLayout(self._container)
        self._cards_layout.setContentsMargins(14, 10, 14, 12)
        self._cards_layout.setSpacing(8)
        self._cards_layout.addStretch(1)

        self._scroll_area.setWidget(self._container)
        root_layout.addWidget(self._scroll_area)

    @override
    def sizeHint(self) -> QSize:
        """Возвращает размер панели с учетом текущей высоты анимации раскрытия."""
        base = super().sizeHint()
        if not self._expanded and self.maximumHeight() == 0:
            return QSize(base.width(), 0)
        return QSize(base.width(), min(self._target_height, self.maximumHeight()))

    @override
    def minimumSizeHint(self) -> QSize:
        """Минимальный размер панели следует текущей высоте раскрытия."""
        return self.sizeHint()

    def _scroll_to_bottom(self) -> None:
        """Плавно прокручивает область истории к последнему сообщению."""
        bar = self._scroll_area.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())

    def add_message(self, role: str, text: str, status: str = "done") -> MessageCard:
        """
        Добавляет новую карточку сообщения в историю чата.

        Args:
            role: Роль ('user', 'assistant', 'action').
            text: Содержимое сообщения.
            status: Начальный статус ('done', 'running', 'error').

        Returns:
            Созданный экземпляр MessageCard.
        """
        card = MessageCard(role=role, text=text, status=status, parent=self._container)
        row_widget = QWidget(self._container)
        row_layout = QHBoxLayout(row_widget)
        row_layout.setSpacing(0)

        norm_role = role.strip().lower()
        if norm_role == "user":
            # Пузыри пользователя немного смещены вправо для визуального диалога
            row_layout.setContentsMargins(52, 0, 0, 0)
        elif norm_role == "assistant":
            row_layout.setContentsMargins(0, 0, 28, 0)
        else:
            row_layout.setContentsMargins(12, 0, 12, 0)

        row_layout.addWidget(card)

        insert_index = max(0, self._cards_layout.count() - 1)
        self._cards_layout.insertWidget(insert_index, row_widget)
        self._cards.append(card)
        self._rows.append(row_widget)

        QTimer.singleShot(15, self._scroll_to_bottom)
        return card

    def get_messages_count(self) -> int:
        """Возвращает количество отображаемых карточек сообщений."""
        return len(self._cards)

    def clear(self) -> None:
        """Удаляет все карточки сообщений из истории диалога."""
        for row in self._rows:
            self._cards_layout.removeWidget(row)
            row.deleteLater()
        self._cards.clear()
        self._rows.clear()

    def is_expanded(self) -> bool:
        """Проверяет, находится ли панель истории в раскрытом состоянии."""
        return self._expanded

    def toggle(self, expand: bool | None = None) -> bool:
        """
        Переключает или задает состояние раскрытия панели ChatDrawer.

        Args:
            expand: Целевое состояние (True/False) или None для переключения.

        Returns:
            Итоговое состояние раскрытия.
        """
        target_state = (not self._expanded) if expand is None else bool(expand)
        if target_state == self._expanded:
            return self._expanded

        self._expanded = target_state
        start_val = self.maximumHeight()
        end_val = self._target_height if self._expanded else 0

        self._animation.stop()
        self._animation.setStartValue(start_val)
        self._animation.setEndValue(end_val)
        self._animation.start()
        self.expanded_changed.emit(self._expanded)

        if self._expanded:
            QTimer.singleShot(60, self._scroll_to_bottom)

        return self._expanded
