<div align="center">

# 🎖️ GupkaCommander

**Автономный локальный командный центр и оператор Windows 11**

[![Python Version](https://img.shields.io/badge/Python-3.12%2B-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%2011-0078D4.svg?logo=windows11&logoColor=white)](https://www.microsoft.com/windows)
[![Privacy](https://img.shields.io/badge/Privacy-100%25%20Local-success.svg)](#-принципы-проекта)
[![Framework](https://img.shields.io/badge/GUI-PySide6%20%28Qt%206%29-41CD52.svg?logo=qt&logoColor=white)](https://www.qt.io/)
[![Hardware Profile](https://img.shields.io/badge/Hardware-RTX%205050%20%7C%208GB%20VRAM-green.svg)](#-аппаратный-профиль-и-бюджет-памяти)
[![License](https://img.shields.io/badge/License-MIT-purple.svg)](LICENSE)

<p align="center">
  <b>Приватность • 100% Офлайн-инференс • Голосовой контур (CPU) • Быстрый Fast-Path • Экранное зрение (VLM)</b>
</p>

</div>

---

> [!NOTE]
> **GupkaCommander** — это персональный ИИ-оператор компьютера, работающий автономно на вашем ПК под управлением Windows 11.  
> Ассистент принимает команды голосом или через плавающий чат (Spotlight), мгновенно запускает любые установленные приложения, открывает нужные страницы в браузере, исполняет консольные команды и взаимодействует с интерфейсом через нативные API и локальную мультимодальную модель.

---

## ⚡ Ключевые возможности

* 🎙️ **Офлайн-диалог без задержек (100% CPU):** Речевой контур (`Silero VAD v5`, `Faster-Whisper INT8`, `Piper TTS`) полностью вынесен на центральный процессор. Видеопамять свободна, время синтеза речи — менее 50 мс.
* ⚡ **Мгновенный Fast-Path (< 20 мс):** 80% типовых действий (открытие софта, навигация по сайтам, поиск в Google/YouTube, громкость, консоль) выполняются за миллисекунды через прямой доступ к реестру Windows, ярлыкам меню «Пуск» и `rapidfuzz` без обращения к тяжелым нейросетям.
* 🪟 **Семантическая автоматизация (UI Automation):** Взаимодействие со стандартными окнами программ без перемещения курсора мыши — клики и ввод текста по внутренним `AutomationId` элементов.
* 👁️ **Визуальное зрение (Vision Fallback):** Компактная модель `Qwen2.5-VL-3B-Instruct` (Q4_K_M) используется для анализа нестандартных GUI, графических холстов и браузерных медиаплееров.
* 🛡️ **Аварийный выключатель (Emergency Kill-Switch):** Мгновенная остановка любых автономных действий агента по нажатию `Esc` или при резком движении мыши (Mouse Shake).

---

## 🏗️ Архитектура системы

```mermaid
flowchart TD
    subgraph Ввод["Источники команд"]
        A1["🎙️ Голосовой поток (Микрофон)"]
        A2["💬 Плавающий Spotlight Bar (PySide6)"]
        A3["⌨️ Глобальные горячие клавиши (Alt+Space / Esc)"]
    end

    subgraph ГолосовойКонтур["Голосовой контур (100% CPU, 0 MB VRAM)"]
        B1["Silero VAD v5 (Детекция речи)"]
        B2["Faster-Whisper (Распознавание речи, INT8)"]
        B3["Piper TTS (Синтез русской речи, ONNX)"]
    end

    subgraph Маршрутизация["Интеллектуальное ядро"]
        C1{"Маршрутизатор намерений"}
        C2["🧠 Локальная VLM / LLM (Qwen2.5-VL-3B Q4)"]
    end

    subgraph Исполнение["4-уровневая пирамида автоматизации"]
        D0["⚡ Tier 0: Fast-Path (Реестр, Shell, Win32) ~10-20 мс"]
        D1["🪟 Tier 1: Windows UI Automation ~50-150 мс"]
        D2["🌐 Tier 2: Browser Bridge (CDP / DOM) ~50-100 мс"]
        D3["👁️ Tier 3: Vision Grounding (mss + клик) ~1-2 сек"]
    end

    A1 --> B1 --> B2 --> C1
    A2 --> C1
    A3 --> C1

    C1 -- "Прямые команды (софт, сайты, команды)" --> D0
    C1 -- "Сложные сценарии" --> C2

    C2 --> D0
    C2 --> D1
    C2 --> D2
    C2 --> D3

    D0 & D1 & D2 & D3 --> B3
```

---

## 📊 Матрица уровней исполнения (4-Tier Pyramid)

| Уровень | Технологический стек | Сценарии использования | Задержка отклика | Расход VRAM |
| :--- | :--- | :--- | :--- | :--- |
| **Tier 0: Fast-Path** | Windows Registry, `.lnk` парсер, `rapidfuzz`, Win32 API, `os.startfile` | Запуск Telegram, Chrome, терминала, переход на YouTube, регулировка звука | **< 20 мс** | **0 МБ** |
| **Tier 1: Semantic UIA** | `uiautomation` (Microsoft UI Automation COM) | Клик по кнопкам в Блокноте, Проводнике, окнах настроек, ввод текста по ID | **~50–150 мс** | **0 МБ** |
| **Tier 2: Browser Bridge** | Chrome DevTools Protocol (CDP) / Playwright | Заполнение веб-форм, поиск видео, клик по ссылкам на страницах | **~50–100 мс** | **0 МБ** |
| **Tier 3: Vision Fallback** | `mss` + `Qwen2.5-VL-3B-Instruct` (Q4_K_M) + `pyautogui` | Сложные неразмеченные интерфейсы, видеоплееры, игры, нестандартный GUI | **~1–2 сек** | **~2.6 ГБ** |

---

## 💻 Аппаратный профиль и бюджет памяти

Конфигурация оптимизирована для мобильных и настольных рабочих станций с ограниченным объемом видеопамяти:

* **Целевой GPU:** NVIDIA GeForce RTX 5050 (8 151 МБ VRAM)
* **Целевой CPU:** Intel Core i5-14450HX (10 ядер, 16 потоков)
* **Оперативная память:** 16 ГБ RAM

| Компонент | Вычислитель | Потребление памяти |
| :--- | :--- | :--- |
| **ОС Windows 11 + дисплеи** | GPU / RAM | ~800–1 000 МБ VRAM / ~6–7 ГБ RAM |
| **Silero VAD + Faster-Whisper + Piper TTS** | CPU | **0 МБ VRAM** / ~350 МБ RAM |
| **Qwen2.5-VL-3B-Instruct (Q4_K_M GGUF)** | GPU | **~2 500–2 800 МБ VRAM** / ~200 МБ RAM |
| **PySide6 GUI + Fast-Path кэш** | CPU / DWM | **0 МБ VRAM** / ~90 МБ RAM |
| **Резерв видеопамяти под буферы** | GPU | **> 4 000 МБ VRAM (Свободно)** |

---

## 📂 Организация репозитория

```text
GupkaCommander/
├── .gitignore                      # Исключения бинарников, моделей, venv
├── .repomixignore                  # Правила упаковки AST-сборки Repomix
├── AGENTS.md                       # Контракт разработки и правила для ИИ
├── TASK.md                         # Журнал состояния и сквозной памяти (SOP)
├── README.md                       # Презентация и архитектура проекта
├── pyproject.toml                  # Конфигурация uv, ruff, mypy, pytest
├── .env.example                    # Документация переменных окружения
├── config/
│   ├── settings.json               # Параметры моделей, хоткеев и звука
│   └── commands.json               # Базовые алиасы программ и сайтов
├── docs/                           # Инженерная документация
│   └── architecture/
│       ├── system_overview.md      # Обзор архитектуры ядра и IPC
│       ├── ideal_stack_analysis.md # Исследование и обоснование стека
│       ├── fast_path_spec.md       # Спецификация детерминированного запуска
│       ├── voice_pipeline.md       # Спецификация голосового контура (CPU)
│       └── vision_and_routing.md   # Спецификация роутера и экранного зрения
├── core/                           # Модули автоматизации ОС
│   └── .gitkeep
├── gui/                            # Компоненты интерфейса (PySide6)
│   └── .gitkeep
└── tests/                          # Набор тестов и сценариев верификации
    └── .gitkeep
```

---

## 🗺️ Дорожная карта (Roadmap)

- [x] **Фаза 1: Архитектурное исследование и спецификации**
  - [x] Анализ подходов к локальным агентам (UI-TARS, Qwen2.5-VL, Fast-Path)
  - [x] Формирование идеального технологического стека под 8GB VRAM
  - [x] Ребрендинг в GupkaCommander и оформление репозитория
- [ ] **Фаза 2: Скелет ядра и Fast-Path автоматизация**
  - [ ] Быстрый сканер софта (Registry + `.lnk` + `rapidfuzz`)
  - [ ] Запуск браузера, веб-ссылок и поисковых запросов
  - [ ] Модуль работы с терминалом и консолью Windows
  - [ ] Модуль управления окнами (Win32) и системным звуком
- [ ] **Фаза 3: Графический интерфейс и голосовой контур**
  - [ ] Плавающий Spotlight Bar на PySide6 (Windows 11 Mica/Acrylic)
  - [ ] Потоковый VAD + Faster-Whisper + Piper TTS на CPU
  - [ ] Интеграция Barge-In и системного трея
- [ ] **Фаза 4: Интеллектуальный роутер и Vision Grounding**
  - [ ] Подключение локального инференса Qwen2.5-VL через llama-server
  - [ ] Двухуровневый роутер (детерминированный Fast-Path vs Tool Calling)
  - [ ] Семантическая автоматизация (UIA) и визуальный клик (mss)
