@echo off
chcp 65001 > nul
setlocal EnableDelayedExpansion

title GupkaCommander - Запуск

echo ====================================================================
echo   🚀 GupkaCommander - Автономный оператор Windows 11
echo ====================================================================
echo.

cd /d "%~dp0"

:: Проверка наличия менеджера пакетов uv
where uv >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "RUNNER=uv run python"
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% equ 0 (
        set "RUNNER=python"
    ) else (
        echo [ОШИБКА] Ни 'uv', ни 'python' не найдены в системном PATH.
        echo Пожалуйста, установите uv (https://astral.sh/uv) или Python 3.12+.
        echo.
        pause
        exit /b 1
    )
)

:: Проверка наличия базовых речевых моделей
if not exist "models\silero_vad.onnx" (
    echo [ИНФО] Речевые модели не найдены в каталоге models\.
    echo Хотите загрузить легкие речевые модели CPU (VAD, Piper TTS ru_RU, Whisper ~200МБ)?
    set /p "CHOICE=Загрузить модели сейчас? [Y/n]: "
    if /i "!CHOICE!"=="" set "CHOICE=Y"
    if /i "!CHOICE!"=="Y" (
        echo [ИНФО] Запуск загрузчика моделей...
        %RUNNER% scripts\download_models.py --voice-only
        echo.
    )
)

:: Проверка наличия иконки приложения
if not exist "assets\gupka_commander.ico" (
    echo [ИНФО] Генерация фирменной иконки assets\gupka_commander.ico...
    %RUNNER% scripts\generate_icon.py >nul 2>&1
)

echo [ИНФО] Запуск GupkaCommander...
echo [СОВЕТ] Нажмите Alt+Space в любом месте Windows для вызова строки поиска.
echo [СОВЕТ] Нажмите Esc или покачайте мышь для аварийной остановки (Fail-Safe).
echo.

%RUNNER% main.py %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ОШИБКА] Приложение завершилось с кодом ошибки %ERRORLEVEL%.
    pause
)
