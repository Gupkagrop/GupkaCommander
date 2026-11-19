@echo off
chcp 65001 > nul
setlocal EnableDelayedExpansion

title GupkaCommander - Первоначальная настройка

echo ====================================================================
echo   🛠️ GupkaCommander - Мастер первоначальной настройки
echo ====================================================================
echo.

cd /d "%~dp0"

:: 1. Проверка окружения
echo [1/4] Проверка среды выполнения (uv / Python)...
where uv >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [OK] Обнаружен менеджер пакетов uv.
    set "RUNNER=uv run python"
    echo Синхронизация зависимостей...
    uv sync
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% equ 0 (
        echo [OK] Обнаружен Python.
        set "RUNNER=python"
    ) else (
        echo [ОШИБКА] Ни 'uv', ни 'python' не найдены в системном PATH.
        echo Установите uv: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
        pause
        exit /b 1
    )
)

:: 2. Генерация фирменной иконки
echo.
echo [2/4] Генерация фирменной иконки приложения assets\gupka_commander.ico...
%RUNNER% scripts\generate_icon.py

:: 3. Загрузка речевых моделей на CPU
echo.
echo [3/4] Загрузка речевых моделей CPU (Silero VAD, Piper TTS ru_RU, Whisper base)...
%RUNNER% scripts\download_models.py --voice-only

:: 4. Создание ярлыка на Рабочем столе
echo.
echo [4/4] Создание ярлыка на Рабочем столе...
%RUNNER% scripts\create_shortcut.py --desktop

echo.
echo ====================================================================
echo   🎉 Настройка GupkaCommander успешно завершена!
echo ====================================================================
echo Ярлык создан на вашем Рабочем столе.
echo Вы можете запустить приложение через start.bat или дважды кликнув по ярлыку.
echo.
set /p "LAUNCH=Запустить GupkaCommander прямо сейчас? [Y/n]: "
if /i "!LAUNCH!"=="" set "LAUNCH=Y"
if /i "!LAUNCH!"=="Y" (
    call start.bat
)
