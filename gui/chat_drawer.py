"""
> [!NOTE]
> Модуль gui.chat_drawer: Выдвижная панель истории диалога и действий GupkaCommander.
> Предоставляет карточки сообщений с временными метками, иконками ролей и статусными пиллами,
> а также плавную анимацию раскрытия/складывания через QPropertyAnimation.
"""

from datetime import datetime

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QTimer,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui.styles import PALETTE


class MessageCard(QFrame):
    """
    Карточка отдельного сообщения в истории диалога.

    Содержит заголовок с ролью, меткой времени и статусным бейджем,
    а также многострочный текст сообщения с поддержкой выделения.
    """

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
            role: Роль отправителя ('user', 'assistant', 'action', 'system').
            text: Содержимое сообщения.
            status: Статус выполнения ('done', 'running', 'error', 'pending').
            parent: Родительский виджет.
        """
        super().__init__(parent)
        self._role = role.lower().strip()
        self._status = status.lower().strip()
        self._timestamp = datetime.now().strftime("%H:%M:%S")

        self._init_ui(text)
        self._apply_styling()

    def _init_ui(self, text: str) -> None:
        """Построение элементов интерфейса карточки."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 8, 10, 8)
        main_layout.setSpacing(4)

        # Верхняя строка: Роль + Время + Статус
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        self._role_label = QLabel(self._get_role_display(), self)
        self._role_label.setStyleSheet("font-weight: 600; font-size: 11px;")

        self._time_label = QLabel(self._timestamp, self)
        self._time_label.setStyleSheet(f"color: {PALETTE.secondary}; font-size: 11px;")

        self._status_pill = QLabel(self._status.upper(), self)
        self._update_status_pill_style()

        header_layout.addWidget(self._role_label)
        header_layout.addWidget(self._time_label)
        header_layout.addStretch()
        header_layout.addWidget(self._status_pill)
        main_layout.addLayout(header_layout)

        # Текст сообщения
        self._content_label = QLabel(text, self)
        self._content_label.setWordWrap(True)
        self._content_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._content_label.setStyleSheet(
            f"color: {PALETTE.text}; font-size: 13px; line-height: 1.4;"
        )
        main_layout.addWidget(self._content_label)

    def _get_role_display(self) -> str:
        """Возвращает форматированное имя роли с пиктограммой."""
        if self._role == "user":
            return "👤 Вы"
        if self._role == "assistant":
            return "🤖 Gupka"
        if self._role == "action":
            return "⚡ Действие"
        return "ℹ️ Система"

    def _apply_styling(self) -> None:
        """Применяет визуальные стили карточки с цветовой полосой слева."""
        if self._role == "user":
            accent_border = PALETTE.accent
        elif self._role == "assistant":
            accent_border = PALETTE.success
        elif self._role == "action":
            accent_border = PALETTE.warning
        else:
            accent_border = PALETTE.border_subtle

        self.setStyleSheet(
            f"MessageCard {{"
            f"  background-color: {PALETTE.surface};"
            f"  border: 1px solid {PALETTE.border};"
            f"  border-left: 3px solid {accent_border};"
            f"  border-radius: 8px;"
            f"}}"
        )

    def _update_status_pill_style(self) -> None:
        """Обновляет внешний вид пилла статуса."""
        status = self._status.lower().strip()
        if status == "done":
            color = PALETTE.success
            bg = "rgba(48, 209, 88, 0.15)"
        elif status == "running":
            color = PALETTE.accent
            bg = "rgba(96, 205, 255, 0.15)"
        elif status in ("error", "failed"):
            color = PALETTE.error
            bg = "rgba(255, 69, 58, 0.15)"
        else:
            color = PALETTE.secondary
            bg = "rgba(160, 160, 160, 0.15)"

        self._status_pill.setText(status.upper())
        self._status_pill.setStyleSheet(
            f"background-color: {bg};"
            f"color: {color};"
            f"border: 1px solid {color};"
            f"border-radius: 8px;"
            f"padding: 1px 6px;"
            f"font-size: 10px;"
            f"font-weight: 600;"
        )

    def set_status(self, status: str) -> None:
        """
        Обновляет статус выполнения карточки.

        Args:
            status: Новый статус ('done', 'running', 'error', 'pending').
        """
        self._status = status
        self._update_status_pill_style()

    def set_text(self, text: str) -> None:
        """
        Обновляет текст сообщения карточки.

        Args:
            text: Новый текст.
        """
        self._content_label.setText(text)

    def get_text(self) -> str:
        """Возвращает текст сообщения."""
        return self._content_label.text()

    def get_role(self) -> str:
        """Возвращает роль сообщения."""
        return self._role

    def get_status(self) -> str:
        """Возвращает статус сообщения."""
        return self._status


class ChatDrawer(QWidget):
    """
    Выдвижная панель истории запросов и ответов с плавной анимацией.

    Поддерживает:
    - Добавление сообщений с автоматической прокруткой вниз.
    - Плавное раскрытие/складывание (QPropertyAnimation по maximumHeight).
    - Очистку истории.
    """

    DEFAULT_EXPANDED_HEIGHT = 320

    def __init__(
        self,
        parent: QWidget | None = None,
        target_height: int = DEFAULT_EXPANDED_HEIGHT,
    ) -> None:
        """
        Инициализация выдвижной панели.

        Args:
            parent: Родительский виджет.
            target_height: Высота панели в раскрытом состоянии в пикселях.
        """
        super().__init__(parent)
        self._target_height = target_height
        self._expanded: bool = False
        self._cards: list[MessageCard] = []

        self._init_ui()
        self._init_animation()

        # Изначально панель полностью сложена
        self.setMaximumHeight(0)

    def _init_ui(self) -> None:
        """Инициализация внутренней разметки и области прокрутки."""
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Область прокрутки карточек
        self._scroll_area = QScrollArea(self)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # Контейнер для карточек внутри scroll area
        self._container = QWidget()
        self._container.setStyleSheet(f"background-color: {PALETTE.background};")
        self._card_layout = QVBoxLayout(self._container)
        self._card_layout.setContentsMargins(6, 6, 6, 6)
        self._card_layout.setSpacing(8)
        self._card_layout.addStretch()

        self._scroll_area.setWidget(self._container)
        root_layout.addWidget(self._scroll_area)

    def _init_animation(self) -> None:
        """Настройка анимации изменения высоты."""
        self._animation = QPropertyAnimation(self, b"maximumHeight")
        self._animation.setDuration(220)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def add_message(self, role: str, text: str, status: str = "done") -> MessageCard:
        """
        Добавляет новое сообщение в историю диалога.

        Args:
            role: Роль отправителя ('user', 'assistant', 'action', 'system').
            text: Текст сообщения.
            status: Статус карточки ('done', 'running', 'error', 'pending').

        Returns:
            MessageCard: Созданный экземпляр карточки.
        """
        card = MessageCard(role, text, status, self._container)
        self._cards.append(card)

        # Вставляем перед последним элементом (stretch)
        insert_idx = max(0, self._card_layout.count() - 1)
        self._card_layout.insertWidget(insert_idx, card)

        # Отложенный скролл к последнему элементу
        QTimer.singleShot(15, self._scroll_to_bottom)
        return card

    def _scroll_to_bottom(self) -> None:
        """Прокручивает вертикальную полосу прокрутки в самый низ."""
        v_bar = self._scroll_area.verticalScrollBar()
        if v_bar is not None:
            v_bar.setValue(v_bar.maximum())

    def clear(self) -> None:
        """Очищает всю историю сообщений."""
        for card in self._cards:
            self._card_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

    def toggle(self, expand: bool | None = None) -> None:
        """
        Плавно раскрывает или сворачивает панель истории.

        Args:
            expand: True для раскрытия, False для сворачивания, None для инверсии.
        """
        target = not self._expanded if expand is None else bool(expand)

        if target == self._expanded and not self._animation.state():
            return

        self._expanded = target
        end_height = self._target_height if self._expanded else 0

        self._animation.stop()
        self._animation.setStartValue(self.maximumHeight())
        self._animation.setEndValue(end_height)
        self._animation.start()

    def is_expanded(self) -> bool:
        """
        Возвращает признак раскрытия панели.

        Returns:
            bool: True если панель раскрыта, иначе False.
        """
        return self._expanded

    def get_messages_count(self) -> int:
        """Возвращает текущее количество карточек в истории."""
        return len(self._cards)
