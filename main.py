"""
> [!NOTE]
> Модуль main: Главная точка входа приложения GupkaCommander.
> Инициализирует QApplication, компоненты быстрого пути (AppFinder, OSController),
> маршрутизатор намерений (IntentRouter), когнитивное ядро (LocalLLMClient),
> визуальный агент (VisionAgent), голосовой конвейер (VoicePipeline),
> плавающее окно ввода (SpotlightBar), системный трей (TrayManager)
> и глобальный диспетчер горячих клавиш (HotkeyManager).
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from core.app_finder import AppFinder
from core.brain.llama_manager import LlamaServerManager
from core.brain.llm_client import LocalLLMClient
from core.failsafe import FailSafeMonitor, get_failsafe_monitor
from core.memory import MemoryManager
from core.os_controller import DangerousCommandError, OSController
from core.paths import get_database_path, get_icon_path
from core.routing.intent_router import Intent, IntentRouter, RouteResult
from core.uia import UIAutomationManager
from core.vision.vision_agent import ActionFailedError, VisionAgent
from core.voice.sentence_streamer import SentenceAccumulator
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
    Потокобезопасный мост между фоновыми потоками (VoicePipeline, CommandWorker) и GUI Qt.

    Принимает вызовы из фонового потока и пересылает их через сигналы Qt,
    гарантируя безопасное обновление виджетов без кросс-потоковых ошибок и фризов UI.
    """

    transcription_received = Signal(str)
    state_changed = Signal(str)
    audio_level_received = Signal(float)
    chat_message_requested = Signal(str, str, str)
    input_clear_requested = Signal()
    stream_card_started = Signal()
    stream_chunk_received = Signal(str)
    stream_card_finished = Signal(str)


class CommandWorker(QThread):
    """Фоновый поток Qt для неблокирующего выполнения команд и SSE-стриминга."""

    command_completed = Signal(str)

    def __init__(
        self,
        runner: Callable[[str], str],
        command_text: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner = runner
        self._command_text = command_text

    def run(self) -> None:
        """Выполняет команду в фоновом потоке и отправляет итоговый ответ сигналом."""
        result = self._runner(self._command_text)
        self.command_completed.emit(result)


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
        intent_router: IntentRouter | None = None,
        llm_client: LocalLLMClient | None = None,
        vision_agent: VisionAgent | None = None,
        uia_manager: UIAutomationManager | None = None,
        failsafe_monitor: FailSafeMonitor | None = None,
        memory_manager: MemoryManager | None = None,
        llama_manager: LlamaServerManager | None = None,
        enable_voice: bool = True,
        enable_hotkey: bool = True,
        enable_tray: bool = True,
        enable_llm_autostart: bool = False,
        async_execution: bool = False,
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
            intent_router: Маршрутизатор намерений IntentRouter.
            llm_client: Клиент взаимодействия с локальной LLM/VLM.
            vision_agent: Агент визуального взаимодействия с экраном.
            uia_manager: Менеджер Microsoft UI Automation.
            failsafe_monitor: Монитор безопасности FailSafe для прерывания операций по Esc.
            memory_manager: Менеджер долговременной памяти SQLite.
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
        self.intent_router = intent_router or IntentRouter()
        self.llm_client = llm_client or LocalLLMClient(router=self.intent_router)
        self.vision_agent = vision_agent or VisionAgent()
        self.uia_manager = uia_manager or UIAutomationManager()
        self.failsafe_monitor = failsafe_monitor or get_failsafe_monitor()
        self.memory_manager = memory_manager or MemoryManager(
            db_path=get_database_path("memory.db")
        )
        self.llama_manager = llama_manager or LlamaServerManager()
        self.enable_llm_autostart = enable_llm_autostart
        self._async_execution: bool = async_execution
        self._command_worker: CommandWorker | None = None
        self._active_stream_card: Any | None = None
        self._stream_ui_tokens: list[str] = []
        self._current_response_streamed: bool = False
        self._audio_streamed: bool = False
        self._is_shutting_down: bool = False
        self._is_executing_command: bool = False
        self._execution_lock: threading.Lock = threading.Lock()
        self.voice_bridge = VoiceBridge()

        # 3. Голосовой конвейер
        self.enable_voice = enable_voice
        self.voice_pipeline = voice_pipeline or VoicePipeline(
            on_transcription=self._on_voice_transcription,
            on_state_changed=self._on_voice_state_changed,
            on_audio_level=self._on_audio_level,
        )

        # 4. Пользовательский интерфейс (GUI)
        self.spotlight_bar = spotlight_bar or SpotlightBar()
        self.tray_manager = tray_manager or TrayManager()
        self.hotkey_manager = hotkey_manager or HotkeyManager()

        self.enable_hotkey = enable_hotkey
        self.enable_tray = enable_tray

        self._connect_signals()

    @property
    def spotlight(self) -> SpotlightBar:
        """Алиас для spotlight_bar для совместимости со спецификацией координатора."""
        return self.spotlight_bar

    def _connect_signals(self) -> None:
        """Связывание сигналов и слотов между ядром, голосом и интерфейсом."""
        # Мост голоса и фонового воркера -> GUI
        self.voice_bridge.transcription_received.connect(self.handle_transcription)
        self.voice_bridge.state_changed.connect(self.spotlight_bar.set_pipeline_state)
        self.voice_bridge.state_changed.connect(self.tray_manager.set_state)
        self.voice_bridge.audio_level_received.connect(self.spotlight.waveform.set_audio_level)
        self.voice_bridge.chat_message_requested.connect(self._ui_add_chat_message)
        self.voice_bridge.input_clear_requested.connect(self._ui_clear_input)
        self.voice_bridge.stream_card_started.connect(self._ui_begin_stream_card)
        self.voice_bridge.stream_chunk_received.connect(self._ui_on_stream_chunk)
        self.voice_bridge.stream_card_finished.connect(self._ui_finish_stream_card)

        # SpotlightBar -> Выполнение команд
        self.spotlight_bar.command_submitted.connect(self._handle_submitted_command)

        # Горячие клавиши -> Видимость SpotlightBar
        self.hotkey_manager.hotkey_pressed.connect(self.spotlight_bar.toggle_visibility)

        # Системный трей -> Действия
        self.tray_manager.show_hide_triggered.connect(self.spotlight_bar.toggle_visibility)
        self.tray_manager.mute_toggled.connect(self.handle_mute_toggled)
        if hasattr(self.tray_manager, "models_triggered"):
            self.tray_manager.models_triggered.connect(self.open_dependency_manager)
        if hasattr(self.tray_manager, "settings_triggered"):
            self.tray_manager.settings_triggered.connect(self.open_settings_dialog)
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

    def _on_audio_level(self, level: float) -> None:
        """Коллбэк из фонового потока VoicePipeline при обновлении громкости входящего звука."""
        self.voice_bridge.audio_level_received.emit(level)

    def _is_gui_thread(self) -> bool:
        """Возвращает True, если вызов выполняется в главном GUI-потоке Qt."""
        return QThread.currentThread() == self._app.thread()

    def _ui_add_chat_message(self, role: str, text: str, status: str) -> None:
        """Слот главного потока Qt для добавления карточки сообщения в ChatDrawer."""
        self.spotlight_bar.add_chat_message(role, text, status=status)

    def _ui_clear_input(self) -> None:
        """Слот главного потока Qt для очистки поля ввода SpotlightBar."""
        self.spotlight_bar.clear_input()

    def _emit_chat_message(self, role: str, text: str, status: str = "done") -> None:
        """Потокобезопасное добавление сообщения в ChatDrawer."""
        if self._is_gui_thread():
            self.spotlight_bar.add_chat_message(role, text, status=status)
            return
        self.voice_bridge.chat_message_requested.emit(role, text, status)

    def _emit_clear_input(self) -> None:
        """Потокобезопасная очистка поля ввода SpotlightBar."""
        if self._is_gui_thread():
            self.spotlight_bar.clear_input()
            return
        self.voice_bridge.input_clear_requested.emit()

    def _handle_submitted_command(self, text: str) -> None:
        """Обработчик сигнала SpotlightBar.command_submitted."""
        if self._async_execution:
            self.execute_command_async(text)
            return
        self.execute_command(text)

    def execute_command_async(self, text: str) -> bool:
        """Запускает выполнение команды в фоновом QThread без блокировки GUI.

        Returns:
            True, если фоновый воркер запущен; False, если текст пуст или команда уже выполняется.
        """
        clean_text = text.strip()
        if not clean_text:
            return False

        with self._execution_lock:
            is_busy = self._is_executing_command or (
                self._command_worker is not None and self._command_worker.isRunning()
            )
        if is_busy:
            msg = "Предыдущая операция еще не завершена. Пожалуйста, подождите."
            self._emit_chat_message("assistant", msg, status="error")
            return False

        worker = CommandWorker(self.execute_command, clean_text, parent=self)
        self._command_worker = worker
        worker.start()
        return True

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
        if self._async_execution:
            self.execute_command_async(clean_text)
            return
        self.execute_command(clean_text)

    def execute_command(self, text: str) -> str:
        """
        Основной цикл исполнения команды пользователя.

        1. Отображает запрос пользователя в карточке ChatDrawer.
        2. Маршрутизирует команду через IntentRouter.
        3. Выполняет действие через OSController, AppFinder, VisionAgent или LocalLLMClient.
        4. Отображает ответ ассистента в ChatDrawer.
        5. Озвучивает ответ через синтезатор речи (TTS).

        Args:
            text: Текст команды.

        Returns:
            str: Текст ответа ассистента.
        """
        clean_text = text.strip()
        if not clean_text:
            return ""

        # Защита от одновременного выполнения команд и реентрантности
        with self._execution_lock:
            if self._is_executing_command:
                logger.warning("Команда уже выполняется, повторный запрос отклонен: %s", clean_text)
                msg = "Предыдущая операция еще не завершена. Пожалуйста, подождите."
                self._emit_chat_message("assistant", msg, status="error")
                return msg
            self._is_executing_command = True

        try:
            # Сброс флага аварийного прерывания FailSafe перед запуском команды
            try:
                self.failsafe_monitor.reset_abort()
            except Exception as exc:
                logger.debug("Ошибка сброса FailSafe: %s", exc)

            # Добавляем сообщение пользователя в историю
            self._emit_chat_message("user", clean_text, status="done")
            self._emit_clear_input()

            self._current_response_streamed = False
            self._audio_streamed = False

            # Определение намерения для логирования и маршрутизации
            route = self.intent_router.route(clean_text)
            intent_name = str(
                route.intent.value if hasattr(route.intent, "value") else route.intent
            )

            # Логируем шаг пользователя в базу долговременной памяти
            try:
                self.memory_manager.log_turn(role="user", text=clean_text, intent=intent_name)
            except Exception as exc:
                logger.warning("Ошибка логирования хода пользователя в память: %s", exc)

            try:
                # Выполняем команду через маршрутизатор и исполнители
                response_text = self._dispatch_command(clean_text, route=route)
            except InterruptedError:
                logger.warning("Исполнение команды прервано пользователем (FailSafe).")
                response_text = "Операция экстренно прервана пользователем (FailSafe)."
                self._current_response_streamed = False
                self._audio_streamed = True
            except DangerousCommandError as exc:
                logger.warning("Попытка выполнения небезопасной команды: %s", exc)
                response_text = f"Команда заблокирована системой безопасности: {exc}"
                self._current_response_streamed = False
            except ActionFailedError as exc:
                logger.warning("Действие интерфейса не удалось: %s", exc)
                response_text = f"Не удалось выполнить визуальное действие: {exc}"
                self._current_response_streamed = False
            except Exception as exc:
                logger.error("Ошибка при обработке команды '%s': %s", clean_text, exc)
                response_text = f"Произошла ошибка при выполнении: {exc}"
                self._current_response_streamed = False

            # Логируем ответ ассистента в базу долговременной памяти
            try:
                self.memory_manager.log_turn(
                    role="assistant", text=response_text, intent=intent_name
                )
            except Exception as exc:
                logger.warning("Ошибка логирования ответа ассистента в память: %s", exc)

            # Добавляем ответ ассистента в историю, если он не был добавлен потоком
            if not self._current_response_streamed:
                self._emit_chat_message("assistant", response_text, status="done")

            # Озвучиваем ответ через TTS, если не было потокового воспроизведения
            if not self._audio_streamed and self.enable_voice and not self.tray_manager.is_muted():
                try:
                    self.voice_pipeline.speak(response_text, blocking=False)
                except Exception as exc:
                    logger.warning("Ошибка при воспроизведении ответа через TTS: %s", exc)

            self._current_response_streamed = False
            self._audio_streamed = False

            return response_text
        finally:
            with self._execution_lock:
                self._is_executing_command = False

    def _dispatch_command(self, text: str, route: RouteResult | None = None) -> str:
        """
        Маршрутизация текстовой команды по специализированным обработчикам.

        Args:
            text: Исходный текст команды.
            route: Опционально предварительно вычисленный результат маршрутизации.

        Returns:
            str: Результат выполнения для пользователя.
        """
        active_route = route or self.intent_router.route(text)

        if active_route.intent == Intent.VOLUME_CONTROL:
            return self._handle_volume_control(active_route)

        if active_route.intent == Intent.WINDOW_CONTROL:
            return self._handle_window_control(active_route)

        if active_route.intent == Intent.MEMORY:
            return self._handle_memory_intent(active_route)

        if active_route.intent == Intent.OPEN_URL:
            return self._handle_open_url(active_route, text)

        if active_route.intent == Intent.SEARCH_WEB:
            return self._handle_search_web(active_route)

        if active_route.intent == Intent.RUN_COMMAND:
            return self._handle_run_command(active_route, text)

        if active_route.intent == Intent.OPEN_APP:
            return self._handle_open_app(active_route)

        if active_route.intent == Intent.VISION_INTERACT:
            return self._handle_vision_interact(active_route, text)

        if active_route.intent == Intent.UI_AUTOMATION:
            return self._handle_ui_automation(active_route, text)

        return self._handle_ai_cognitive(text)

    def _handle_memory_intent(self, params: dict[str, Any] | RouteResult) -> str:
        """
        Диспетчер команд долговременной памяти для быстрого исполнения (< 15 мс).

        Args:
            params: Параметры намерения (словарь параметров или экземпляр RouteResult).

        Returns:
            str: Результат операции для вывода пользователю и голосового ответа.
        """
        p = params.params if isinstance(params, RouteResult) else params
        action = str(p.get("action", ""))

        if action in ("store_fact", "save_fact", "store_user_fact"):
            key = str(p.get("key", "")).strip()
            val = str(p.get("value", "")).strip()
            cat = str(p.get("category", "general")).strip()
            if not key or not val:
                return "Не удалось определить ключ или значение факта."
            self.memory_manager.set_fact(key, val, category=cat)
            return f"Запомнил: {key} — это {val}."

        if action in ("retrieve_facts", "list_facts", "retrieve_user_facts"):
            cat_filter = str(p.get("category", "")).strip() or None
            facts = self.memory_manager.list_facts(category=cat_filter)
            if not facts:
                return "Я пока ничего о вас не помню."
            items = [f"{k}: {v}" for k, v in facts.items()]
            return "Вот что я о вас помню: " + "; ".join(items) + "."

        if action in ("add_note", "create_note", "add_user_note"):
            content = str(p.get("content", "")).strip()
            if not content:
                return "Текст заметки не может быть пустым."
            note_id = self.memory_manager.add_note(content)
            return f"Заметка #{note_id} сохранена: {content}."

        if action in ("list_notes", "list_user_notes"):
            query_str = str(p.get("query", "")).strip() or None
            notes = self.memory_manager.list_notes(only_active=True, query=query_str)
            if not notes:
                return "У вас нет активных заметок."
            preview = [f"#{n['id']} {n['content']}" for n in notes[:5]]
            total = len(notes)
            count_str = f" ({total})" if total > 1 else ""
            return f"Ваши заметки{count_str}: " + "; ".join(preview) + "."

        if action in ("complete_note", "complete_user_note", "close_note"):
            note_id = int(p.get("note_id", 0))
            if note_id <= 0:
                return "Не указан номер заметки."
            ok = self.memory_manager.complete_note(note_id)
            return (
                f"Заметка #{note_id} отмечена как выполненная."
                if ok
                else f"Заметка #{note_id} не найдена."
            )

        if action in ("delete_note", "delete_user_note"):
            note_id = int(p.get("note_id", 0))
            if note_id <= 0:
                return "Не указан номер заметки."
            ok = self.memory_manager.delete_note(note_id)
            return f"Заметка #{note_id} удалена." if ok else f"Заметка #{note_id} не найдена."

        return "Команда памяти не распознана."

    def _handle_volume_control(self, route: RouteResult) -> str:
        """Обработка команд управления системной громкостью."""
        action = route.params.get("action", route.target)
        if action == "up":
            self.os_controller.volume_up()
            return "Громкость увеличена."
        if action == "down":
            self.os_controller.volume_down()
            return "Громкость уменьшена."
        if action == "mute":
            self.os_controller.volume_mute()
            return "Звук переключен."
        if action == "set":
            val = route.params.get("value", 50)
            return f"Громкость установлена на {val}%."
        self.os_controller.volume_up()
        return "Громкость изменена."

    def _handle_window_control(self, route: RouteResult) -> str:
        """Обработка команд управления окнами Windows."""
        action = route.params.get("action", route.target)
        target = str(route.params.get("target_window", ""))
        if action == "minimize":
            self.os_controller.minimize_window(target)
            return "Окно свернуто."
        if action == "maximize":
            self.os_controller.maximize_window(target)
            return "Окно развернуто."
        if action == "close":
            self.os_controller.close_window(target)
            return "Окно закрыто."
        if action == "restore":
            self.os_controller.focus_window(target)
            return "Окно восстановлено."
        if action == "minimize_all":
            self.os_controller.minimize_window("")
            self.os_controller.minimize_all()
            return "Все окна свернуты."
        return f"Выполнено действие с окном: {action}."

    def _handle_open_url(self, route: RouteResult, raw_text: str) -> str:
        """Обработка открытия веб-адресов и алиасов сайтов."""
        lower = raw_text.lower().strip()
        alias_url = self.os_controller.resolve_alias(lower)
        if alias_url is not None:
            self.os_controller.open_alias(lower)
            return f"Открываю веб-сайт: {lower}"

        alias = route.params.get("alias") or route.params.get("fuzzy_alias")
        if alias:
            alias_url = self.os_controller.resolve_alias(alias)
            if alias_url is not None:
                self.os_controller.open_alias(alias)
                return f"Открываю веб-сайт: {alias}"

        url = route.params.get("url") or route.target
        self.os_controller.open_url(url)
        return f"Открываю веб-сайт: {url}"

    def _handle_search_web(self, route: RouteResult) -> str:
        """Обработка поисковых запросов в интернете."""
        query = str(route.params.get("query", route.target))
        engine = str(route.params.get("engine", "google"))
        if engine == "google":
            self.os_controller.search(query)
        else:
            self.os_controller.search(query, engine=engine)
        return f"Ищу в интернете: {query}"

    def _handle_run_command(self, route: RouteResult, raw_text: str) -> str:
        """Обработка запуска терминала и выполнения консольных команд."""
        lower = raw_text.lower().strip()
        cmd = str(route.params.get("command", route.target))
        terminal_keywords = ("терминал", "консоль", "открой терминал", "запусти терминал")
        if lower in terminal_keywords or cmd in ("", "terminal"):
            self.os_controller.open_terminal()
            return "Окно терминала запущено."
        code, stdout, stderr = self.os_controller.run_command(cmd)
        if code == 0:
            preview = stdout.strip()[:100] if stdout.strip() else "успешно"
            return f"Команда выполнена: {preview}"
        return f"Команда завершилась с ошибкой: {stderr.strip()[:100]}"

    def _handle_open_app(self, route: RouteResult) -> str:
        """Обработка запуска установленных приложений через AppFinder."""
        target = str(route.target or route.params.get("app", ""))
        found_apps = self.app_finder.find(target, threshold=60.0)
        if not found_apps and "raw_target" in route.params:
            found_apps = self.app_finder.find(str(route.params["raw_target"]), threshold=60.0)

        if found_apps:
            best_app = found_apps[0]
            success = self._launch_app(best_app.path)
            if success:
                return f"Запущено приложение: {best_app.name}"
            return f"Не удалось запустить приложение: {best_app.name}"

        return f"Приложение '{target}' не найдено на устройстве."

    def _handle_vision_interact(self, route: RouteResult, raw_text: str) -> str:
        """Обработка визуальных действий с интерфейсом (Vision Grounding)."""
        action = str(route.params.get("action", ""))
        if action == "inspect_screen":
            info = self.vision_agent.inspect_screen()
            return (
                f"Анализ экрана выполнен: {info['width']}x{info['height']}, dhash: {info['dhash']}."
            )

        if action == "type_text":
            text_to_type = str(route.params.get("text", route.target))
            self.vision_agent.type_text(text_to_type)
            return f"Введен текст: {text_to_type}"

        if action == "click_screen":
            if "x" in route.params and "y" in route.params:
                x = int(route.params["x"])
                y = int(route.params["y"])
                self.vision_agent.click_at(x, y)
                return f"Выполнен клик по координатам ({x}, {y})."

            frame_info = self.vision_agent.inspect_screen()
            image_b64 = str(frame_info.get("image_b64") or "")
            planned = self.llm_client.plan_actions(raw_text, image_b64=image_b64)
            if planned:
                return self._execute_planned_actions(planned)
            return f"Цель для клика '{route.target}' не найдена на экране."

        return f"Выполнено визуальное действие: {action}."

    def _handle_ui_automation(self, route: RouteResult, raw_text: str) -> str:
        """Обработка команд управления интерфейсом через UI Automation с Vision Fallback.

        Args:
            route: Результат маршрутизации намерения пользователя.
            raw_text: Исходный текст команды.

        Returns:
            str: Результат выполнения действия для пользователя.
        """
        action = str(route.params.get("action", "click"))
        elem = str(route.params.get("element_name", route.target))
        win = route.params.get("window_title") or None
        txt = str(route.params.get("text", ""))

        if action in ("click", "click_element"):
            if self.uia_manager.click_element(elem, window_title=win):
                target_str = f"'{elem}'" + (f" в окне '{win}'" if win else "")
                return f"Нажат элемент {target_str}."

            logger.info("Элемент '%s' не найден в UIA. Каскадный переход на VisionAgent.", elem)
            return self._fallback_click_with_vision(elem, raw_text)

        if action in ("type", "type_text", "type_into_element"):
            if self.uia_manager.type_into_element(elem, txt, window_title=win):
                target_str = f"'{elem}'" + (f" в окне '{win}'" if win else "")
                return f"Введен текст '{txt}' в поле {target_str}."

            logger.info("Поле '%s' не найдено в UIA. Запасной ввод через VisionAgent.", elem)
            self.vision_agent.type_text(txt)
            return f"Введен текст: {txt}"

        if action in ("set_toggle", "toggle", "check", "uncheck"):
            state = route.params.get("state")
            if state is None:
                state = txt.lower() in ("true", "1", "yes", "on", "вкл")
            ok = self.uia_manager.set_toggle_state(elem, bool(state), window_title=win)
            state_text = "включен" if state else "отключен"
            if ok:
                target_str = f"'{elem}'" + (f" в окне '{win}'" if win else "")
                return f"Переключатель {target_str} {state_text}."
            return f"Не удалось переключить элемент '{elem}'."

        if action == "inspect":
            count = len(self.uia_manager.inspect_window_elements(window_title=win))
            return f"Найдено {count} интерактивных элементов UI Automation."

        return f"Выполнено действие UI Automation: {action}."

    def _fallback_click_with_vision(self, elem_name: str, raw_text: str) -> str:
        """Каскадный резервный клик через VisionAgent при отсутствии элемента в дереве UIA."""
        logger.info("Вызов VisionAgent для элемента '%s' (fallback).", elem_name)
        try:
            image_b64 = ""
            try:
                frame_info = self.vision_agent.inspect_screen()
                image_b64 = str(frame_info.get("image_b64") or "")
            except Exception as exc:
                logger.warning("Не удалось получить снимок экрана: %s", exc)

            if not image_b64:
                return f"Элемент '{elem_name}' не найден на экране через визуальный анализ."

            target_x: int | None = None
            target_y: int | None = None

            query = raw_text or f"кликни {elem_name}"
            planned = self.llm_client.plan_actions(query, image_b64=image_b64)
            for act in planned:
                if act.get("tool") == "click_screen":
                    coords = act.get("parameters", {})
                    if "x" in coords and "y" in coords:
                        target_x = int(coords["x"])
                        target_y = int(coords["y"])
                        break

            if target_x is None or target_y is None:
                logger.info(
                    "Визуальный анализ не определил координаты для элемента '%s'.", elem_name
                )
                return f"Элемент '{elem_name}' не найден на экране через визуальный анализ."

            self.vision_agent.click_at(target_x, target_y, verify_change=False)
            return f"Элемент '{elem_name}' нажат через визуальный агент (Vision Fallback)."
        except Exception as exc:
            logger.warning("Ошибка при визуальном клике VisionAgent: %s", exc)
            return f"Элемент '{elem_name}' не найден на экране через визуальный анализ."

    def _has_stream_drawer(self) -> bool:
        """Проверяет, поддерживает ли ChatDrawer потоковое создание карточек."""
        drawer = getattr(self.spotlight_bar, "chat_drawer", None)
        if callable(drawer):
            drawer = drawer()
        return drawer is not None and hasattr(drawer, "add_message")

    def _setup_stream_card(self) -> Any | None:
        """Создает карточку ассистента в ChatDrawer для отображения живой печати."""
        drawer = getattr(self.spotlight_bar, "chat_drawer", None)
        if callable(drawer):
            drawer = drawer()

        if drawer is None or not hasattr(drawer, "add_message"):
            return None

        card = drawer.add_message("assistant", "", status="running")
        if hasattr(drawer, "is_expanded") and not drawer.is_expanded():
            if hasattr(drawer, "toggle"):
                drawer.toggle(expand=True)
        return card

    def _ui_begin_stream_card(self) -> None:
        """Слот главного потока Qt: открывает новую карточку для потокового вывода."""
        self._stream_ui_tokens = []
        self._active_stream_card = self._setup_stream_card()

    def _ui_on_stream_chunk(self, chunk: str) -> None:
        """Слот главного потока Qt: дописывает входящий токен в активную карточку чата."""
        self._stream_ui_tokens.append(chunk)
        card = self._active_stream_card
        if card is not None and hasattr(card, "set_text"):
            card.set_text("".join(self._stream_ui_tokens))

    def _ui_finish_stream_card(self, status: str) -> None:
        """Слот главного потока Qt: завершает потоковую карточку с указанным статусом."""
        card = self._active_stream_card
        if card is not None and hasattr(card, "set_status"):
            card.set_status(status)
        self._active_stream_card = None
        self._stream_ui_tokens = []

    def _stream_cognitive_response(self, text: str) -> str:
        """Потоковая генерация ответа через LLM с озвучиванием и неблокирующей живой печатью."""
        has_drawer = self._has_stream_drawer()
        if has_drawer:
            if self._is_gui_thread():
                self._ui_begin_stream_card()
            else:
                self.voice_bridge.stream_card_started.emit()

        accumulator = SentenceAccumulator()
        sentence_queue: queue.Queue[str | None] = queue.Queue()

        def _queue_iter() -> Iterator[str]:
            while True:
                item = sentence_queue.get()
                if item is None:
                    break
                yield item

        synthesizer = getattr(self.voice_pipeline, "synthesizer", None)
        can_speak = (
            self.enable_voice
            and not self.tray_manager.is_muted()
            and synthesizer is not None
            and hasattr(synthesizer, "speak_stream")
        )
        if can_speak and synthesizer is not None:
            synthesizer.speak_stream(_queue_iter())
            self._audio_streamed = True

        memory_ctx = self.memory_manager.format_system_prompt_context()
        if memory_ctx:
            base_sys = (
                "Ты автономный оператор Windows 11 GupkaCommander. Отвечай кратко на русском языке."
            )
            sys_prompt = f"{base_sys}\n\n{memory_ctx}"
            stream_gen = self.llm_client.generate_response_stream(text, system_prompt=sys_prompt)
        else:
            stream_gen = self.llm_client.generate_response_stream(text)

        tokens: list[str] = []
        try:
            for chunk in stream_gen:
                if self.failsafe_monitor.is_interrupted():
                    if synthesizer is not None and hasattr(synthesizer, "stop"):
                        synthesizer.stop()
                    if has_drawer:
                        if self._is_gui_thread():
                            self._ui_finish_stream_card("error")
                        else:
                            self.voice_bridge.stream_card_finished.emit("error")
                    raise InterruptedError("Операция экстренно прервана пользователем (FailSafe).")

                tokens.append(chunk)
                try:
                    self.voice_bridge.stream_chunk_received.emit(chunk)
                except Exception as exc:
                    logger.debug("Ошибка отправки stream_chunk_received: %s", exc)

                for sentence in accumulator.feed(chunk):
                    if can_speak:
                        sentence_queue.put(sentence)

            tail = accumulator.flush()
            if tail and can_speak:
                sentence_queue.put(tail)
        finally:
            if can_speak:
                sentence_queue.put(None)

        full_text = "".join(tokens).strip()
        if has_drawer:
            if self._is_gui_thread():
                self._ui_finish_stream_card("done")
            else:
                self.voice_bridge.stream_card_finished.emit("done")
            self._current_response_streamed = True

        return full_text

    def _handle_ai_cognitive(self, text: str) -> str:
        """Обработка свободного запроса через локальную модель LLM (планирование/ответ)."""
        if self.llm_client.is_healthy():
            memory_ctx = self.memory_manager.format_system_prompt_context()
            if memory_ctx:
                base_planner = (
                    "Ты планировщик действий операционной системы. Выбирай подходящие инструменты."
                )
                planner_sys = f"{base_planner}\n\n{memory_ctx}"
                actions = self.llm_client.plan_actions(text, system_prompt=planner_sys)
            else:
                actions = self.llm_client.plan_actions(text)
            if actions:
                return self._execute_planned_actions(actions)
            return self._stream_cognitive_response(text)

        lower = text.strip().lower()
        if any(w in lower for w in ("привет", "здравствуй", "добрый день")):
            return self._stream_cognitive_response(text)

        self.os_controller.search(text)
        return f"Команда не найдена локально. Выполняю поиск в сети: {text}"

    def _execute_planned_actions(self, actions: list[dict[str, Any]]) -> str:
        """Исполнение последовательности действий, сгенерированных LLM/VLM."""
        results: list[str] = []
        for action in actions:
            tool_name = str(action.get("tool", ""))
            parameters = action.get("parameters", {})
            if isinstance(parameters, dict):
                res = self._execute_planned_tool(tool_name, parameters)
                results.append(res)
        return " ".join(results) if results else "Действия не запланированы."

    def _execute_single_action(self, tool: str, params: dict[str, Any]) -> str:
        """Алиас для обратной совместимости с существующими тестами."""
        return self._execute_planned_tool(tool, params)

    def _execute_planned_tool(self, tool: str, params: dict[str, Any]) -> str:
        """Исполнение одного действия из списка системных инструментов."""
        if tool == "open_application":
            app_name = str(params.get("app_name", ""))
            apps = self.app_finder.find(app_name)
            if apps and self._launch_app(apps[0].path):
                return f"Запущено приложение: {apps[0].name}."
            return f"Приложение '{app_name}' не найдено."

        if tool == "open_url":
            url = str(params.get("url", ""))
            self.os_controller.open_url(url)
            return f"Открыт веб-сайт: {url}."

        if tool == "search_web":
            query = str(params.get("query", ""))
            engine = str(params.get("engine", "google"))
            if engine == "google":
                self.os_controller.search(query)
            else:
                self.os_controller.search(query, engine=engine)
            return f"Выполнен поиск в сети: {query}."

        if tool == "run_command":
            cmd = str(params.get("command", ""))
            code, stdout, stderr = self.os_controller.run_command(cmd)
            return f"Команда '{cmd}' выполнена." if code == 0 else f"Ошибка команды: {stderr}."

        if tool == "window_control":
            action = str(params.get("action", ""))
            target = str(params.get("target_window", ""))
            return self._execute_window_action(action, target)

        if tool == "click_screen":
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            btn = str(params.get("button", "left"))
            self.vision_agent.click_at(x, y, button=btn)
            return f"Клик по координатам ({x}, {y})."

        if tool == "type_text":
            txt = str(params.get("text", ""))
            self.vision_agent.type_text(txt)
            if params.get("press_enter"):
                self.vision_agent.press_key("enter")
            return f"Введен текст: '{txt}'."

        if tool == "inspect_screen":
            info = self.vision_agent.inspect_screen()
            return f"Снимок экрана: {info['width']}x{info['height']}."

        if tool == "click_ui_element":
            elem_name = str(params.get("element_name", ""))
            win_title = str(params.get("window_title", "")) or None
            ctrl_type = str(params.get("control_type", "")) or None
            if self.uia_manager.click_element(
                elem_name, window_title=win_title, control_type=ctrl_type
            ):
                target_str = f"'{elem_name}'" + (f" в окне '{win_title}'" if win_title else "")
                return f"Нажат элемент UI Automation {target_str}."

            logger.info(
                "Элемент '%s' не найден в UIA. Каскадный переход на VisionAgent.",
                elem_name,
            )
            return self._fallback_click_with_vision(elem_name, "")

        if tool == "type_into_ui_element":
            elem_name = str(params.get("element_name", ""))
            text_val = str(params.get("text", ""))
            win_title = str(params.get("window_title", "")) or None
            if self.uia_manager.type_into_element(elem_name, text_val, window_title=win_title):
                target_str = f"'{elem_name}'" + (f" в окне '{win_title}'" if win_title else "")
                return f"Введен текст '{text_val}' в элемент {target_str}."

            self.vision_agent.type_text(text_val)
            return f"Введен текст '{text_val}' через клавиатурный ввод."

        if tool == "inspect_ui_elements":
            win_title = str(params.get("window_title", "")) or None
            elements = self.uia_manager.inspect_window_elements(window_title=win_title)
            count = len(elements)
            target = f"в окне '{win_title}'" if win_title else "в активном окне"
            return f"Найдено {count} элементов UI Automation {target}."

        if tool == "store_user_fact":
            key = str(params.get("key", "")).strip()
            val = str(params.get("value", "")).strip()
            cat = str(params.get("category", "general")).strip()
            if not key or not val:
                return "Ошибка: пустой ключ или значение факта."
            self.memory_manager.set_fact(key, val, category=cat)
            return f"Факт '{key}' сохранен в память."

        if tool == "retrieve_user_facts":
            cat_query: str | None = str(params.get("category", "")).strip() or None
            facts = self.memory_manager.list_facts(category=cat_query)
            if not facts:
                return "Факты не найдены."
            return "Сохраненные факты: " + ", ".join(f"{k}: {v}" for k, v in facts.items()) + "."

        if tool == "add_user_note":
            content = str(params.get("content", "")).strip()
            if not content:
                return "Ошибка: пустой текст заметки."
            nid = self.memory_manager.add_note(content)
            return f"Заметка #{nid} добавлена."

        if tool == "list_user_notes":
            notes_query: str | None = str(params.get("query", "")).strip() or None
            notes = self.memory_manager.list_notes(only_active=True, query=notes_query)
            if not notes:
                return "Заметки не найдены."
            return "Заметки: " + "; ".join(f"#{n['id']} {n['content']}" for n in notes) + "."

        if tool == "delete_user_note":
            nid = int(params.get("note_id", 0))
            ok = self.memory_manager.delete_note(nid)
            return f"Заметка #{nid} удалена." if ok else f"Заметка #{nid} не найдена."

        return f"Инструмент '{tool}' не поддерживается."

    def _execute_window_action(self, action: str, target: str) -> str:
        """Исполнение действия с окном или системной громкостью."""
        if action == "volume_up":
            self.os_controller.volume_up()
            return "Громкость увеличена."
        if action == "volume_down":
            self.os_controller.volume_down()
            return "Громкость уменьшена."
        if action == "mute":
            self.os_controller.volume_mute()
            return "Звук переключен."
        if action == "minimize":
            self.os_controller.minimize_window(target)
            return "Окно свернуто."
        if action == "maximize":
            self.os_controller.maximize_window(target)
            return "Окно развернуто."
        if action == "close":
            self.os_controller.close_window(target)
            return "Окно закрыто."
        if action == "restore":
            self.os_controller.focus_window(target)
            return "Окно восстановлено."
        if action == "minimize_all":
            self.os_controller.minimize_window("")
            self.os_controller.minimize_all()
            return "Все окна свернуты."
        return f"Действие с окном '{action}' выполнено."

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
        try:
            if hasattr(self.voice_pipeline, "set_muted"):
                self.voice_pipeline.set_muted(muted)
        except Exception as exc:
            logger.warning("Ошибка установки режима mute в VoicePipeline: %s", exc)

        if muted:
            try:
                synth = getattr(self.voice_pipeline, "synthesizer", None)
                if synth is not None and hasattr(synth, "stop"):
                    synth.stop()
            except Exception as exc:
                logger.debug("Ошибка при остановке синтезатора: %s", exc)

    def open_dependency_manager(self) -> None:
        """Открывает диалог управления локальными моделями и зависимостями."""
        from gui.setup_dialog import DependencySetupDialog

        dialog = DependencySetupDialog(manage_mode=True)
        dialog.exec()

    def open_settings_dialog(self) -> None:
        """Открывает окно системных настроек GupkaCommander."""
        from gui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(on_open_models=self.open_dependency_manager)
        dialog.exec()

    def start(self, start_hidden: bool = False) -> None:
        """
        Запуск всех подсистем приложения.

        Args:
            start_hidden: Если True, окно SpotlightBar не отображается при старте
                          (тихий запуск в системный трей).
        """
        logger.info("Запуск подсистем GupkaCommander (start_hidden=%s)...", start_hidden)

        # 0. Монитор безопасности FailSafe
        try:
            self.failsafe_monitor.start()
        except Exception as exc:
            logger.warning("Не удалось запустить FailSafeMonitor: %s", exc)

        # 0.5. Автозапуск локального llama-server.exe в фоне (если скачан VLM)
        if self.enable_llm_autostart:
            threading.Thread(
                target=self.llama_manager.start,
                name="LlamaServerAutostart",
                daemon=True,
            ).start()

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
        if not start_hidden:
            self.spotlight_bar.show_bar()
        logger.info("GupkaCommander успешно запущен и готов к работе.")

    def shutdown(self) -> None:
        """Корректное завершение работы и освобождение системных ресурсов."""
        if self._is_shutting_down:
            return
        self._is_shutting_down = True
        logger.info("Остановка GupkaCommander...")

        # 0. Остановка активного фонового воркера команд
        worker = self._command_worker
        if worker is not None and worker.isRunning():
            try:
                self.failsafe_monitor.trigger_abort("Application shutdown")
            except Exception:
                pass
            worker.wait(1500)
        self._command_worker = None

        # 0.5. Остановка монитора безопасности FailSafe
        try:
            self.failsafe_monitor.stop()
        except Exception as exc:
            logger.debug("Ошибка при остановке FailSafeMonitor: %s", exc)

        # 1. Остановка управляемого процесса llama-server.exe
        try:
            self.llama_manager.stop()
        except Exception as exc:
            logger.debug("Ошибка при остановке LlamaServerManager: %s", exc)

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

        try:
            self.memory_manager.close()
        except Exception as exc:
            logger.debug("Ошибка при закрытии базы данных памяти: %s", exc)

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
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Запустить приложение свернутым в системный трей без показа SpotlightBar",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Принудительно открыть окно проверки и загрузки локальных моделей при старте",
    )
    parser.add_argument(
        "--skip-setup",
        action="store_true",
        help="Пропустить автоматическую проверку и диалог загрузки моделей при старте",
    )
    return parser.parse_args()


def main() -> int:
    """Точка входа запуска приложения GupkaCommander."""
    # 0. Инициализация Per-Monitor DPI Awareness до создания контекста Qt
    from core.vision.screen_capture import set_dpi_awareness

    set_dpi_awareness()

    args = parse_arguments()

    if args.headless or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(sys.argv)

    icon_path = get_icon_path()
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Проверка и загрузка недостающих моделей с разрешения пользователя до инициализации ядра
    from gui.setup_dialog import run_startup_dependency_check

    run_startup_dependency_check(
        force_setup=bool(args.setup),
        skip_setup=bool(args.skip_setup),
        headless=bool(args.headless),
        quiet=bool(args.quiet),
        enable_voice=not args.no_voice,
    )

    commander = GupkaCommanderApp(
        app=app,
        enable_voice=not args.no_voice,
        enable_hotkey=not args.no_hotkey,
        enable_tray=not args.no_tray,
        enable_llm_autostart=not args.headless,
        async_execution=not args.headless,
    )

    commander.start(start_hidden=bool(args.quiet))

    # В headless режиме не блокируем процесс бесконечно
    if args.headless:
        commander.shutdown()
        return 0

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
