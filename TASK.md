# Project Living State Tracker (TASK.md)

> [!NOTE]
> **Назначение файла:** Живой журнал состояния проекта GupkaCommander для сквозной памяти между чатами ("Zero-Context-Amnesia").
> Обновляется агентом в конце каждого выполненного этапа или задачи (Stage 4 SOP).

## 1. Project Health & Stage
- **Current Phase:** Phase 5: Complete Desktop & Cognitive Core Implementation (Done)
- **Status:** All Sprints Complete / Ready for Production Use & Live Deployment
- **Last Updated:** 2026-11-19 21:07
- **Active Branch:** `main`

## 2. Completed Milestones
- [x] **2026-11-19 Исследование и архитектурное проектирование:** Проведен глубокий анализ подходов к автономным Computer-Use агентам (UI-TARS, Qwen2.5-VL, OmniParser v2, Fast-Path). Разработана 4-уровневая архитектура (Fast-Path -> UIA -> Browser Bridge -> Vision Fallback), спроектирован CPU-контур для голоса (VAD + Whisper + Piper) и подготовлены базовые спецификации. (Commit: `26b4609`)
- [x] **2026-11-19 Глубокий анализ и утверждение идеального стека:** Проведен сравнительный анализ моделей и библиотек под профиль RTX 5050 (8GB VRAM) / i5-14450HX. Утверждена связка: PySide6 (GUI) + Silero VAD/Faster-Whisper/Piper TTS (CPU) + Qwen2.5-VL-3B-Instruct Q4 через llama-server (GPU ~2.6GB VRAM) + Windows Registry/UIA/mss/rapidfuzz. Документ зафиксирован в `docs/architecture/ideal_stack_analysis.md`. (Commit: `077c51c`)
- [x] **2026-11-19 Ребрендинг в GupkaCommander и оформление репозитория:** Утверждено название проекта GupkaCommander. Сформирован конфигурационный файл `pyproject.toml`, подготовлена эталонная структура каталогов (`core/`, `gui/`, `tests/`), обновлены манифесты и презентационный `README.md`. Репозиторий на GitHub переименован в `Gupkagrop/GupkaCommander`, обновлен git remote origin. (Commit: `3981f4c`)
- [x] **2026-11-19 Реализация Спринта 1 (Fast-Path Core):** Роем автономных агентов (`app-finder`, `os-controller`, `failsafe`, `verifier`) реализовано ядро автоматизации: сканер реестра/.lnk с rapidfuzz (`core/app_finder.py`), исполнитель веб/шелл/win32 (`core/os_controller.py`), монитор прерываний Esc и Mouse Shake (`core/failsafe.py`). Написаны 74 модульных и интеграционных теста (`tests/`), 100% тестов пройдены успешно, 0 ошибок mypy strict и ruff. (Commit: `252b05e`)
- [x] **2026-11-19 Реализация Спринта 2 (Voice Pipeline на CPU):** Роем автономных агентов (`audio-vad`, `stt-tts`, `pipeline-orch`, `verifier-2`) реализован полный речевой контур на CPU с нулевым расходом VRAM: захват 16 кГц PCM с кольцевым буфером (`AudioCapture`), Silero VAD v5 с отсечением пауз (`VADDetector`), CTranslate2 Faster-Whisper INT8 (`FasterWhisperTranscriber`), многоуровневый Piper TTS синтез (`PiperSynthesizer`) и оркестратор `VoicePipeline` с мгновенным перебиванием речи (Barge-In). Написаны 50 новых тестов (всего 124 теста в проекте), 100% тестов пройдены успешно, 0 ошибок mypy strict и ruff. (Commit: `a8afab5`)
- [x] **2026-11-19 Реализация Спринта 3 (PySide6 GUI & System Tray):** Роем автономных агентов (`ui-components`, `spotlight-app`, `verifier-3`) реализован графический интерфейс в стиле Windows 11 Fluent Dark: плавающий безорамочный `SpotlightBar` с вызовом по `Alt+Space` через Win32 `RegisterHotKey`, анимированный эквалайзер речевой активности `WaveformWidget`, выдвижное полотно карточек истории `ChatDrawer`, системный трей с динамической индикацией `TrayManager` и точка входа `main.py` (`GupkaCommanderApp`) с потокобезопасным мостом `VoiceBridge`. Написаны 60 новых тестов (всего 184 теста в проекте), 100% тестов пройдены успешно, 0 ошибок mypy strict и ruff. (Commit: `d396cd7`)
- [x] **2026-11-19 Реализация Спринта 4 (Cognitive Brain, Routing & Vision Grounding):** Роем автономных агентов (`router-brain`, `vision-grounding`, `verifier-4`) реализовано когнитивное ядро и координатное зрение: двухуровневый маршрутизатор с Fast-Path First (`IntentRouter`), OpenAI-совместимый клиент `LocalLLMClient` с поддержкой 8 системных инструментов и автономным `FallbackRuleEngine`, модуль захвата экрана `ScreenCapture` (mss + Win32 DPI awareness + dhash) и координатный агент `VisionAgent` с плавным S-curve наведением курсора, верификацией изменения кадра и безопасным прерыванием по Esc. Написано 103 новых теста (всего 287 тестов в проекте), 100% тестов пройдены успешно, 0 ошибок mypy strict и ruff. (Commit: `32794c4`)
- [x] **2026-11-19 Реализация Спринта 5 (Deployment, Models Setup & Windows Autostart):** Роем автономных агентов (`packager`, `model-fetcher`, `verifier-5`, `monitor-5`) реализованы утилиты дистрибуции и автономного развертывания: скрипт загрузки весов моделей с докачкой (`scripts/download_models.py` для Silero VAD, Piper TTS ru_RU, Whisper и Qwen2.5-VL GGUF), генератор многослойной Fluent Dark иконки `assets/gupka_commander.ico` (`scripts/generate_icon.py`), скрипт создания Windows-ярлыков на Рабочем столе и в автозагрузке (`scripts/create_shortcut.py`), скрипты запуска `llama-server.exe` под NVIDIA RTX 5050 (`scripts/run_llama_server.bat` и `.ps1`) и интеграция локальных путей моделей в ядро и GUI. Написано 44 новых теста (всего 331 тест в проекте), 100% тестов пройдены успешно, 0 ошибок mypy strict и ruff.

## 3. Active Task (Current Focus)
- **Feature:** Все 5 спринтов проекта GupkaCommander полностью реализованы, протестированы (331/331 green) и готовы к работе.
- **Status:** Проект готов к полноценному повседневному использованию на Windows 11.
- **Verification Plan:** `uv run pytest tests/ -q`, `uv run ruff check .`, `uv run mypy core gui tests scripts main.py`.

## 4. Next Immediate Steps (Backlog)
- [x] Разработка утилит развертывания (`scripts/download_models.py`, `scripts/run_llama_server.bat`).
- [x] Создание ярлыка запуска на Рабочем столе и в автозагрузке Windows (`scripts/create_shortcut.py`).
- [ ] Опционально: запуск скрипта скачивания моделей `uv run python scripts/download_models.py --voice-only` пользователем.
- [ ] Запуск приложения `uv run python main.py` для живого взаимодействия.

## 5. Architectural Decisions & Blockers
- **Decisions Log:**
  - `2026-11-19`: Принято решение разделить управление на 4 уровня (Fast-Path -> UIA -> Browser -> Vision), чтобы избавиться от задержек чистого компьютерного зрения для тривиальных действий (открытие софта, консоли, сайтов).
  - `2026-11-19`: Весь голосовой стек (Silero VAD, Faster-Whisper, Piper TTS) размещен на CPU, чтобы освободить весь 8GB VRAM буфер под компактную мультимодальную модель (Qwen2.5-VL-3B Q4).
  - `2026-11-19`: Реализован интерфейс Windows 11 Fluent Dark с мягкими скруглениями (14px) и выдвижной панелью истории (Chat Drawer).
  - `2026-11-19`: Вызов Spotlight Bar привязан к нативному Win32 `RegisterHotKey` (Alt+Space) с автоцентрированием на активном мониторе и возвратом фокуса.
  - `2026-11-19`: Реализовано когнитивное зрение и планирование действий через OpenAI-совместимый протокол со встроенным FallbackRuleEngine для автономной работы без активного LLM-сервера.
  - `2026-11-19`: Все модели локализованы в директории `models/` корня репозитория для 100% автономности и переносимости; сгенерирована фирменная Windows-иконка `assets/gupka_commander.ico`.
- **Known Blockers / Warnings:**
  - Нет блокеров. Все 5 спринтов полностью завершены и протестированы (331/331 тест зелёный).
