<div align="center">

# 🌌 LocalAgent (Antigravity Jarvis)

**Автономный локальный голосовой и мультимодальный AI-ассистент для Windows 11**

[![Python Version](https://img.shields.io/badge/Python-3.12%2B-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%2011-0078D4.svg?logo=windows11&logoColor=white)](https://www.microsoft.com/windows)
[![CUDA Acceleration](https://img.shields.io/badge/CUDA-Hardware%20Accelerated-76B900.svg?logo=nvidia&logoColor=white)](https://developer.nvidia.com/cuda-toolkit)
[![Architecture](https://img.shields.io/badge/Architecture-Hybrid%20Local-8A2BE2.svg)](#архитектурные-принципы)
[![Branch Status](https://img.shields.io/badge/Branch-main%20(Next--Gen)-success.svg)](https://github.com/Gupkagrop/localAgent)
[![Archive Branch](https://img.shields.io/badge/Archive-v1.0%20Preserved-orange.svg)](https://github.com/Gupkagrop/localAgent/tree/archive)

<p align="center">
  <b>100% приватный • Без облачных подписок • Автономное компьютерное зрение • Мгновенный отклик</b>
</p>

</div>

---

> [!NOTE]
> **Перезапуск архитектуры:** Исходная версия v1.0 со всеми наработками, тестами и прототипом управления экраном бережно сохранена в ветке [`archive`](https://github.com/Gupkagrop/localAgent/tree/archive).  
> В ветке `main` разворачивается разработка следующего поколения с обновленным модульным ядром, повышенной производительностью и строгой изоляцией процессов.

---

## 🎯 Концепция проекта

**LocalAgent** — это персональный локальный агент-ассистент операционной системы, объединяющий голосовой интерфейс реального времени и компьютерное зрение для автоматизации повседневных задач на ПК без выхода в интернет.

### Ключевые возможности:

* 🔒 **100% локальная приватность:** Звук с микрофона, скриншоты экрана и системные команды никогда не покидают ваш компьютер.
* ⚡ **Гибридный Fast-Path роутер:** Детерминированные команды (запуск программ, системная громкость, быстрый поиск) исполняются мгновенно (<10 мс) без задействования тяжёлых нейросетей.
* 🎙️ **Быстрый голосовой контур:** Аппаратное распознавание речи на GPU (Faster-Whisper CUDA FP16) + естественный синтез речи без задержек (Piper Neural TTS на CPU).
* 👁️ **Computer-Use Vision Agent:** Компактная Vision-Language модель для понимания интерфейса и взаимодействия с элементами экрана кликами и клавиатурой (Win32 GDI BitBlt + SendInput).
* 🎨 **Fluent GUI для Windows 11:** Компактный плавающий статус-оверлей (Floating Pill), поиск Spotlight, системный трей и мягкая контурная индикация активности (Screen Glow).

---

## 🏗️ Архитектурные принципы

```mermaid
flowchart TD
    subgraph Inputs["Источники ввода"]
        Mic["🎙️ Микрофон (SoundDevice)"]
        Hotkeys["⌨️ Хоткеи / Copilot Key (WH_KEYBOARD_LL)"]
        Spotlight["🔍 Spotlight Bar (Текстовый ввод)"]
    end

    subgraph Core["Ядро системы (Coordinator)"]
        STT["Faster-Whisper (CUDA FP16)"]
        Router{"Гибридный роутер\nDecision Engine"}
        FastPath["⚡ Fast-Path Executor\n(Системные команды, Hotkeys, Win32)"]
        TTS["🔊 Piper TTS (ONNX CPU)"]
    end

    subgraph VisionProcess["Изолированный Vision Worker (Subprocess)"]
        VLM["👁️ Vision Model (Jedi-3B / Qwen2.5-VL NF4)"]
        Screen["📸 GDI Screen Capture (BitBlt ~4ms)"]
        InputSim["🖱️ SendInput (Координаты мыши / Клавиатура)"]
    end

    subgraph GUI["Интерфейс Windows 11"]
        Pill["Floating Pill & VU-метр"]
        Glow["Screen Glow Overlay"]
        Tray["System Tray"]
    end

    Mic --> STT
    Hotkeys --> Router
    Spotlight --> Router
    STT --> Router

    Router -- "Детерминированные задачи" --> FastPath
    Router -- "Интерфейсные задачи" --> VisionProcess

    FastPath --> GUI
    FastPath --> TTS
    VisionProcess --> GUI
    VisionProcess --> TTS
```

---

## 🛠️ Стек технологий

| Компонент | Технология | Назначение |
|---|---|---|
| **Язык** | Python 3.12+ | Основной язык реализации |
| **GUI** | PyQt6 | Fluent-дизайн, плавающие оверлеи, аппаратное ускорение рендеринга |
| **STT** | Faster-Whisper / CTranslate2 | Высокоскоростное распознавание речи на CUDA (FP16/INT8) |
| **TTS** | Piper TTS (ONNX Runtime) | Локальный нейросинтез русской речи высокого качества на CPU |
| **Vision** | BitsAndBytes NF4 / Transformers | Компактные Vision-модели управления экраном |
| **Win32 API** | `pywin32`, `ctypes`, `pycaw` | Захват экрана BitBlt, хуки клавиатуры, аудио-дакинг громкости |
| **Тестирование** | `unittest` | Всесторонняя автоматизированная верификация всех модулей |

---

## 🚀 Быстрый старт

### Требования к системе:
* **ОС:** Windows 10/11 (64-bit)
* **Процессор:** 4+ ядер
* **Оперативная память:** от 16 ГБ RAM
* **Видеокарта:** NVIDIA GeForce (от 6-8 ГБ VRAM с поддержкой CUDA)
* **Python:** 3.12+

### Установка:

```powershell
# 1. Клонирование репозитория
git clone https://github.com/Gupkagrop/localAgent.git
cd localAgent

# 2. Создание виртуального окружения
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Установка зависимостей (будет дополнено по мере разработки)
pip install --upgrade pip
```

---

## 📂 Структура репозитория

```text
localAgent/
├── .gitignore              # Исключения Git (кэши, .venv, модели)
├── README.md               # Документация и презентация проекта
├── config/                 # Конфигурационные файлы и схемы команд (в разработке)
├── core/                   # Ядро: аудио, роутер, исполнитель, vision
├── gui/                    # Графический интерфейс PyQt6 и оверлеи
├── models/                 # Локальные веса моделей (TTS, Whisper)
└── tests/                  # Автоматизированный тестовый сьют
```

---

## 📜 Лицензия

Проект распространяется под лицензией **MIT**. Подробности в файле `LICENSE`.
