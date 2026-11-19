# Project Living State Tracker (TASK.md)

> [!NOTE]
> **Назначение файла:** Живой журнал состояния проекта GupkaCommander для сквозной памяти между чатами ("Zero-Context-Amnesia").
> Обновляется агентом в конце каждого выполненного этапа или задачи (Stage 4 SOP).

## 1. Project Health & Stage
- **Current Phase:** Phase 2: Fast-Path Core Implementation (Completed)
- **Status:** Sprint 1 Done / Ready for Sprint 2
- **Last Updated:** 2026-11-19 19:46
- **Active Branch:** `main`

## 2. Completed Milestones
- [x] **2026-11-19 Исследование и архитектурное проектирование:** Проведен глубокий анализ подходов к автономным Computer-Use агентам (UI-TARS, Qwen2.5-VL, OmniParser v2, Fast-Path). Разработана 4-уровневая архитектура (Fast-Path -> UIA -> Browser Bridge -> Vision Fallback), спроектирован CPU-контур для голоса (VAD + Whisper + Piper) и подготовлены базовые спецификации. (Commit: `26b4609`)
- [x] **2026-11-19 Глубокий анализ и утверждение идеального стека:** Проведен сравнительный анализ моделей и библиотек под профиль RTX 5050 (8GB VRAM) / i5-14450HX. Утверждена связка: PySide6 (GUI) + Silero VAD/Faster-Whisper/Piper TTS (CPU) + Qwen2.5-VL-3B-Instruct Q4 через llama-server (GPU ~2.6GB VRAM) + Windows Registry/UIA/mss/rapidfuzz. Документ зафиксирован в `docs/architecture/ideal_stack_analysis.md`. (Commit: `077c51c`)
- [x] **2026-11-19 Ребрендинг в GupkaCommander и оформление репозитория:** Утверждено название проекта GupkaCommander. Сформирован конфигурационный файл `pyproject.toml`, подготовлена эталонная структура каталогов (`core/`, `gui/`, `tests/`), обновлены манифесты и презентационный `README.md`. Репозиторий на GitHub переименован в `Gupkagrop/GupkaCommander`, обновлен git remote origin. (Commit: `3981f4c`)
- [x] **2026-11-19 Реализация Спринта 1 (Fast-Path Core):** Роем автономных агентов (`app-finder`, `os-controller`, `failsafe`, `verifier`) реализовано ядро автоматизации: сканер реестра/.lnk с rapidfuzz (`core/app_finder.py`), исполнитель веб/шелл/win32 (`core/os_controller.py`), монитор прерываний Esc и Mouse Shake (`core/failsafe.py`). Написаны 74 модульных и интеграционных теста (`tests/`), 100% тестов пройдены успешно, 0 ошибок mypy strict и ruff. (Commit: `252b05e`)

## 3. Active Task (Current Focus)
- **Feature:** Завершение Спринта 1 и фиксация изменений в репозитории GitHub.
- **Target Files:**
  - `[core/app_finder.py](file:///c:/Users/denis/Documents/antigravity/localAgent/core/app_finder.py)`
  - `[core/os_controller.py](file:///c:/Users/denis/Documents/antigravity/localAgent/core/os_controller.py)`
  - `[core/failsafe.py](file:///c:/Users/denis/Documents/antigravity/localAgent/core/failsafe.py)`
  - `[tests/test_app_finder.py](file:///c:/Users/denis/Documents/antigravity/localAgent/tests/test_app_finder.py)`
  - `[tests/test_os_controller.py](file:///c:/Users/denis/Documents/antigravity/localAgent/tests/test_os_controller.py)`
  - `[tests/test_failsafe.py](file:///c:/Users/denis/Documents/antigravity/localAgent/tests/test_failsafe.py)`
  - `[tests/test_integration_fast_path.py](file:///c:/Users/denis/Documents/antigravity/localAgent/tests/test_integration_fast_path.py)`
- **Verification Plan:** `uv run pytest tests/ -v`, `uv run ruff check .`, `uv run mypy core tests`.

## 4. Next Immediate Steps (Backlog)
- [ ] Спринт 2: Реализация голосового контура на CPU (Silero VAD v5 + Faster-Whisper INT8 + Piper TTS + Barge-In).
- [ ] Спринт 3: Разработка графического интерфейса Spotlight Bar на PySide6 (Windows 11 Fluent/Mica) и системного трея.
- [ ] Спринт 4: Подключение локального LLM/VLM инференса и двухуровневого роутера.

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
