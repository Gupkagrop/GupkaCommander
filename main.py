"""
> [!NOTE]
> Модуль main: Главная точка входа приложения GupkaCommander.
> Инициализирует QApplication, компоненты быстрого пути (AppFinder, OSController),
> голосовой конвейер (VoicePipeline), плавающее окно ввода (SpotlightBar),
> системный трей (TrayManager) и глобальный диспетчер горячих клавиш (HotkeyManager).
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from core.app_finder import AppFinder
from core.os_controller import OSController
from core.voice.voice_pipeline import PipelineState, VoicePipeline
from gui.hotkey_manager import HotkeyManager
from gui.spotlight_bar import SpotlightBar
from gui.tray_icon import TrayManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
)
logger = logging.getLogger("gupka.main")


class VoiceBridge(QObject):
    """
    Потокобезопасный мост между фоновым потоком VoicePipeline и главным GUI потоком Qt.

    Принимает вызовы из фонового потока и пересылает их через сигналы Qt,
    гарантируя безопасное обновление виджетов без кросс-потоковых ошибок.
    """

    transcription_received = Signal(str)
    state_changed = Signal(str)
    audio_level_received = Signal(float)


class GupkaCommanderApp(QObject):
    """
    Главный контроллер приложения GupkaCommander.

    Координирует:
    - Ввод пользователя через SpotlightBar или голос (VoicePipeline).
    - Маршрутизацию и выполнение команд через OSController и AppFinder.
    - Озвучивание результатов (TTS).
    - Синхронизацию статусов с WaveformWidget и TrayManager.
    - Переключение видимости по Alt+Space (HotkeyManager).
    """

    def __init__(
        self,
        app: QApplication | None = None,
        app_finder: AppFinder | None = None,
        os_controller: OSController | None = None,
        voice_pipeline: VoicePipeline | None = None,
        spotlight_bar: SpotlightBar | None = None,
        tray_manager: TrayManager | None = None,
        hotkey_manager: HotkeyManager | None = None,
        enable_voice: bool = True,
        enable_hotkey: bool = True,
        enable_tray: bool = True,
    ) -> None:
        """
        Инициализация главного контроллера с поддержкой внедрения зависимостей.

        Args:
            app: Экземпляр QApplication.
            app_finder: Модуль поиска установленных программ Windows.
            os_controller: Фасад системного управления OSController.
            voice_pipeline: Голосовой конвейер VoicePipeline.
            spotlight_bar: Главная поисковая строка SpotlightBar.
            tray_manager: Иконка в системном трее TrayManager.
            hotkey_manager: Глобальный перехватчик горячих клавиш.
            enable_voice: Флаг активации голосового конвейера.
            enable_hotkey: Флаг активации глобального хоткея Alt+Space.
            enable_tray: Флаг отображения системного трея.
        """
        super().__init__()

        # 1. Qt Application
        existing_app = app or QApplication.instance()
        if not isinstance(existing_app, QApplication):
            self._app = QApplication(sys.argv)
        else:
            self._app = existing_app

        # 2. Компоненты ядра (Core)
        self.app_finder = app_finder or AppFinder()
        self.os_controller = os_controller or OSController()
        self.voice_bridge = VoiceBridge()

        # 3. Голосовой конвейер
        self.enable_voice = enable_voice
        self.voice_pipeline = voice_pipeline or VoicePipeline(
            on_transcription=self._on_voice_transcription,
            on_state_changed=self._on_voice_state_changed,
        )

        # 4. Пользовательский интерфейс (GUI)
        self.spotlight_bar = spotlight_bar or SpotlightBar()
        self.tray_manager = tray_manager or TrayManager()
        self.hotkey_manager = hotkey_manager or HotkeyManager()

        self.enable_hotkey = enable_hotkey
        self.enable_tray = enable_tray

        self._connect_signals()

    def _connect_signals(self) -> None:
        """Связывание сигналов и слотов между ядром, голосом и интерфейсом."""
        # Мост голоса -> GUI
        self.voice_bridge.transcription_received.connect(self.handle_transcription)
        self.voice_bridge.state_changed.connect(self.spotlight_bar.set_pipeline_state)
        self.voice_bridge.state_changed.connect(self.tray_manager.set_state)
        self.voice_bridge.audio_level_received.connect(self.spotlight_bar.set_audio_level)

        # SpotlightBar -> Выполнение команд
        self.spotlight_bar.command_submitted.connect(self.execute_command)

        # Горячие клавиши -> Видимость SpotlightBar
        self.hotkey_manager.hotkey_pressed.connect(self.spotlight_bar.toggle_visibility)

        # Системный трей -> Действия
        self.tray_manager.show_hide_triggered.connect(self.spotlight_bar.toggle_visibility)
        self.tray_manager.mute_toggled.connect(self.handle_mute_toggled)
        self.tray_manager.quit_requested.connect(self.shutdown)

        # Корректное завершение при закрытии приложения
        self._app.aboutToQuit.connect(self.shutdown)

    def _on_voice_transcription(self, text: str) -> None:
        """Коллбэк из фонового потока VoicePipeline при распознавании речи."""
        self.voice_bridge.transcription_received.emit(text)

    def _on_voice_state_changed(self, state: PipelineState) -> None:
        """Коллбэк из фонового потока VoicePipeline при смене состояния."""
        state_str = state.value if hasattr(state, "value") else str(state)
        self.voice_bridge.state_changed.emit(state_str)

    def handle_transcription(self, text: str) -> None:
        """
        Обработка голосовой транскрипции на главном потоке Qt.

        Args:
            text: Распознанный текст речи.
        """
        clean_text = text.strip()
        if not clean_text:
            return

        logger.info("Получена голосовая транскрипция: %s", clean_text)
        self.spotlight_bar.set_input_text(clean_text)
        self.execute_command(clean_text)

    def execute_command(self, text: str) -> str:
        """
        Основной цикл исполнения команды пользователя.

        1. Отображает запрос пользователя в карточке ChatDrawer.
        2. Выполняет действие через OSController или AppFinder.
        3. Отображает ответ ассистента в ChatDrawer.
        4. Озвучивает ответ через синтезатор речи (TTS).

        Args:
            text: Текст команды.

        Returns:
            str: Текст ответа ассистента.
        """
        clean_text = text.strip()
        if not clean_text:
            return ""

        # Добавляем сообщение пользователя в историю
        self.spotlight_bar.add_chat_message("user", clean_text, status="done")
        self.spotlight_bar.clear_input()

        # Выполняем команду через быстрый путь
        response_text = self._dispatch_command(clean_text)

        # Добавляем ответ ассистента в историю
        self.spotlight_bar.add_chat_message("assistant", response_text, status="done")

        # Озвучиваем ответ, если голосовой вывод доступен и микрофон/звук не заглушен
        if self.enable_voice and not self.tray_manager.is_muted():
            try:
                self.voice_pipeline.speak(response_text, blocking=False)
            except Exception as exc:
                logger.warning("Ошибка при воспроизведении ответа через TTS: %s", exc)

        return response_text

    def _dispatch_command(self, text: str) -> str:
        """
        Маршрутизация текстовой команды по специализированным обработчикам.

        Args:
            text: Исходный текст команды.

        Returns:
            str: Результат выполнения для пользователя.
        """
        lower = text.lower().strip()

        # 1. Системные действия: громкость и терминал
        if any(kw in lower for kw in ("громкость выше", "прибавь звук", "звук выше")):
            self.os_controller.volume_up()
            return "Громкость увеличена."

        if any(kw in lower for kw in ("громкость ниже", "убавь звук", "звук ниже")):
            self.os_controller.volume_down()
            return "Громкость уменьшена."

        if any(kw in lower for kw in ("выключи звук", "без звука", "мут", "mute")):
            self.os_controller.volume_mute()
            return "Звук переключен."

        if lower in ("терминал", "консоль", "открой терминал", "запусти терминал"):
            self.os_controller.open_terminal()
            return "Окно терминала запущено."

        # 2. Прямые веб-алиасы (например: "ютуб", "youtube", "яндекс")
        alias_url = self.os_controller.resolve_alias(lower)
        if alias_url is not None:
            self.os_controller.open_alias(lower)
            return f"Открываю веб-сайт: {lower}"

        # 3. Поисковые запросы в браузере ("найди ...", "поиск ...")
        for prefix in ("найди в google ", "найди в гугл ", "найди ", "поиск ", "гугл "):
            if lower.startswith(prefix):
                query = text[len(prefix) :].strip()
                if query:
                    self.os_controller.search(query)
                    return f"Ищу в интернете: {query}"

        # 4. Запуск приложений через AppFinder
        app_target = text
        for launch_prefix in ("открой ", "запусти "):
            if lower.startswith(launch_prefix):
                app_target = text[len(launch_prefix) :].strip()
                break

        found_apps = self.app_finder.find(app_target, threshold=60.0)
        if found_apps:
            best_app = found_apps[0]
            success = self._launch_app(best_app.path)
            if success:
                return f"Запущено приложение: {best_app.name}"

        # 5. Резервный поиск в поисковой системе
        self.os_controller.search(text)
        return f"Команда не найдена локально. Выполняю поиск в сети: {text}"

    def _launch_app(self, app_path: str) -> bool:
        """
        Безопасный запуск исполняемого файла приложения.

        Args:
            app_path: Путь к файлу программы.

        Returns:
            bool: True при успешном запуске.
        """
        try:
            if hasattr(os, "startfile"):
                os.startfile(app_path)
                return True

            subprocess.Popen([app_path], shell=True)  # noqa: S602
            return True
        except Exception as exc:
            logger.error("Не удалось запустить приложение '%s': %s", app_path, exc)
            return False

    def handle_mute_toggled(self, muted: bool) -> None:
        """
        Обработка отключения или включения микрофона.

        Args:
            muted: True, если микрофон отключен.
        """
        logger.info("Состояние микрофона изменено: muted=%s", muted)
        if muted:
            try:
                self.voice_pipeline.synthesizer.stop()
            except Exception as exc:
                logger.debug("Ошибка при остановке синтезатора: %s", exc)

    def start(self) -> None:
        """Запуск всех подсистем приложения."""
        logger.info("Запуск подсистем GupkaCommander...")

        # 1. Системный трей
        if self.enable_tray:
            self.tray_manager.show()

        # 2. Глобальные горячие клавиши (Alt+Space)
        if self.enable_hotkey:
            self.hotkey_manager.start()

        # 3. Голосовой конвейер
        if self.enable_voice:
            try:
                self.voice_pipeline.start()
            except Exception as exc:
                logger.warning("Не удалось запустить голосовой конвейер: %s", exc)

        # 4. Отображение главного окна SpotlightBar
        self.spotlight_bar.show_bar()
        logger.info("GupkaCommander успешно запущен и готов к работе.")

    def shutdown(self) -> None:
        """Корректное завершение работы и освобождение системных ресурсов."""
        logger.info("Остановка GupkaCommander...")

        if self.enable_hotkey:
            try:
                self.hotkey_manager.stop()
            except Exception as exc:
                logger.debug("Ошибка при остановке HotkeyManager: %s", exc)

        if self.enable_voice:
            try:
                self.voice_pipeline.stop()
            except Exception as exc:
                logger.debug("Ошибка при остановке VoicePipeline: %s", exc)

        try:
            self.spotlight_bar.hide_bar()
            self.tray_manager.hide()
        except Exception as exc:
            logger.debug("Ошибка при скрытии интерфейса: %s", exc)

        logger.info("GupkaCommander остановлен.")


def parse_arguments() -> argparse.Namespace:
    """Парсинг аргументов командной строки."""
    parser = argparse.ArgumentParser(description="GupkaCommander Desktop Voice & Fast-Path Agent")
    parser.add_argument(
        "--no-voice",
        action="store_true",
        help="Отключить голосовой конвейер (AudioCapture, Whisper STT, Piper TTS)",
    )
    parser.add_argument(
        "--no-hotkey",
        action="store_true",
        help="Отключить глобальный перехват Alt+Space",
    )
    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="Отключить иконку в системном трее",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Запустить интерфейс в headless режиме (offscreen)",
    )
    return parser.parse_args()


def main() -> int:
    """Точка входа запуска приложения GupkaCommander."""
    args = parse_arguments()

    if args.headless or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(sys.argv)

    commander = GupkaCommanderApp(
        app=app,
        enable_voice=not args.no_voice,
        enable_hotkey=not args.no_hotkey,
        enable_tray=not args.no_tray,
    )

    commander.start()

    # В headless режиме не блокируем процесс бесконечно
    if args.headless:
        commander.shutdown()
        return 0

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
