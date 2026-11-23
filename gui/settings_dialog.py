"""Окно настроек приложения GupkaCommander в стиле Windows 11 Fluent Dark.

> [!NOTE]
> **Назначение файла:** Графический диалог PySide6 для просмотра и сохранения настроек
> config/settings.json (пороги VAD и паузы речи, параметры FailSafe, автозапуск llama-server,
> показ мастера загрузки моделей), а также быстрого перехода к менеджеру моделей и папке данных.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.bootstrap import DependencyManager
from core.paths import get_config_path, get_data_root, get_icon_path
from gui.styles import FLUENT_DARK_THEME, PALETTE

logger = logging.getLogger(__name__)

SETTINGS_EXTRA_STYLES = f"""
QDialog#settingsDialog {{
    background-color: {PALETTE.background};
}}
QFrame#sectionCard {{
    background-color: {PALETTE.surface};
    border: 1px solid {PALETTE.border};
    border-radius: 8px;
}}
QLabel#settingsTitle {{
    font-size: 18px;
    font-weight: 600;
    color: {PALETTE.text};
}}
QLabel#sectionHeader {{
    font-size: 13px;
    font-weight: 600;
    color: {PALETTE.accent};
}}
QLabel#hintLabel {{
    font-size: 12px;
    color: {PALETTE.secondary};
}}
QSpinBox, QDoubleSpinBox {{
    background-color: {PALETTE.surface_secondary};
    color: {PALETTE.text};
    border: 1px solid {PALETTE.border};
    border-radius: 5px;
    padding: 4px 8px;
    min-width: 110px;
}}
"""


def _default_settings() -> dict[str, Any]:
    """Возвращает базовую структуру настроек settings.json."""
    return {
        "system": {
            "language": "ru",
            "hotkeys": {
                "toggle_spotlight": "alt+space",
                "emergency_kill_switch": "escape",
            },
            "mouse_failsafe": {
                "shake_detection": True,
                "shake_threshold_pixels": 350,
            },
        },
        "audio": {
            "sample_rate": 16000,
            "vad_threshold": 0.5,
            "silence_duration_ms": 600,
            "ducking_enabled": True,
        },
        "models": {
            "llm": {
                "autostart": True,
                "n_gpu_layers": 99,
                "context_window": 4096,
            }
        },
    }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Рекурсивно объединяет словари настроек, сохраняя неизвестные ключи."""
    merged = dict(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


class SettingsDialog(QDialog):
    """Окно системных настроек GupkaCommander."""

    def __init__(
        self,
        settings_path: Path | None = None,
        dep_manager: DependencyManager | None = None,
        on_open_models: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Инициализация окна настроек с поддержкой внедрения путей для тестов."""
        super().__init__(parent)
        self._settings_path = (
            settings_path if settings_path is not None else get_config_path("settings.json")
        )
        self._dep_manager = dep_manager or DependencyManager()
        self._on_open_models = on_open_models
        self._data: dict[str, Any] = self._load_settings()

        self.setObjectName("settingsDialog")
        self.setWindowTitle("GupkaCommander — Настройки")
        self.setMinimumWidth(480)
        self.setModal(True)
        self.setStyleSheet(FLUENT_DARK_THEME + SETTINGS_EXTRA_STYLES)

        icon_path = get_icon_path()
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._build_ui()

    def _load_settings(self) -> dict[str, Any]:
        """Загружает настройки из settings.json с откатом на значения по умолчанию."""
        defaults = _default_settings()
        if not self._settings_path.is_file():
            return defaults
        try:
            loaded = json.loads(self._settings_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return _deep_merge(defaults, loaded)
        except Exception as exc:
            logger.warning("Ошибка чтения %s: %s", self._settings_path, exc)
        return defaults

    def _build_ui(self) -> None:
        """Собирает секции и элементы управления диалога настроек."""
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(12)

        title = QLabel("Настройки GupkaCommander")
        title.setObjectName("settingsTitle")
        root.addWidget(title)

        root.addWidget(self._build_voice_section())
        root.addWidget(self._build_safety_and_llm_section())
        root.addLayout(self._build_tools_row())
        root.addLayout(self._build_footer_row())

    def _build_voice_section(self) -> QFrame:
        """Секция параметров голосового контура (VAD и пауза)."""
        card = QFrame(self)
        card.setObjectName("sectionCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QLabel("Голосовой контур (CPU)", card)
        header.setObjectName("sectionHeader")
        layout.addWidget(header)

        form = QFormLayout()
        form.setSpacing(8)

        audio_cfg = self._data["audio"]
        self.vad_threshold_spin = QDoubleSpinBox(card)
        self.vad_threshold_spin.setRange(0.10, 0.95)
        self.vad_threshold_spin.setSingleStep(0.05)
        self.vad_threshold_spin.setValue(float(audio_cfg.get("vad_threshold", 0.5)))
        form.addRow("Порог чувствительности VAD:", self.vad_threshold_spin)

        self.silence_ms_spin = QSpinBox(card)
        self.silence_ms_spin.setRange(200, 2000)
        self.silence_ms_spin.setSingleStep(50)
        self.silence_ms_spin.setSuffix(" мс")
        self.silence_ms_spin.setValue(int(audio_cfg.get("silence_duration_ms", 600)))
        form.addRow("Пауза окончания фразы:", self.silence_ms_spin)

        layout.addLayout(form)
        return card

    def _build_safety_and_llm_section(self) -> QFrame:
        """Секция безопасности FailSafe и автозапуска локальной VLM."""
        card = QFrame(self)
        card.setObjectName("sectionCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QLabel("Безопасность и локальный ИИ", card)
        header.setObjectName("sectionHeader")
        layout.addWidget(header)

        form = QFormLayout()
        form.setSpacing(8)

        hotkeys_hint = QLabel("Вызов панели: Alt+Space   •   Экстренная остановка: Esc", card)
        hotkeys_hint.setObjectName("hintLabel")
        layout.addWidget(hotkeys_hint)

        failsafe_cfg = self._data["system"]["mouse_failsafe"]
        self.shake_threshold_spin = QSpinBox(card)
        self.shake_threshold_spin.setRange(150, 1000)
        self.shake_threshold_spin.setSingleStep(50)
        self.shake_threshold_spin.setSuffix(" px")
        self.shake_threshold_spin.setValue(int(failsafe_cfg.get("shake_threshold_pixels", 350)))
        form.addRow("Порог Mouse Shake (FailSafe):", self.shake_threshold_spin)
        layout.addLayout(form)

        llm_cfg = self._data["models"]["llm"]
        self.llm_autostart_cb = QCheckBox(
            "Автоматически запускать llama-server.exe при наличии скачанной VLM", card
        )
        self.llm_autostart_cb.setChecked(bool(llm_cfg.get("autostart", True)))
        layout.addWidget(self.llm_autostart_cb)

        self.show_bootstrap_cb = QCheckBox(
            "Показывать мастер загрузки моделей при запуске, если чего-то не хватает", card
        )
        self.show_bootstrap_cb.setChecked(not self._dep_manager.is_skip_prompt_saved())
        layout.addWidget(self.show_bootstrap_cb)

        return card

    def _build_tools_row(self) -> QHBoxLayout:
        """Строка быстрых действий: менеджер моделей и папка данных."""
        row = QHBoxLayout()
        row.setSpacing(10)

        self.manage_models_btn = QPushButton("Модели и зависимости...", self)
        self.manage_models_btn.clicked.connect(self._handle_open_models)
        row.addWidget(self.manage_models_btn)

        self.open_data_dir_btn = QPushButton("Открыть папку данных", self)
        self.open_data_dir_btn.clicked.connect(self._handle_open_data_dir)
        row.addWidget(self.open_data_dir_btn)

        row.addStretch(1)
        return row

    def _build_footer_row(self) -> QHBoxLayout:
        """Нижние кнопки Сохранить / Отмена."""
        row = QHBoxLayout()
        row.setSpacing(10)

        self.cancel_btn = QPushButton("Отмена", self)
        self.cancel_btn.clicked.connect(self.reject)
        row.addWidget(self.cancel_btn)

        row.addStretch(1)

        self.save_btn = QPushButton("Сохранить", self)
        self.save_btn.setObjectName("accentButton")
        self.save_btn.clicked.connect(self._handle_save)
        row.addWidget(self.save_btn)

        return row

    def _handle_open_models(self) -> None:
        """Открывает менеджер загрузки моделей."""
        if self._on_open_models is not None:
            self._on_open_models()
            return
        from gui.setup_dialog import DependencySetupDialog

        dlg = DependencySetupDialog(manager=self._dep_manager, manage_mode=True, parent=self)
        dlg.exec()

    def _handle_open_data_dir(self) -> None:
        """Открывает пользовательскую директорию данных в Проводнике Windows."""
        data_root = get_data_root()
        if hasattr(os, "startfile"):
            try:
                os.startfile(str(data_root))
            except OSError as exc:
                logger.warning("Не удалось открыть папку %s: %s", data_root, exc)

    def save_settings(self) -> dict[str, Any]:
        """Применяет значения виджетов и сохраняет settings.json и bootstrap_state.json."""
        self._data["audio"]["vad_threshold"] = round(float(self.vad_threshold_spin.value()), 2)
        self._data["audio"]["silence_duration_ms"] = int(self.silence_ms_spin.value())
        self._data["system"]["mouse_failsafe"]["shake_threshold_pixels"] = int(
            self.shake_threshold_spin.value()
        )
        self._data["models"]["llm"]["autostart"] = bool(self.llm_autostart_cb.isChecked())

        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        self._settings_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._dep_manager.save_skip_prompt(not self.show_bootstrap_cb.isChecked())
        return self._data

    def _handle_save(self) -> None:
        """Сохраняет настройки и закрывает диалог с кодом Accepted."""
        self.save_settings()
        self.accept()
