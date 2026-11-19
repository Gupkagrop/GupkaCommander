# ⚡ Архитектура Fast-Path: Детерминированное управление ОС

> [!NOTE]
> **Назначение документа:** Спецификация мгновенного (Tier 0) уровня исполнения команд в Windows 11. Описывает механизмы быстрого поиска и запуска установленных программ, навигации по веб-сайтам, исполнения консольных команд и управления окнами без обращения к нейросетевым моделям.

---

## 1. Концепция Fast-Path

Fast-Path гарантирует выполнение типовых системных команд за время **менее 20 миллисекунд** со 100% надежностью. 

Вместо того чтобы делать скриншот, передавать его в Vision-модель и искать иконку кликом мыши, агент использует **нативные Windows API, реестр и системные протоколы**.

---

## 2. Модуль разрешения приложений (`AppFinder`)

### Источники данных об установленных программах:
1. **Реестр Windows (Uninstall Keys):**
   * `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall`
   * `HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall`
   * `HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall`
   * `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths` (прямые пути к `.exe`)
2. **Ярлыки меню «Пуск» (`.lnk` файлы):**
   * `%ProgramData%\Microsoft\Windows\Start Menu\Programs`
   * `%APPDATA%\Microsoft\Windows\Start Menu\Programs`
3. **UWP / Windows Store приложения:**
   * Протоколы `ms-windows-store:`, `calculator:`, `ms-settings:`, `ms-clock:` и т.д.

### Алгоритм кэширования и нечеткого поиска (Fuzzy Search):
* При старте приложения сканер строит ассоциативный словарь:  
  `"название программы / алиасы" ➔ "полный путь к .exe или .lnk / протокол"`.
* Кэш сохраняется в JSON-файл в локальной папке конфигурации и обновляется в фоновом режиме раз в сутки или по требованию.
* Поиск выполняется через библиотеку `rapidfuzz` (C++ биндинги для максимальной скорости < 1 мс).
* Поддержка пользовательских синонимов (например, `телега` ➔ `Telegram.exe`, `код` ➔ `Code.exe`, `браузер` ➔ `chrome.exe`).

---

## 3. Модуль запуска веб-сайтов (`WebLauncher`)

* **Прямой переход:** Использование системной функции `os.startfile(url)` или вызов установленного браузера по умолчанию (`start https://...`).
* **Разрешение популярных сайтов:**
  * Словарь соответствий:
    * `"ютуб" / "youtube"` ➔ `https://www.youtube.com`
    * `"гугл" / "поиск"` ➔ `https://www.google.com`
    * `"яндекс"` ➔ `https://ya.ru`
    * `"гитхаб" / "github"` ➔ `https://github.com`
  * Поддержка поисковых запросов на лету:
    * «найди в гугле рецепт плова» ➔ `https://www.google.com/search?q=рецепт+плова`
    * «найди на ютубе рок концерт» ➔ `https://www.youtube.com/results?search_query=рок+концерт`

---

## 4. Модуль консоли и терминала (`ShellExecutor`)

* **Запуск интерактивного терминала:**
  * Запуск Windows Terminal: `wt.exe`
  * Запуск PowerShell: `powershell.exe -NoExit`
  * Запуск классической CMD: `cmd.exe /k`
* **Фоновое выполнение скриптов/команд:**
  * Выполнение через `subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, timeout=15)`
  * Ограничение вывода до 50 строк для предотвращения переполнения контекста.
  * Четкое разделение `stdout` и `stderr`.
* **Политика безопасности:**
  * Блокировка потенциально разрушительных команд (`Format-Volume`, `rmdir /s /q C:\`, модификация реестра ядра) без явного подтверждения пользователя через интерфейс.

---

## 5. Модуль управления окнами и системой (`WindowManager`)

Использует Win32 API (`pywin32`):
* `ShowWindow(hwnd, SW_MINIMIZE)` — свернуть окно.
* `ShowWindow(hwnd, SW_MAXIMIZE)` — развернуть окно.
* `SetForegroundWindow(hwnd)` — перевести окно на передний план.
* `PostMessage(hwnd, WM_CLOSE, 0, 0)` — безопасное закрытие окна.
* Глобальные системные действия:
  * Управление громкостью через `VK_VOLUME_UP`, `VK_VOLUME_DOWN`, `VK_VOLUME_MUTE`.
  * Управление медиа-плеером (`VK_MEDIA_PLAY_PAUSE`, `VK_MEDIA_NEXT_TRACK`).
  * Сворачивание всех окон (`Win + D` через `keybd_event`).
