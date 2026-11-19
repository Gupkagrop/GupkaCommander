# Project Living State Tracker (TASK.md)

> [!NOTE]
> **Назначение файла:** Живой журнал состояния проекта GupkaCommander для сквозной памяти между чатами ("Zero-Context-Amnesia").
> Обновляется агентом в конце каждого выполненного этапа или задачи (Stage 4 SOP).

## 1. Project Health & Stage
- **Current Phase:** Phase 3: Voice Pipeline on CPU (Completed)
- **Status:** Sprint 2 Done / Ready for Sprint 3 (PySide6 Spotlight Bar GUI)
- **Last Updated:** 2026-11-19 20:18
- **Active Branch:** `main`

## 2. Completed Milestones
- [x] **2026-11-19 Исследование и архитектурное проектирование:** Проведен глубокий анализ подходов к автономным Computer-Use агентам (UI-TARS, Qwen2.5-VL, OmniParser v2, Fast-Path). Разработана 4-уровневая архитектура (Fast-Path -> UIA -> Browser Bridge -> Vision Fallback), спроектирован CPU-контур для голоса (VAD + Whisper + Piper) и подготовлены базовые спецификации. (Commit: `26b4609`)
- [x] **2026-11-19 Глубокий анализ и утверждение идеального стека:** Проведен сравнительный анализ моделей и библиотек под профиль RTX 5050 (8GB VRAM) / i5-14450HX. Утверждена связка: PySide6 (GUI) + Silero VAD/Faster-Whisper/Piper TTS (CPU) + Qwen2.5-VL-3B-Instruct Q4 через llama-server (GPU ~2.6GB VRAM) + Windows Registry/UIA/mss/rapidfuzz. Документ зафиксирован в `docs/architecture/ideal_stack_analysis.md`. (Commit: `077c51c`)
- [x] **2026-11-19 Ребрендинг в GupkaCommander и оформление репозитория:** Утверждено название проекта GupkaCommander. Сформирован конфигурационный файл `pyproject.toml`, подготовлена эталонная структура каталогов (`core/`, `gui/`, `tests/`), обновлены манифесты и презентационный `README.md`. Репозиторий на GitHub переименован в `Gupkagrop/GupkaCommander`, обновлен git remote origin. (Commit: `3981f4c`)
- [x] **2026-11-19 Реализация Спринта 1 (Fast-Path Core):** Роем автономных агентов (`app-finder`, `os-controller`, `failsafe`, `verifier`) реализовано ядро автоматизации: сканер реестра/.lnk с rapidfuzz (`core/app_finder.py`), исполнитель веб/шелл/win32 (`core/os_controller.py`), монитор прерываний Esc и Mouse Shake (`core/failsafe.py`). Написаны 74 модульных и интеграционных теста (`tests/`), 100% тестов пройдены успешно, 0 ошибок mypy strict и ruff. (Commit: `252b05e`)
- [x] **2026-11-19 Реализация Спринта 2 (Voice Pipeline на CPU):** Роем автономных агентов (`audio-vad`, `stt-tts`, `pipeline-orch`, `verifier-2`) реализован полный речевой контур на CPU с нулевым расходом VRAM: захват 16 кГц PCM с кольцевым буфером (`AudioCapture`), Silero VAD v5 с отсечением пауз (`VADDetector`), CTranslate2 Faster-Whisper INT8 (`FasterWhisperTranscriber`), многоуровневый Piper TTS синтез (`PiperSynthesizer`) и оркестратор `VoicePipeline` с мгновенным перебиванием речи (Barge-In). Написаны 50 новых тестов (всего 124 теста в проекте), 100% тестов пройдены успешно, 0 ошибок mypy strict и ruff. (Commit: `a8afab5`)

## 3. Active Task (Current Focus)
- **Feature:** Завершение Спринта 2 и фиксация изменений в репозитории GitHub.
- **Target Files:**
  - `[core/voice/audio_capture.py](file:///c:/Users/denis/Documents/antigravity/localAgent/core/voice/audio_capture.py)`
  - `[core/voice/vad_detector.py](file:///c:/Users/denis/Documents/antigravity/localAgent/core/voice/vad_detector.py)`
  - `[core/voice/transcriber.py](file:///c:/Users/denis/Documents/antigravity/localAgent/core/voice/transcriber.py)`
  - `[core/voice/synthesizer.py](file:///c:/Users/denis/Documents/antigravity/localAgent/core/voice/synthesizer.py)`
  - `[core/voice/voice_pipeline.py](file:///c:/Users/denis/Documents/antigravity/localAgent/core/voice/voice_pipeline.py)`
  - `[core/voice/__init__.py](file:///c:/Users/denis/Documents/antigravity/localAgent/core/voice/__init__.py)`
  - `[tests/test_audio_capture.py](file:///c:/Users/denis/Documents/antigravity/localAgent/tests/test_audio_capture.py)`
  - `[tests/test_vad_detector.py](file:///c:/Users/denis/Documents/antigravity/localAgent/tests/test_vad_detector.py)`
  - `[tests/test_transcriber.py](file:///c:/Users/denis/Documents/antigravity/localAgent/tests/test_transcriber.py)`
  - `[tests/test_synthesizer.py](file:///c:/Users/denis/Documents/antigravity/localAgent/tests/test_synthesizer.py)`
  - `[tests/test_voice_pipeline.py](file:///c:/Users/denis/Documents/antigravity/localAgent/tests/test_voice_pipeline.py)`
- **Verification Plan:** `uv run pytest tests/ -v`, `uv run ruff check .`, `uv run mypy core tests`.

## 4. Next Immediate Steps (Backlog)
- [ ] Спринт 3: Разработка графического интерфейса Spotlight Bar на PySide6 (Windows 11 Fluent/Mica) и системного трея.
- [ ] Спринт 4: Подключение локального LLM/VLM инференса (Qwen2.5-VL-3B через llama-server) и двухуровневого роутера.

## 3. Active Task (Current Focus)
- **Feature:** Формирование архитектурной базы и спецификаций без написания кода реализации.
- **Target Files:**
  - `[AGENTS.md](file:///c:/Users/denis/Documents/antigravity/localAgent/AGENTS.md)`
  - `[TASK.md](file:///c:/Users/denis/Documents/antigravity/localAgent/TASK.md)`
  - `[README.md](file:///c:/Users/denis/Documents/antigravity/localAgent/README.md)`
  - `[docs/architecture/system_overview.md](file:///c:/Users/denis/Documents/antigravity/localAgent/docs/architecture/system_overview.md)`
  - `[docs/architecture/fast_path_spec.md](file:///c:/Users/denis/Documents/antigravity/localAgent/docs/architecture/fast_path_spec.md)`
  - `[docs/architecture/voice_pipeline.md](file:///c:/Users/denis/Documents/antigravity/localAgent/docs/architecture/voice_pipeline.md)`
  - `[docs/architecture/vision_and_routing.md](file:///c:/Users/denis/Documents/antigravity/localAgent/docs/architecture/vision_and_routing.md)`
- **Verification Plan:** Проверка соответствия шаблонам из `C:\Users\denis\.gemini\config\templates`, валидация ссылок и генерация AST-карты Repomix.

## 4. Next Immediate Steps (Backlog)
- [ ] Task 1: Генерация сжатого AST-слепка через `npx repomix --compress` для верификации структуры.
- [ ] Task 2: Фиксация архитектурной документации в Git (коммит на русском языке).
- [ ] Task 3: Согласование с пользователем перехода к Фазе 2 (реализация Fast-Path и сканера приложений).

## 5. Architectural Decisions & Blockers
- **Decisions Log:**
  - `2026-11-19`: Принято решение разделить управление на 4 уровня (Fast-Path -> UIA -> Browser -> Vision), чтобы избавиться от задержек чистого компьютерного зрения для тривиальных действий (открытие софта, консоли, сайтов).
  - `2026-11-19`: Весь голосовой стек (Silero VAD, Faster-Whisper, Piper TTS) размещен на CPU, чтобы освободить весь 8GB VRAM буфер под компактную мультимодальную модель (Qwen2.5-VL-3B Q4).
  - `2026-11-19`: Интегрированы шаблоны проектов из `C:\Users\denis\.gemini\config\templates` для единообразия стандартов.
- **Known Blockers / Warnings:**
  - Код реализации пока не пишем — этап зафиксирован на концепции, архитектуре и документации.
