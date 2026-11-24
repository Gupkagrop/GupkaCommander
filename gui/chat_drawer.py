"""
> [!NOTE]
> Модуль gui.chat_drawer: Выдвижная панель истории команд, ответов ассистента и
> карточек шагов ReAct (ChatDrawer). Поддерживает безопасный рендер Markdown в
> стилизованный HTML (код-блоки, inline-код, жирный, курсив, списки), адаптацию
> к темам Windows 11 Fluent 2 (Dark / Light) и надёжную автопрокрутку без обрезки.
"""

from __future__ import annotations

import html
import re
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

from gui.agent_widgets import StepCard
from gui.theme_manager import DARK_TOKENS, ThemeTokens

_ROLE_TITLES: dict[str, str] = {
    "user": "Вы",
    "assistant": "Губка",
    "action": "Действие",
}

_STATUS_BADGE_TEXTS: dict[str, str] = {
    "running": "Выполняется...",
    "error": "Ошибка",
}

_FENCE_RE = re.compile(r"```([a-zA-Z0-9_+#.-]*)[ \t]*\r?\n?(.*?)```", re.DOTALL)
_UNCLOSED_FENCE_RE = re.compile(r"```([a-zA-Z0-9_+#.-]*)[ \t]*\r?\n?(.*)$", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")
_UL_RE = re.compile(r"^\s*[-*•]\s+(.+)$")
_OL_RE = re.compile(r"^\s*(\d+)\.\s+(.+)$")


def _code_palette(tokens: ThemeTokens) -> tuple[str, str, str, str, str, str]:
    """Возвращает цвета для блоков кода и inline-пилюль под текущую тему."""
    if tokens.is_dark:
        return (
            "#12151C",  # block_bg
            "#2B313E",  # block_border
            "#E6EDF3",  # block_fg
            "#8B949E",  # lang_fg
            "#2A2F3B",  # inline_bg
            "#F6CE7A",  # inline_fg
        )
    return (
        "#EDEBE4",  # block_bg
        "#D5D1C5",  # block_border
        "#1B1E24",  # block_fg
        "#5D6472",  # lang_fg
        "#E5E2D9",  # inline_bg
        "#8C5A08",  # inline_fg
    )


def _render_inline_markdown(escaped_line: str, tokens: ThemeTokens) -> str:
    """Преобразует inline-код, **жирный** и *курсив* в уже экранированной строке."""
    _, _, _, _, inline_bg, inline_fg = _code_palette(tokens)
    placeholders: list[str] = []

    def _stash_code(match: re.Match[str]) -> str:
        snippet = match.group(1)
        pill = (
            "<span style=\"font-family: 'Cascadia Code', 'JetBrains Mono', 'Consolas', monospace; "
            f'font-size: 12px; background-color: {inline_bg}; color: {inline_fg};">'
            f"&nbsp;{snippet}&nbsp;</span>"
        )
        placeholders.append(pill)
        return f"\x00CODE{len(placeholders) - 1}\x00"

    text = _INLINE_CODE_RE.sub(_stash_code, escaped_line)
    text = _BOLD_RE.sub(
        lambda m: f'<b style="font-weight: 600;">{m.group(1) or m.group(2)}</b>',
        text,
    )
    text = _ITALIC_RE.sub(r"<i>\1</i>", text)

    for idx, pill in enumerate(placeholders):
        text = text.replace(f"\x00CODE{idx}\x00", pill)
    return text


def _render_code_block(lang: str, raw_code: str, tokens: ThemeTokens) -> str:
    """Рендерит fenced-блок кода в стилизованную HTML-таблицу для Qt RichText."""
    block_bg, block_border, block_fg, lang_fg, _, _ = _code_palette(tokens)
    cleaned = raw_code.strip("\r\n")
    escaped_code = html.escape(cleaned).replace("\r\n", "\n").replace("\r", "\n")
    # Сохраняем ведущие пробелы и переводы строк для идеального отступа в QLabel RichText
    code_lines = [line.replace("  ", "&nbsp;&nbsp;") for line in escaped_code.split("\n")]
    code_html = "<br/>".join(code_lines)

    header_html = ""
    if lang.strip():
        safe_lang = html.escape(lang.strip().lower())
        header_html = (
            f'<div style="font-size: 10px; font-weight: 600; color: {lang_fg}; '
            f'margin-bottom: 4px; letter-spacing: 0.5px;">{safe_lang}</div>'
        )

    return (
        f'<table width="100%" cellspacing="0" cellpadding="0" '
        f'style="margin-top: 6px; margin-bottom: 6px; background-color: {block_bg}; '
        f'border: 1px solid {block_border};">'
        f'<tr><td style="padding: 8px 10px;">'
        f"{header_html}"
        f"<div style=\"font-family: 'Cascadia Code', 'JetBrains Mono', 'Consolas', monospace; "
        f'font-size: 12px; color: {block_fg}; line-height: 138%;">{code_html}</div>'
        f"</td></tr></table>"
    )


def _render_text_segment(segment: str, tokens: ThemeTokens) -> str:
    """Рендерит обычный Markdown-фрагмент (заголовки, списки, абзацы, inline)."""
    normalized = segment.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    out: list[str] = []

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            # Сжимаем подряд идущие пустые строки в один компактный абзацный отступ
            if out and out[-1] != '<div style="font-size: 5px;">&nbsp;</div>':
                out.append('<div style="font-size: 5px;">&nbsp;</div>')
            continue

        escaped = html.escape(raw_line)
        heading = _HEADING_RE.match(escaped)
        if heading:
            level = len(heading.group(1))
            size_px = 14.5 if level == 1 else (14.0 if level == 2 else 13.5)
            body = _render_inline_markdown(heading.group(2), tokens)
            out.append(
                f'<div style="font-size: {size_px}px; font-weight: 600; '
                f'margin-top: 4px; margin-bottom: 2px;">{body}</div>'
            )
            continue

        ol_match = _OL_RE.match(escaped)
        if ol_match:
            num = ol_match.group(1)
            body = _render_inline_markdown(ol_match.group(2), tokens)
            out.append(
                f'<div style="margin-left: 4px; margin-top: 2px; margin-bottom: 2px;">'
                f'<span style="color: {tokens.sponge_ochre}; font-weight: 600;">{num}.</span> '
                f"{body}</div>"
            )
            continue

        ul_match = _UL_RE.match(escaped)
        if ul_match:
            body = _render_inline_markdown(ul_match.group(1), tokens)
            out.append(
                f'<div style="margin-left: 6px; margin-top: 2px; margin-bottom: 2px;">'
                f'<span style="color: {tokens.sponge_ochre}; font-weight: 700;">•</span> '
                f"{body}</div>"
            )
            continue

        inline = _render_inline_markdown(escaped, tokens)
        out.append(f"<div>{inline}</div>")

    return "".join(out)


def render_markdown_to_html(text: str, tokens: ThemeTokens = DARK_TOKENS) -> str:
    """
    Безопасно преобразует Markdown-текст в стилизованный HTML для Qt RichText.

    Выполняет обязательное HTML-экранирование всего пользовательского текста,
    оформляет блоки кода `` ```...``` ``, inline-пилюли `` `code` ``,
    ``**жирный**``, ``*курсив*``, списки и заголовки под палитру ThemeTokens.
    """
    if not text:
        return ""

    parts: list[str] = []
    pos = 0
    for match in _FENCE_RE.finditer(text):
        before = text[pos : match.start()]
        if before.strip():
            parts.append(_render_text_segment(before, tokens))
        parts.append(_render_code_block(match.group(1), match.group(2), tokens))
        pos = match.end()

    tail = text[pos:]
    unclosed = _UNCLOSED_FENCE_RE.search(tail)
    if unclosed:
        before = tail[: unclosed.start()]
        if before.strip():
            parts.append(_render_text_segment(before, tokens))
        parts.append(_render_code_block(unclosed.group(1), unclosed.group(2), tokens))
    elif tail:
        parts.append(_render_text_segment(tail, tokens))

    return f'<div style="line-height: 142%; color: {tokens.text};">{"".join(parts)}</div>'


def _role_title_color(role: str, tokens: ThemeTokens) -> str:
    """Возвращает цвет заголовка роли в карточке сообщения."""
    if role == "user":
        return tokens.accent
    if role == "assistant":
        return tokens.sponge_ochre
    if role == "action":
        return tokens.sponge_ochre
    return tokens.text_secondary


def _role_dot_color(role: str, status: str, tokens: ThemeTokens) -> str:
    """Возвращает цвет индикаторной точки роли с учётом статуса карточки."""
    if role == "assistant":
        if status == "error":
            return tokens.danger
        if status == "done":
            return tokens.success
        return tokens.sponge_ochre
    return _role_title_color(role, tokens)


def _card_frame_qss(role: str, tokens: ThemeTokens) -> str:
    """Формирует QSS-оформление фона и границы MessageCard с учётом темы и роли."""
    if role == "user":
        bg = "rgba(96, 205, 255, 0.13)" if tokens.is_dark else "rgba(0, 103, 192, 0.09)"
        border = "rgba(96, 205, 255, 0.28)" if tokens.is_dark else "rgba(0, 103, 192, 0.24)"
        return (
            f"QFrame#messageCard {{ background-color: {bg}; border: 1px solid {border}; "
            "border-top-left-radius: 14px; border-top-right-radius: 14px; "
            "border-bottom-left-radius: 14px; border-bottom-right-radius: 5px; }"
        )
    if role == "action":
        bg, border, radius = tokens.sponge_ochre_soft, tokens.sponge_ochre_border, 12
    else:
        bg = "rgba(33, 36, 44, 0.86)" if tokens.is_dark else "rgba(255, 255, 253, 0.90)"
        border = "rgba(255, 255, 255, 0.07)" if tokens.is_dark else "rgba(24, 28, 36, 0.08)"
        radius = 14

    return (
        f"QFrame#messageCard {{ background-color: {bg}; "
        f"border: 1px solid {border}; border-radius: {radius}px; }}"
    )


class MessageCard(QFrame):
    """Стилизованная стеклянная карточка отдельного сообщения с Markdown-рендером."""

    content_resized = Signal()

    def __init__(
        self,
        role: str,
        text: str,
        status: str = "done",
        parent: QWidget | None = None,
        *,
        tokens: ThemeTokens = DARK_TOKENS,
    ) -> None:
        """
        Инициализация карточки сообщения.

        Args:
            role: Роль отправителя ('user', 'assistant' или 'action').
            text: Исходный plain-text / Markdown содержимого сообщения.
            status: Статус выполнения ('done', 'running', 'error').
            parent: Родительский виджет.
            tokens: Активный набор токенов темы Fluent 2.
        """
        super().__init__(parent)
        self._role: str = role.strip().lower()
        self._text: str = text
        self._status: str = status.strip().lower()
        self._tokens: ThemeTokens = tokens

        self.setObjectName("messageCard")
        self.setProperty("role", self._role)
        if self._role == "user":
            self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
            self.setMaximumWidth(520)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self._init_ui()
        self.apply_theme(self._tokens)

    def _init_ui(self) -> None:
        """Построение внутренней разметки карточки с поддержкой RichText."""
        layout = QVBoxLayout(self)
        if self._role == "user":
            layout.setContentsMargins(13, 8, 13, 9)
            layout.setSpacing(4)
        else:
            layout.setContentsMargins(14, 10, 14, 12)
            layout.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(7)

        self._role_dot = QFrame(self)
        self._role_dot.setFixedSize(7, 7)
        header.addWidget(self._role_dot)

        role_title = _ROLE_TITLES.get(self._role, self._role.capitalize())
        self._role_label = QLabel(role_title, self)
        self._role_label.setObjectName("cardRoleLabel")
        header.addWidget(self._role_label)

        header.addStretch(1)

        self._status_label = QLabel(self)
        self._status_label.setObjectName("cardStatusBadge")
        header.addWidget(self._status_label)

        layout.addLayout(header)

        self._text_label = QLabel(self)
        self._text_label.setObjectName("cardTextLabel")
        self._text_label.setTextFormat(Qt.TextFormat.RichText)
        self._text_label.setWordWrap(True)
        self._text_label.setOpenExternalLinks(False)
        self._text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._text_label.setSizePolicy(
            QSizePolicy.Policy.Preferred if self._role == "user" else QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        layout.addWidget(self._text_label)

    def _sync_rendered_text(self) -> None:
        """Перерендеривает исходный текст Markdown в стилизованный HTML."""
        self._text_label.setText(render_markdown_to_html(self._text, self._tokens))
        self._text_label.updateGeometry()
        self.updateGeometry()

    def apply_theme(self, tokens: ThemeTokens) -> None:
        """Применяет дизайн-токены Dark/Light к карточке и перерендеривает Markdown."""
        self._tokens = tokens
        title_color = _role_title_color(self._role, tokens)

        self.setStyleSheet(_card_frame_qss(self._role, tokens))
        self._role_label.setStyleSheet(
            f"background: transparent; border: none; color: {title_color}; "
            "font-size: 11px; font-weight: 600;"
        )
        self._text_label.setStyleSheet(
            f"background: transparent; border: none; color: {tokens.text}; "
            "font-size: 13.5px; font-weight: 400;"
        )
        self._apply_status_style()
        self._sync_rendered_text()

    def _apply_status_style(self) -> None:
        """Обновляет цвет точки роли, видимость и стиль бейджа статуса."""
        t = self._tokens
        dot_color = _role_dot_color(self._role, self._status, t)
        self._role_dot.setStyleSheet(
            f"background-color: {dot_color}; border-radius: 3px; border: none;"
        )

        badge_text = _STATUS_BADGE_TEXTS.get(self._status)
        if not badge_text:
            self._status_label.setText("")
            self._status_label.setVisible(False)
            return

        self._status_label.setVisible(True)
        self._status_label.setText(badge_text)
        if self._status == "running":
            self._status_label.setStyleSheet(
                f"color: {t.sponge_ochre}; background-color: {t.sponge_ochre_soft}; "
                f"border: 1px solid {t.sponge_ochre_border}; border-radius: 9px; "
                "padding: 2px 8px; font-size: 10px; font-weight: 600;"
            )
            return

        if self._status == "error":
            self._status_label.setStyleSheet(
                f"color: {t.danger}; background-color: {t.danger_soft}; "
                f"border: 1px solid {t.danger_border}; border-radius: 9px; "
                "padding: 2px 8px; font-size: 10px; font-weight: 600;"
            )

    def get_role(self) -> str:
        """Возвращает роль отправителя сообщения."""
        return self._role

    def get_text(self) -> str:
        """Возвращает исходный plain-text сообщения (без HTML-обёртки)."""
        return self._text

    def get_status(self) -> str:
        """Возвращает текущий статус карточки."""
        return self._status

    def set_text(self, text: str) -> None:
        """
        Обновляет отображаемый текст карточки (используется при потоковом выводе LLM).

        Args:
            text: Новый текст сообщения в формате plain-text / Markdown.
        """
        self._text = text
        self._sync_rendered_text()
        self.content_resized.emit()

    def set_status(self, status: str) -> None:
        """
        Обновляет статус карточки ('done', 'running', 'error').

        Args:
            status: Новый статус выполнения.
        """
        self._status = status.strip().lower()
        self._apply_status_style()


class ChatDrawer(QWidget):
    """Выдвижная панель истории диалога и живых шагов ReAct с плавной анимацией."""

    DEFAULT_TARGET_HEIGHT: int = 360
    expanded_changed = Signal(bool)

    def __init__(
        self,
        target_height: int = DEFAULT_TARGET_HEIGHT,
        parent: QWidget | None = None,
        *,
        tokens: ThemeTokens = DARK_TOKENS,
    ) -> None:
        """
        Инициализация выдвижной панели ChatDrawer.

        Args:
            target_height: Целевая высота панели в раскрытом состоянии.
            parent: Родительский виджет.
            tokens: Активный набор дизайн-токенов Fluent 2.
        """
        super().__init__(parent)
        self._target_height: int = max(120, target_height)
        self._expanded: bool = False
        self._tokens: ThemeTokens = tokens
        self._cards: list[MessageCard] = []
        self._step_cards: list[StepCard] = []
        self._rows: list[QWidget] = []
        self._auto_scroll_pending: bool = False

        self.setObjectName("chatDrawer")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMaximumHeight(0)

        self._animation = QPropertyAnimation(self, b"maximumHeight", self)
        self._animation.setDuration(220)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.finished.connect(self._on_animation_finished)

        self._init_ui()
        self.apply_theme(self._tokens)

    @property
    def step_cards(self) -> list[StepCard]:
        """Возвращает список добавленных карточек шагов ReAct."""
        return list(self._step_cards)

    def _init_ui(self) -> None:
        """Создание области прокрутки и контейнера карточек с нижним буфером 16px."""
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._scroll_area = QScrollArea(self)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        self._container = QWidget(self._scroll_area)
        self._container.setObjectName("chatDrawerViewport")
        self._cards_layout = QVBoxLayout(self._container)
        self._cards_layout.setContentsMargins(14, 10, 16, 16)
        self._cards_layout.setSpacing(10)
        self._cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._scroll_area.setWidget(self._container)
        vbar = self._scroll_area.verticalScrollBar()
        if vbar is not None:
            vbar.rangeChanged.connect(self._on_scroll_range_changed)
        root_layout.addWidget(self._scroll_area)

    def apply_theme(self, tokens: ThemeTokens) -> None:
        """Обновляет оформление панели, скроллбара, MessageCard и StepCard под тему ОС."""
        self._tokens = tokens
        self._scroll_area.setStyleSheet(
            "QScrollArea, QWidget#chatDrawerViewport {"
            "  background-color: transparent; border: none;"
            "}"
            "QScrollBar:vertical {"
            "  background: transparent; width: 5px; margin: 6px 2px 6px 0px;"
            "}"
            "QScrollBar::handle:vertical {"
            f"  background: {tokens.scrollbar_handle}; border-radius: 2px; min-height: 24px;"
            "}"
            "QScrollBar::handle:vertical:hover {"
            f"  background: {tokens.sponge_ochre_border};"
            "}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {"
            "  background: transparent; height: 0px;"
            "}"
        )
        for card in self._cards:
            card.apply_theme(tokens)
        for step_card in self._step_cards:
            step_card.set_tokens(tokens)

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

    def _schedule_scroll_to_bottom(self, delay_ms: int = 25) -> None:
        """Планирует надёжную отложенную прокрутку к нижней границе истории."""
        self._auto_scroll_pending = True
        QTimer.singleShot(delay_ms, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        """Прокручивает область истории строго до конца с учётом нижнего отступа."""
        self._cards_layout.activate()
        self._container.updateGeometry()
        bar = self._scroll_area.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())
        self._auto_scroll_pending = False

    def _on_scroll_range_changed(self, _min_val: int, max_val: int) -> None:
        """Удерживает ленту внизу при росте RichText-карточек во время стриминга."""
        if not self._auto_scroll_pending:
            return
        bar = self._scroll_area.verticalScrollBar()
        if bar is not None:
            bar.setValue(max_val)

    def _on_animation_finished(self) -> None:
        """После завершения анимации раскрытия гарантированно докручивает до конца."""
        if self._expanded:
            self._schedule_scroll_to_bottom(25)

    def _insert_row_widget(self, row_widget: QWidget) -> None:
        """Добавляет строку с карточкой в конец ленты и планирует автопрокрутку."""
        self._cards_layout.addWidget(row_widget)
        self._rows.append(row_widget)
        self._schedule_scroll_to_bottom(25)

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
        card = MessageCard(
            role=role,
            text=text,
            status=status,
            parent=self._container,
            tokens=self._tokens,
        )
        card.content_resized.connect(lambda: self._schedule_scroll_to_bottom(25))

        row_widget = QWidget(self._container)
        row_widget.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row_widget)
        row_layout.setSpacing(0)

        norm_role = role.strip().lower()
        if norm_role == "user":
            row_layout.setContentsMargins(64, 0, 0, 0)
            row_layout.addStretch(1)
            row_layout.addWidget(card)
        elif norm_role == "assistant":
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(card)
        else:
            row_layout.setContentsMargins(8, 0, 8, 0)
            row_layout.addWidget(card)

        self._cards.append(card)
        self._insert_row_widget(row_widget)
        return card

    def add_step_card(
        self,
        step_index: int,
        title: str,
        engine: str,
        status: str = "running",
        duration_ms: int = 0,
        detail: str = "",
    ) -> StepCard:
        """
        Добавляет карточку живого шага ReAct (Fast-Path / UIA / CDP / Vision) в ленту.

        Args:
            step_index: Порядковый номер шага.
            title: Краткое описание выполняемого действия.
            engine: Движок выполнения ('Fast-Path', 'UIA', 'CDP', 'Vision').
            status: Статус шага ('running', 'done', 'error').
            duration_ms: Длительность выполнения шага в миллисекундах.
            detail: Сворачиваемая техническая сводка шага.

        Returns:
            Созданный экземпляр StepCard.
        """
        step_card = StepCard(
            step_index=step_index,
            title=title,
            engine=engine,
            status=status,
            duration_ms=duration_ms,
            details=detail,
            tokens=self._tokens,
            parent=self._container,
        )
        row_widget = QWidget(self._container)
        row_widget.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)
        row_layout.addWidget(step_card)

        self._step_cards.append(step_card)
        self._insert_row_widget(row_widget)
        return step_card

    def get_messages_count(self) -> int:
        """Возвращает количество отображаемых карточек сообщений."""
        return len(self._cards)

    def get_step_cards_count(self) -> int:
        """Возвращает количество карточек шагов ReAct в истории."""
        return len(self._step_cards)

    def clear(self) -> None:
        """Удаляет все карточки сообщений и шагов ReAct из истории диалога."""
        for row in self._rows:
            self._cards_layout.removeWidget(row)
            row.deleteLater()
        self._cards.clear()
        self._step_cards.clear()
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
            self._schedule_scroll_to_bottom(25)

        return self._expanded
