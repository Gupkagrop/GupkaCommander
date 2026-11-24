"""Окно настроек приложения GupkaCommander в стиле Windows 11 Fluent 2.

> [!NOTE]
> **Назначение файла:** Графический диалог PySide6 для просмотра и сохранения настроек
> config/settings.json: внешний вид и стыковка панели (8 позиций, монитор, тема,
> размытие Acrylic/Mica), движки голоса (GigaAM v3 / Faster-Whisper, Qwen3-TTS / Piper),
> пороги VAD, параметры FailSafe и автозапуск llama-server.
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
    QComboBox,
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
from gui.theme_manager import DARK_TOKENS, ThemeManager, ThemeMode, ThemeTokens

logger = logging.getLogger(__name__)

_DOCK_POSITION_ITEMS: tuple[tuple[str, str], ...] = (
    ("Сверху по центру", "top"),
    ("Снизу по центру", "bottom"),
    ("Слева (боковая панель)", "left"),
    ("Справа (боковая панель)", "right"),
    ("Верхний левый угол", "top_left"),
    ("Верхний правый угол", "top_right"),
    ("Нижний левый угол", "bottom_left"),
    ("Нижний правый угол", "bottom_right"),
)

_MONITOR_ITEMS: tuple[tuple[str, str], ...] = (
    ("Авто (активный под курсором)", "auto"),
    ("Монитор 1", "1"),
    ("Монитор 2", "2"),
    ("Монитор 3", "3"),
)

_THEME_ITEMS: tuple[tuple[str, str], ...] = (
    ("Системная (Windows 11)", "system"),
    ("Тёмная", "dark"),
    ("Светлая", "light"),
)

_BACKDROP_ITEMS: tuple[tuple[str, str], ...] = (
    ("Acrylic (стекло)", "acrylic"),
    ("Mica (матовый фон)", "mica"),
)

_STT_ENGINE_ITEMS: tuple[tuple[str, str], ...] = (
    ("GigaAM v3 (ONNX)", "gigaam-v3"),
    ("Faster-Whisper (INT8)", "faster-whisper"),
)

_TTS_ENGINE_ITEMS: tuple[tuple[str, str], ...] = (
    ("Qwen3-TTS (GPU)", "qwen3-tts"),
    ("Piper TTS (CPU)", "piper"),
)


def _build_settings_stylesheet(tokens: ThemeTokens) -> str:
    """Формирует QSS для диалога настроек в палитре Windows 11 Fluent 2."""
    return f"""
    QDialog#settingsDialog {{
        background-color: {tokens.background};
        color: {tokens.text};
        font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Inter', sans-serif;
    }}
    QLabel {{
        background: transparent;
        border: none;
        color: {tokens.text};
    }}
    QFrame#sectionCard {{
        background-color: {tokens.surface_card};
        border: 1px solid {tokens.border};
        border-radius: 10px;
    }}
    QLabel#settingsTitle {{
        font-size: 18px;
        font-weight: 600;
        color: {tokens.text};
    }}
    QLabel#sectionHeader {{
        font-size: 13px;
        font-weight: 700;
        color: {tokens.sponge_ochre};
    }}
    QLabel#hintLabel {{
        font-size: 12px;
        color: {tokens.text_secondary};
    }}
    QSpinBox, QDoubleSpinBox, QComboBox {{
        background-color: {tokens.surface};
        color: {tokens.text};
        border: 1px solid {tokens.border};
        border-radius: 6px;
        padding: 4px 10px;
        min-width: 170px;
    }}
    QComboBox::drop-down {{
        border: none;
        width: 22px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {tokens.surface};
        color: {tokens.text};
        border: 1px solid {tokens.border};
        selection-background-color: {tokens.accent_soft};
        selection-color: {tokens.text};
    }}
    QCheckBox {{
        color: {tokens.text};
        spacing: 8px;
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
    """


def _default_settings() -> dict[str, Any]:
    """Возвращает базовую структуру настроек settings.json."""
    return {
        "ui": {
            "dock_position": "top",
            "target_monitor": "auto",
            "theme": "system",
            "backdrop": "acrylic",
        },
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
            "stt": {
                "engine": "gigaam-v3",
                "fallback_engine": "faster-whisper",
            },
            "tts": {
                "engine": "qwen3-tts",
                "fallback_engine": "piper",
            },
            "llm": {
                "autostart": True,
                "n_gpu_layers": 99,
                "context_window": 8192,
            },
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


def _populate_combo(
    combo: QComboBox,
    items: tuple[tuple[str, str], ...],
    selected_value: str,
) -> None:
    """Заполняет выпадающий список парами (подпись, значение) и выбирает текущее."""
    combo.clear()
    norm_selected = str(selected_value).strip().lower()
    selected_idx = 0
    for idx, (label, value) in enumerate(items):
        combo.addItem(label, value)
        if value.lower() == norm_selected:
            selected_idx = idx
    combo.setCurrentIndex(selected_idx)


def _combo_value(combo: QComboBox, default: str) -> str:
    """Возвращает data-значение выбранного пункта QComboBox."""
    data = combo.currentData()
    return str(data) if data is not None else default


class SettingsDialog(QDialog):
    """Окно системных настроек GupkaCommander 2.0."""

    def __init__(
        self,
        settings_path: Path | None = None,
        dep_manager: DependencyManager | None = None,
        on_open_models: Callable[[], None] | None = None,
        parent: QWidget | None = None,
        *,
        theme_tokens: ThemeTokens | None = None,
    ) -> None:
        """Инициализация окна настроек с поддержкой внедрения путей и темы для тестов."""
        super().__init__(parent)
        self._settings_path = (
            settings_path if settings_path is not None else get_config_path("settings.json")
        )
        self._dep_manager = dep_manager or DependencyManager()
        self._on_open_models = on_open_models
        self._data: dict[str, Any] = self._load_settings()

        if theme_tokens is not None:
            self._tokens: ThemeTokens = theme_tokens
        else:
            saved_theme = str(self._data.get("ui", {}).get("theme", "system"))
            try:
                mode = ThemeMode(saved_theme.strip().lower())
            except ValueError:
                mode = ThemeMode.SYSTEM
            self._tokens = ThemeManager(self, mode=mode).tokens

        self.setObjectName("settingsDialog")
        self.setWindowTitle("GupkaCommander — Настройки")
        self.setMinimumWidth(520)
        self.setModal(True)

        icon_path = get_icon_path()
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._build_ui()
        self.apply_theme(self._tokens)

    def apply_theme(self, tokens: ThemeTokens = DARK_TOKENS) -> None:
        """Применяет дизайн-токены Dark/Light ко всему диалогу настроек."""
        self._tokens = tokens
        self.setStyleSheet(_build_settings_stylesheet(tokens))

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

        root.addWidget(self._build_appearance_section())
        root.addWidget(self._build_voice_section())
        root.addWidget(self._build_safety_and_llm_section())
        root.addLayout(self._build_tools_row())
        root.addLayout(self._build_footer_row())

    def _build_appearance_section(self) -> QFrame:
        """Секция внешнего вида Windows 11 Fluent 2 и параметров стыковки (8 зон)."""
        card = QFrame(self)
        card.setObjectName("sectionCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QLabel("Внешний вид и стыковка (Windows 11)", card)
        header.setObjectName("sectionHeader")
        layout.addWidget(header)

        form = QFormLayout()
        form.setSpacing(8)
        ui_cfg = self._data["ui"]

        self.dock_position_combo = QComboBox(card)
        _populate_combo(
            self.dock_position_combo,
            _DOCK_POSITION_ITEMS,
            str(ui_cfg.get("dock_position", "top")),
        )
        form.addRow("Позиция панели:", self.dock_position_combo)

        self.target_monitor_combo = QComboBox(card)
        _populate_combo(
            self.target_monitor_combo,
            _MONITOR_ITEMS,
            str(ui_cfg.get("target_monitor", "auto")),
        )
        form.addRow("Целевой монитор:", self.target_monitor_combo)

        self.theme_combo = QComboBox(card)
        _populate_combo(self.theme_combo, _THEME_ITEMS, str(ui_cfg.get("theme", "system")))
        form.addRow("Тема оформления:", self.theme_combo)

        self.backdrop_combo = QComboBox(card)
        _populate_combo(
            self.backdrop_combo, _BACKDROP_ITEMS, str(ui_cfg.get("backdrop", "acrylic"))
        )
        form.addRow("Эффект стекла (DWM):", self.backdrop_combo)

        layout.addLayout(form)
        return card

    def _build_voice_section(self) -> QFrame:
        """Секция голосового контура: движки STT/TTS, порог VAD и пауза речи."""
        card = QFrame(self)
        card.setObjectName("sectionCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QLabel("Голосовой контур (STT / TTS / VAD)", card)
        header.setObjectName("sectionHeader")
        layout.addWidget(header)

        form = QFormLayout()
        form.setSpacing(8)

        stt_cfg = self._data["models"]["stt"]
        self.stt_engine_combo = QComboBox(card)
        _populate_combo(
            self.stt_engine_combo,
            _STT_ENGINE_ITEMS,
            str(stt_cfg.get("engine", "gigaam-v3")),
        )
        form.addRow("Распознавание речи (STT):", self.stt_engine_combo)

        tts_cfg = self._data["models"]["tts"]
        self.tts_engine_combo = QComboBox(card)
        _populate_combo(
            self.tts_engine_combo,
            _TTS_ENGINE_ITEMS,
            str(tts_cfg.get("engine", "qwen3-tts")),
        )
        form.addRow("Синтез речи (TTS):", self.tts_engine_combo)

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

        hotkeys_hint = QLabel("Вызов панели: Alt+Space   •   Экстренная остановка: Esc", card)
        hotkeys_hint.setObjectName("hintLabel")
        layout.addWidget(hotkeys_hint)

        form = QFormLayout()
        form.setSpacing(8)
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
        self._data["ui"]["dock_position"] = _combo_value(self.dock_position_combo, "top")
        self._data["ui"]["target_monitor"] = _combo_value(self.target_monitor_combo, "auto")
        self._data["ui"]["theme"] = _combo_value(self.theme_combo, "system")
        self._data["ui"]["backdrop"] = _combo_value(self.backdrop_combo, "acrylic")

        self._data["models"]["stt"]["engine"] = _combo_value(self.stt_engine_combo, "gigaam-v3")
        self._data["models"]["tts"]["engine"] = _combo_value(self.tts_engine_combo, "qwen3-tts")
        self._data["models"]["llm"]["autostart"] = bool(self.llm_autostart_cb.isChecked())

        self._data["audio"]["vad_threshold"] = round(float(self.vad_threshold_spin.value()), 2)
        self._data["audio"]["silence_duration_ms"] = int(self.silence_ms_spin.value())
        self._data["system"]["mouse_failsafe"]["shake_threshold_pixels"] = int(
            self.shake_threshold_spin.value()
        )

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
