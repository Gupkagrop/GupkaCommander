# Project Operational Guidelines (AGENTS.md)

> [!NOTE]
> **Назначение файла:** Контракт правил и архитектурных стандартов для агентов в проекте GupkaCommander.
> Агент считывает этот файл автоматически при открытии рабочей области.

## 1. Project Overview & Tech Stack
- **Project Name:** GupkaCommander
- **Target Platform:** Windows 11 Desktop (GPU: NVIDIA RTX 5050 Laptop 8GB VRAM, CPU: Intel i5-14450HX, RAM: 16GB)
- **Stack:** Python 3.12+ / PySide6 (Qt 6 Fluent UI) / Windows Win32 API & UI Automation (UIA)
- **Voice Stack (CPU):** Silero VAD (ONNX) / Faster-Whisper INT8 (CTranslate2) / Piper TTS (ONNX)
- **Cognitive Core (GPU):** 4-bit Quantized Local VLM/LLM (Qwen3-VL-4B-Instruct Q4_K_M + mmproj Q8_0)
- **Package Manager & Runtimes:** `uv` / Python 3.12+

## 2. Standard Commands (Harness Sensors)
- **Install Dependencies:** `uv sync` / `uv pip install -e .`
- **Build / Lint:** `uv run ruff check .`
- **Format:** `uv run ruff format .`
- **Run Development:** `uv run python main.py`
- **Run Tests:** `uv run pytest`
- **Typecheck:** `uv run mypy core`

## 3. Session Continuity & Orientation Protocol
- **Every new chat/task MUST start with:**
  1. `git status` and `git log -n 5 --oneline` (review recent commits).
  2. Reading `TASK.md` in repository root to determine active phase, immediate backlog, and recent decisions.
  3. Reading `repomix-output.xml` if inspecting repository architecture.

## 4. Coding & Architecture Standards
- Early returns: keep nesting depth $\le 3$.
- Functions $\le 30-40$ lines.
- Zero implicit `any` or loose types; explicit typing everywhere (`mypy` strict).
- All code identifiers in English.
- All code comments, docstrings, and Git commit messages strictly in Russian.
- Zero hardcoded secrets: use `.env` files.
- **Fail-Safe Invariant:** Any OS automation loop MUST respect immediate abortion via `Esc` key or mouse shake.
- **Process Isolation Invariant:** Never run audio capture or heavy PyTorch/VLM models on the GUI thread.

## 5. Pre-Commit Checklist
- [ ] Automated tests run and pass green (`uv run pytest`).
- [ ] Static analyzer / linter passes with zero warnings (`uv run ruff check .`).
- [ ] `TASK.md` updated with completed milestone and commit hash.
- [ ] Conventional Commit authored in Russian (e.g. `feat: ...` / `docs: ...`).
