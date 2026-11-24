"""Диалог первичной настройки и загрузки зависимостей GupkaCommander.

> [!NOTE]
> **Назначение файла:** Графическое окно Windows 11 Fluent 2 (PySide6) для запроса
> разрешения пользователя на скачивание локальных моделей (Silero VAD, Piper TTS,
> Faster-Whisper, GigaAM v3 ONNX, Qwen3-TTS, Qwen3.5-4B) и сервера llama-server.exe
> при первом запуске .exe или из меню трея.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import override

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.bootstrap import (
    BootstrapReport,
    DeleteResult,
    DependencyItem,
    DependencyManager,
    DownloadProgress,
    DownloadResult,
    format_size_ru,
)
from core.paths import get_icon_path
from gui.theme_manager import ThemeManager, ThemeMode, ThemeTokens

logger = logging.getLogger(__name__)


def _build_setup_stylesheet(tokens: ThemeTokens) -> str:
    """Формирует QSS для диалога загрузки зависимостей в стиле Fluent 2 + Sponge Ochre."""
    return f"""
QDialog#setupDialog {{
    background-color: {tokens.background};
    color: {tokens.text};
    font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Inter', sans-serif;
}}
QLabel {{
    background: transparent;
    border: none;
    color: {tokens.text};
}}
QScrollArea#depsScrollArea,
QWidget#depsScrollContainer {{
    background: transparent;
    border: none;
}}
QFrame#depCard {{
    background-color: {tokens.surface_card};
    border: 1px solid {tokens.border};
    border-radius: 10px;
}}
QLabel#titleLabel {{
    font-size: 18px;
    font-weight: 600;
    color: {tokens.text};
}}
QLabel#subtitleLabel {{
    font-size: 13px;
    color: {tokens.text_secondary};
}}
QLabel#descLabel {{
    font-size: 12px;
    color: {tokens.text_secondary};
}}
QLabel#statusInstalled {{
    font-size: 12px;
    font-weight: 600;
    color: {tokens.success};
}}
QLabel#sizeBadge {{
    font-size: 12px;
    font-weight: 600;
    color: {tokens.sponge_ochre};
}}
QLabel#warningLabel {{
    font-size: 12px;
    color: {tokens.danger};
}}
QLabel#storageLabel {{
    font-size: 11px;
    color: {tokens.text_muted};
}}
QProgressBar#downloadProgress {{
    background-color: {tokens.surface_chip};
    border: 1px solid {tokens.border};
    border-radius: 5px;
    height: 10px;
    text-align: center;
}}
QProgressBar#downloadProgress::chunk {{
    background-color: {tokens.sponge_ochre};
    border-radius: 4px;
}}
QCheckBox {{
    color: {tokens.text};
    spacing: 8px;
    font-weight: 500;
}}
QPushButton {{
    background-color: {tokens.surface_chip};
    color: {tokens.text};
    border: 1px solid {tokens.border};
    border-radius: 8px;
    padding: 6px 14px;
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {tokens.surface_chip_hover};
}}
QPushButton#accentButton {{
    background-color: {tokens.sponge_ochre_soft};
    color: {tokens.sponge_ochre};
    border: 1px solid {tokens.sponge_ochre_border};
    font-weight: 700;
}}
QPushButton#accentButton:hover {{
    background-color: {tokens.sponge_ochre};
    color: #1B1D22;
}}
QPushButton#cardDeleteBtn,
QPushButton#dangerButton {{
    background-color: {tokens.danger_soft};
    color: {tokens.danger};
    border: 1px solid {tokens.danger_border};
    border-radius: 8px;
    font-weight: 600;
}}
QPushButton#cardDeleteBtn {{
    padding: 3px 10px;
    font-size: 11px;
}}
QPushButton#cardDeleteBtn:hover,
QPushButton#dangerButton:hover {{
    background-color: {tokens.danger};
    color: #FFFFFF;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    margin: 2px 0px 2px 0px;
}}
QScrollBar::handle:vertical {{
    background: {tokens.scrollbar_handle};
    border-radius: 3px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{
    background: transparent;
    height: 0px;
}}
"""


class DependencyDownloadWorker(QThread):
    """Фоновый поток Qt для неблокирующей загрузки зависимостей."""

    progress_updated = Signal(object)
    download_finished = Signal(object)

    def __init__(
        self,
        manager: DependencyManager,
        item_ids: list[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._item_ids = list(item_ids)
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Запрашивает корректную остановку текущей загрузки."""
        self._cancel_event.set()

    def run(self) -> None:
        """Выполняет загрузку выбранных компонентов в фоновом потоке."""
        result = self._manager.download_items(
            item_ids=self._item_ids,
            on_progress=self.progress_updated.emit,
            cancel_event=self._cancel_event,
        )
        self.download_finished.emit(result)


class DependencySetupDialog(QDialog):
    """Окно выбора и загрузки внешних моделей/зависимостей в стиле Fluent Dark."""

    def __init__(
        self,
        manager: DependencyManager | None = None,
        manage_mode: bool = False,
        parent: QWidget | None = None,
        *,
        on_before_delete: Callable[[], None] | None = None,
        on_after_delete: Callable[[], None] | None = None,
        confirm_delete: Callable[[str, str], bool] | None = None,
    ) -> None:
        """Инициализирует диалог первичной настройки зависимостей.

        Args:
            manager: Экземпляр DependencyManager (для внедрения в тестах).
            manage_mode: Если True, окно открыто вручную из трея (скрывает чекбокс «Не показывать»).
            parent: Родительский виджет Qt.
            on_before_delete: Хук освобождения блокировок перед удалением моделей/бинарников.
            on_after_delete: Хук обновления подсистем после удаления зависимостей.
            confirm_delete: Функция подтверждения удаления `(title, message) -> bool`.
        """
        super().__init__(parent)
        self._manager = manager or DependencyManager()
        self._manage_mode = manage_mode
        self._on_before_delete = on_before_delete
        self._on_after_delete = on_after_delete
        self._confirm_delete = confirm_delete
        self._report: BootstrapReport = self._manager.inspect()
        self._checkboxes: dict[str, QCheckBox] = {}
        self._status_labels: dict[str, QLabel] = {}
        self._delete_buttons: dict[str, QPushButton] = {}
        self._worker: DependencyDownloadWorker | None = None
        self._last_result: DownloadResult | None = None
        self._completed_successfully: bool = False

        self.setObjectName("setupDialog")
        self.setWindowTitle("GupkaCommander — Загрузка компонентов")
        self.setMinimumWidth(600)
        self.setModal(True)
        self._tokens: ThemeTokens = ThemeManager.resolve_tokens(ThemeMode.SYSTEM)
        self.setStyleSheet(_build_setup_stylesheet(self._tokens))

        icon_path = get_icon_path()
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._build_ui()
        self._refresh_storage_and_delete_all()
        self._refresh_selection_summary()

    @property
    def last_result(self) -> DownloadResult | None:
        """Результат последней завершенной операции загрузки."""
        return self._last_result

    @property
    def checkboxes(self) -> dict[str, QCheckBox]:
        """Словарь чекбоксов компонентов по их идентификаторам."""
        return self._checkboxes

    @property
    def delete_buttons(self) -> dict[str, QPushButton]:
        """Словарь кнопок удаления отдельных компонентов по их идентификаторам."""
        return self._delete_buttons

    def _build_ui(self) -> None:
        """Формирует компоновку диалогового окна."""
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(12)

        title = QLabel("Настройка локальных компонентов")
        title.setObjectName("titleLabel")
        root.addWidget(title)

        subtitle = QLabel(
            "Для автономной работы без интернета GupkaCommander использует локальные модели. "
            "Разрешите загрузку выбранных компонентов (докачка поддерживается):"
        )
        subtitle.setObjectName("subtitleLabel")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        scroll_area = QScrollArea(self)
        scroll_area.setObjectName("depsScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setMinimumHeight(240)
        scroll_area.setMaximumHeight(420)

        cards_container = QWidget(scroll_area)
        cards_container.setObjectName("depsScrollContainer")
        cards_layout = QVBoxLayout(cards_container)
        cards_layout.setContentsMargins(0, 0, 6, 0)
        cards_layout.setSpacing(8)
        for item in self._report.items:
            cards_layout.addWidget(self._create_item_card(item))
        cards_layout.addStretch(1)

        scroll_area.setWidget(cards_container)
        self._scroll_area = scroll_area
        root.addWidget(scroll_area, stretch=1)

        storage_text = (
            f"Папка моделей: {self._report.models_dir}  •  "
            f"Свободно на диске: {format_size_ru(self._report.free_space_mb)}"
        )
        self._storage_label = QLabel(storage_text)
        self._storage_label.setObjectName("storageLabel")
        self._storage_label.setWordWrap(True)
        root.addWidget(self._storage_label)

        self._warning_label = QLabel("")
        self._warning_label.setObjectName("warningLabel")
        self._warning_label.setWordWrap(True)
        self._warning_label.hide()
        root.addWidget(self._warning_label)

        self._build_progress_panel(root)
        self._build_footer(root)

    def _create_item_card(self, item: DependencyItem) -> QFrame:
        """Создает визуальную карточку одного компонента с чекбоксом и размером."""
        card = QFrame(self)
        card.setObjectName("depCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(4)

        top_row = QHBoxLayout()
        cb = QCheckBox(item.name, card)
        cb.setChecked(item.installed or item.required_for_default)
        cb.setEnabled(not item.installed)
        cb.stateChanged.connect(lambda _state, iid=item.id: self._on_checkbox_changed(iid))
        self._checkboxes[item.id] = cb
        top_row.addWidget(cb, stretch=1)

        badge = QLabel(card)
        if item.installed:
            badge.setText("✓ Установлено")
            badge.setObjectName("statusInstalled")
        else:
            badge.setText(format_size_ru(item.approx_size_mb))
            badge.setObjectName("sizeBadge")
        self._status_labels[item.id] = badge
        top_row.addWidget(badge, alignment=Qt.AlignmentFlag.AlignRight)

        del_btn = QPushButton("Удалить", card)
        del_btn.setObjectName("cardDeleteBtn")
        del_btn.setVisible(item.installed)
        del_btn.clicked.connect(
            lambda _checked=False, iid=item.id: self._on_delete_item_clicked(iid)
        )
        self._delete_buttons[item.id] = del_btn
        top_row.addWidget(del_btn, alignment=Qt.AlignmentFlag.AlignRight)
        card_layout.addLayout(top_row)

        desc = QLabel(item.description, card)
        desc.setObjectName("descLabel")
        desc.setWordWrap(True)
        card_layout.addWidget(desc)
        return card

    def _build_progress_panel(self, parent_layout: QVBoxLayout) -> None:
        """Создает скрытый блок индикации прогресса загрузки."""
        self._progress_container = QWidget(self)
        layout = QVBoxLayout(self._progress_container)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(6)

        self._progress_title = QLabel("Подготовка к загрузке...", self._progress_container)
        layout.addWidget(self._progress_title)

        self._progress_bar = QProgressBar(self._progress_container)
        self._progress_bar.setObjectName("downloadProgress")
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        layout.addWidget(self._progress_bar)

        self._progress_detail = QLabel("", self._progress_container)
        self._progress_detail.setObjectName("descLabel")
        layout.addWidget(self._progress_detail)

        self._progress_container.hide()
        parent_layout.addWidget(self._progress_container)

    def _build_footer(self, parent_layout: QVBoxLayout) -> None:
        """Создает нижнюю панель с опцией пропуска и кнопками действий."""
        self._skip_checkbox = QCheckBox("Не показывать это окно при следующих запусках", self)
        self._skip_checkbox.setChecked(self._report.skip_prompt_saved)
        if self._manage_mode:
            self._skip_checkbox.hide()
        parent_layout.addWidget(self._skip_checkbox)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._secondary_btn = QPushButton(
            "Закрыть" if self._manage_mode else "Запустить без загрузки",
            self,
        )
        self._secondary_btn.clicked.connect(self._on_secondary_clicked)
        btn_row.addWidget(self._secondary_btn)

        self._delete_all_btn = QPushButton("Удалить все зависимости", self)
        self._delete_all_btn.setObjectName("dangerButton")
        self._delete_all_btn.clicked.connect(self._on_delete_all_clicked)
        btn_row.addWidget(self._delete_all_btn)

        btn_row.addStretch(1)

        self._primary_btn = QPushButton("Скачать выбранное", self)
        self._primary_btn.setObjectName("accentButton")
        self._primary_btn.clicked.connect(self._on_primary_clicked)
        btn_row.addWidget(self._primary_btn)

        parent_layout.addLayout(btn_row)

    def _on_checkbox_changed(self, item_id: str) -> None:
        """Обрабатывает переключение чекбокса и автовыбор llama-server для VLM или Qwen3-TTS."""
        if item_id in {"vlm_qwen", "qwen3_tts"}:
            cb = self._checkboxes.get(item_id)
            if cb is not None and cb.isChecked():
                llama_cb = self._checkboxes.get("llama_server")
                if llama_cb is not None and llama_cb.isEnabled() and not llama_cb.isChecked():
                    llama_cb.setChecked(True)
        self._refresh_selection_summary()

    def get_selected_missing_ids(self) -> list[str]:
        """Возвращает список ID отмеченных и еще не установленных компонентов."""
        selected: list[str] = []
        for item in self._report.items:
            cb = self._checkboxes.get(item.id)
            if cb is not None and cb.isEnabled() and cb.isChecked():
                selected.append(item.id)
        return selected

    def _refresh_selection_summary(self) -> None:
        """Обновляет текст главной кнопки и проверку свободного места на диске."""
        selected_ids = self.get_selected_missing_ids()
        if not selected_ids:
            self._primary_btn.setText("Готово — Запустить" if not self._manage_mode else "Готово")
            self._warning_label.hide()
            return

        item_map = {it.id: it for it in self._report.items}
        total_mb = sum(item_map[iid].approx_size_mb for iid in selected_ids if iid in item_map)
        self._primary_btn.setText(f"Скачать выбранное (~{format_size_ru(total_mb)})")

        if self._report.free_space_mb > 0 and total_mb * 1.1 > self._report.free_space_mb:
            self._warning_label.setText(
                f"Недостаточно свободного места на диске: требуется ~{format_size_ru(total_mb)}, "
                f"доступно {format_size_ru(self._report.free_space_mb)}."
            )
            self._warning_label.show()
        else:
            self._warning_label.hide()

    def _on_primary_clicked(self) -> None:
        """Запускает фоновую загрузку либо закрывает окно, если всё уже установлено."""
        if self._worker is not None and self._worker.isRunning():
            return

        selected_ids = self.get_selected_missing_ids()
        if not selected_ids or self._completed_successfully:
            self._persist_skip_choice()
            self.accept()
            return

        self._start_download(selected_ids)

    def _on_secondary_clicked(self) -> None:
        """Отменяет активную загрузку или закрывает окно без скачивания."""
        if self._worker is not None and self._worker.isRunning():
            self._secondary_btn.setEnabled(False)
            self._progress_title.setText("Отмена загрузки...")
            self._worker.cancel()
            return

        self._persist_skip_choice()
        self.reject()

    def _persist_skip_choice(self) -> None:
        """Сохраняет состояние чекбокса «Не показывать снова»."""
        if not self._manage_mode:
            self._manager.save_skip_prompt(self._skip_checkbox.isChecked())

    def _start_download(self, item_ids: list[str]) -> None:
        """Переводит диалог в режим загрузки и стартует фоновый поток."""
        self._completed_successfully = False
        self._warning_label.hide()
        for cb in self._checkboxes.values():
            cb.setEnabled(False)
        for btn in self._delete_buttons.values():
            btn.setEnabled(False)
        self._delete_all_btn.setEnabled(False)

        self._primary_btn.setEnabled(False)
        self._primary_btn.setText("Загрузка...")
        self._secondary_btn.setText("Отмена")
        self._secondary_btn.setEnabled(True)

        self._progress_bar.setValue(0)
        self._progress_title.setText("Подключение к серверу...")
        self._progress_detail.setText("")
        self._progress_container.show()

        self._worker = DependencyDownloadWorker(self._manager, item_ids, parent=self)
        self._worker.progress_updated.connect(self._on_progress_updated)
        self._worker.download_finished.connect(self._on_download_finished)
        self._worker.start()

    def _on_progress_updated(self, progress: DownloadProgress) -> None:
        """Обновляет прогресс-бар и статус в главном потоке GUI."""
        self._progress_title.setText(
            f"Загрузка ({progress.item_index} из {progress.total_items}): {progress.item_name}"
        )
        self._progress_bar.setValue(int(round(progress.overall_percent)))
        self._progress_detail.setText(progress.status_text)

    def _sync_cards_from_report(self, *, reset_uninstalled_checks: bool) -> None:
        """Синхронизирует карточки, бейджи и кнопки удаления с актуальным inspect()."""
        self._report = self._manager.inspect()
        for item in self._report.items:
            cb = self._checkboxes[item.id]
            badge = self._status_labels[item.id]
            del_btn = self._delete_buttons[item.id]
            if item.installed:
                cb.setChecked(True)
                cb.setEnabled(False)
                badge.setText("✓ Установлено")
                badge.setObjectName("statusInstalled")
            else:
                if reset_uninstalled_checks:
                    cb.setChecked(False)
                cb.setEnabled(True)
                badge.setText(format_size_ru(item.approx_size_mb))
                badge.setObjectName("sizeBadge")
            del_btn.setVisible(item.installed)
            del_btn.setEnabled(True)
            badge.style().unpolish(badge)
            badge.style().polish(badge)
        self._refresh_storage_and_delete_all()

    def _refresh_storage_and_delete_all(self) -> None:
        """Обновляет метку занятого/свободного места и кнопку полного удаления."""
        installed_mb = self._manager.get_installed_size_mb()
        self._storage_label.setText(
            f"Папка моделей: {self._report.models_dir}  •  "
            f"Занято: {format_size_ru(installed_mb)}  •  "
            f"Свободно на диске: {format_size_ru(self._report.free_space_mb)}"
        )
        has_installed = installed_mb > 0.0 or any(it.installed for it in self._report.items)
        if installed_mb > 0.0:
            self._delete_all_btn.setText(
                f"Удалить все зависимости (~{format_size_ru(installed_mb)})"
            )
        else:
            self._delete_all_btn.setText("Удалить все зависимости")
        self._delete_all_btn.setVisible(has_installed)
        self._delete_all_btn.setEnabled(True)

    def _default_confirm_delete(self, title: str, message: str) -> bool:
        """Стандартный диалог подтверждения удаления через QMessageBox."""
        answer = QMessageBox.question(
            self,
            title,
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _on_delete_item_clicked(self, item_id: str) -> None:
        """Запрашивает подтверждение и удаляет один выбранный компонент."""
        item_map = {it.id: it for it in self._report.items}
        item = item_map.get(item_id)
        item_name = item.name if item is not None else item_id
        size_mb = self._manager.get_installed_size_mb(item_id)
        size_note = f" (~{format_size_ru(size_mb)})" if size_mb > 0.0 else ""
        self._execute_delete(
            title="Удаление компонента",
            message=f"Удалить локальные файлы компонента «{item_name}»{size_note}?",
            delete_fn=lambda: self._manager.delete_item(item_id),
        )

    def _on_delete_all_clicked(self) -> None:
        """Запрашивает подтверждение и удаляет все загруженные модели и бинарники."""
        total_mb = self._manager.get_installed_size_mb()
        size_note = f" (~{format_size_ru(total_mb)})" if total_mb > 0.0 else ""
        self._execute_delete(
            title="Удаление всех зависимостей",
            message=(
                f"Удалить все скачанные модели и llama-server.exe{size_note}?\n"
                "Настройки и база памяти config/memory.db сохранятся."
            ),
            delete_fn=self._manager.delete_all_dependencies,
        )

    def _execute_delete(
        self,
        *,
        title: str,
        message: str,
        delete_fn: Callable[[], DeleteResult],
    ) -> None:
        """Выполняет удаление с подтверждением, хуками блокировок и обновлением UI."""
        if self._worker is not None and self._worker.isRunning():
            return
        confirm_fn = self._confirm_delete or self._default_confirm_delete
        if not confirm_fn(title, message):
            return

        if self._on_before_delete is not None:
            self._on_before_delete()

        result = delete_fn()

        if self._on_after_delete is not None:
            self._on_after_delete()

        self._completed_successfully = False
        self._sync_cards_from_report(reset_uninstalled_checks=True)
        self._refresh_selection_summary()

        self._progress_container.show()
        self._progress_bar.setValue(100 if result.ok else 0)
        self._progress_detail.setText("")
        freed_str = format_size_ru(result.freed_mb)
        if result.ok:
            self._warning_label.hide()
            self._progress_title.setText(
                f"✓ Удалено файлов: {result.deleted_files}, освобождено {freed_str}."
            )
            return

        errors_preview = "; ".join(f"{k}: {v}" for k, v in result.failed.items())
        self._progress_title.setText(
            f"Удалено файлов: {result.deleted_files}, освобождено {freed_str} (есть ошибки)."
        )
        self._warning_label.setText(f"Не удалось удалить: {errors_preview}")
        self._warning_label.show()

    def _on_download_finished(self, result: DownloadResult) -> None:
        """Обрабатывает завершение фоновой загрузки и обновляет статусы карточек."""
        self._last_result = result
        self._sync_cards_from_report(reset_uninstalled_checks=False)

        self._primary_btn.setEnabled(True)
        self._secondary_btn.setEnabled(True)
        self._secondary_btn.setText("Закрыть" if self._manage_mode else "Запустить без загрузки")

        if result.ok:
            self._completed_successfully = True
            self._progress_bar.setValue(100)
            self._progress_title.setText("✓ Все выбранные компоненты успешно установлены!")
            self._progress_detail.setText("")
            self._primary_btn.setText(
                "Запустить GupkaCommander" if not self._manage_mode else "Готово"
            )
            return

        if result.cancelled:
            self._progress_title.setText("Загрузка отменена (докачка сохранена).")
            self._refresh_selection_summary()
            return

        errors_preview = "; ".join(f"{k}: {v}" for k, v in result.failed.items())
        self._progress_title.setText("Часть компонентов не удалось загрузить.")
        self._warning_label.setText(f"Ошибки загрузки: {errors_preview}")
        self._warning_label.show()
        self._primary_btn.setText("Повторить загрузку")

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        """Корректно завершает фоновый поток перед закрытием окна."""
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        super().closeEvent(event)


def run_startup_dependency_check(
    *,
    manager: DependencyManager | None = None,
    force_setup: bool = False,
    skip_setup: bool = False,
    headless: bool = False,
    quiet: bool = False,
    enable_voice: bool = True,
) -> bool:
    """Проверяет недостающие зависимости при запуске и запрашивает разрешение пользователя.

    Returns:
        True, если окно загрузки было показано и загрузка завершилась успешно; иначе False.
    """
    dep_manager = manager or DependencyManager()
    should_show = dep_manager.should_prompt_user(
        force_setup=force_setup,
        skip_setup=skip_setup,
        headless=headless,
        quiet=quiet,
        enable_voice=enable_voice,
    )
    if not should_show:
        return False

    dialog = DependencySetupDialog(manager=dep_manager, manage_mode=force_setup)
    dialog.exec()
    return dialog.last_result is not None and dialog.last_result.ok
