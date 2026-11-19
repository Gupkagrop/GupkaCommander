"""
> [!NOTE]
> Модуль тестов tests.test_icon_integration: Проверка интеграции системной иконки
> assets/gupka_commander.ico в SpotlightBar (иконка окна) и TrayManager (базовая иконка трея).
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from gui.spotlight_bar import SpotlightBar
from gui.tray_icon import TrayManager


@pytest.fixture(scope="session")
def qapp() -> Generator[QApplication, None, None]:
    """Глобальная фикстура QApplication для GUI тестов."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app  # type: ignore[misc]


class TestIconIntegration:
    """Тестирование применения иконки приложения в компонентах интерфейса."""

    def test_spotlight_bar_sets_window_icon_when_ico_exists(self, qapp: QApplication) -> None:
        """Проверка установки системной иконки окна SpotlightBar при наличии .ico файла."""
        bar = SpotlightBar(hide_on_focus_loss=False)
        try:
            assert bar.icon_path.exists()
            assert not bar.windowIcon().isNull()
        finally:
            bar.close()

    def test_spotlight_bar_handles_missing_ico_gracefully(
        self,
        qapp: QApplication,
        tmp_path: Path,
    ) -> None:
        """Проверка штатного поведения SpotlightBar при отсутствии файла иконки."""
        missing_ico = tmp_path / "missing.ico"
        bar = SpotlightBar(hide_on_focus_loss=False, icon_path=missing_ico)
        try:
            assert not bar.icon_path.exists()
            assert bar.windowIcon().isNull()
        finally:
            bar.close()

    def test_tray_manager_uses_base_icon_when_ico_exists(self, qapp: QApplication) -> None:
        """Проверка отрисовки трея на основе gupka_commander.ico при наличии файла."""
        tray = TrayManager()
        try:
            assert tray.base_icon_path.exists()
            icon = tray.tray_icon.icon()
            assert not icon.isNull()
            # Проверяем генерацию пиксмапа иконки
            pixmap = icon.pixmap(32, 32)
            assert not pixmap.isNull()
            assert pixmap.width() == 32
            assert pixmap.height() == 32
        finally:
            tray.hide()

    def test_tray_manager_procedural_fallback_when_ico_missing(
        self,
        qapp: QApplication,
        tmp_path: Path,
    ) -> None:
        """Проверка процедурной отрисовки трея при отсутствии файла иконки."""
        missing_ico = tmp_path / "missing_tray.ico"
        tray = TrayManager(base_icon_path=missing_ico)
        try:
            assert not tray.base_icon_path.exists()
            icon = tray.tray_icon.icon()
            assert not icon.isNull()
            pixmap = icon.pixmap(32, 32)
            assert not pixmap.isNull()
        finally:
            tray.hide()
