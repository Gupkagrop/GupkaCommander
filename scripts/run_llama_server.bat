@echo off
setlocal

rem ==============================================================================
rem GupkaCommander: Скрипт запуска локального сервера llama.cpp (llama-server.exe)
rem Модель: Qwen3-VL-4B-Instruct (GGUF Q4_K_M + mmproj Q8_0)
rem Порт: 8080 (совместимо с OpenAI API /v1)
rem ==============================================================================

rem Переход в корневую директорию проекта
cd /d "%~dp0\.."

set "MODEL_PATH=models\llm\qwen3-vl-4b-instruct-q4_k_m.gguf"
set "MMPROJ_PATH=models\llm\mmproj-qwen3-vl-4b-instruct-q8_0.gguf"
set "PORT=8080"
set "HOST=127.0.0.1"
set "NGL=99"
set "CTX_SIZE=4096"

echo ====================================================================
echo   GupkaCommander -- Запуск локального VLM сервера llama.cpp
echo ====================================================================

if not exist "%MODEL_PATH%" if exist "%LOCALAPPDATA%\GupkaCommander\models\llm\qwen3-vl-4b-instruct-q4_k_m.gguf" (
    set "MODEL_PATH=%LOCALAPPDATA%\GupkaCommander\models\llm\qwen3-vl-4b-instruct-q4_k_m.gguf"
)
if not exist "%MMPROJ_PATH%" if exist "%LOCALAPPDATA%\GupkaCommander\models\llm\mmproj-qwen3-vl-4b-instruct-q8_0.gguf" (
    set "MMPROJ_PATH=%LOCALAPPDATA%\GupkaCommander\models\llm\mmproj-qwen3-vl-4b-instruct-q8_0.gguf"
)

rem 1. Поиск исполняемого файла llama-server.exe
set "LLAMA_BIN="

if defined LLAMA_SERVER_PATH if exist "%LLAMA_SERVER_PATH%" set "LLAMA_BIN=%LLAMA_SERVER_PATH%"
if not defined LLAMA_BIN if exist "%LOCALAPPDATA%\GupkaCommander\bin\llama-server.exe" set "LLAMA_BIN=%LOCALAPPDATA%\GupkaCommander\bin\llama-server.exe"
if not defined LLAMA_BIN if exist "llama-server.exe" set "LLAMA_BIN=llama-server.exe"
if not defined LLAMA_BIN if exist "tools\llama-server.exe" set "LLAMA_BIN=tools\llama-server.exe"
if not defined LLAMA_BIN if exist "bin\llama-server.exe" set "LLAMA_BIN=bin\llama-server.exe"
if not defined LLAMA_BIN if exist "models\llama-server.exe" set "LLAMA_BIN=models\llama-server.exe"

if defined LLAMA_BIN goto :check_model

for /f "tokens=*" %%i in ('where llama-server.exe 2^>nul') do (
    set "LLAMA_BIN=%%i"
    goto :check_model
)

echo.
echo [ОШИБКА] Исполняемый файл llama-server.exe не обнаружен.
echo.
echo Для автономной работы когнитивного зрения и планировщика GupkaCommander
echo требуется бинарный сервер llama.cpp с поддержкой аппаратного ускорения GPU.
echo.
echo Пожалуйста, выполните следующие шаги:
echo 1. Перейдите на страницу официальных релизов llama.cpp:
echo    https://github.com/ggerganov/llama.cpp/releases
echo 2. Скачайте архив для Windows с поддержкой CUDA:
echo    llama-bXXXX-bin-win-cuda-cu12.4-x64.zip
echo 3. Распакуйте llama-server.exe в корень проекта или добавьте путь к нему в PATH.
echo.
if not "%1"=="--no-pause" pause
exit /b 1

:check_model
echo [OK] Найден бинарный файл: %LLAMA_BIN%

if not exist "%MODEL_PATH%" (
    echo.
    echo [ПРЕДУПРЕЖДЕНИЕ] Файл основной модели не найден: %MODEL_PATH%
    echo Скачайте модель командой: uv run python scripts/download_models.py --with-vlm
    echo.
)

if not exist "%MMPROJ_PATH%" (
    echo [ПРЕДУПРЕЖДЕНИЕ] Файл проектора mmproj не найден: %MMPROJ_PATH%
    echo.
)

echo.
echo Запуск сервера:
echo   Хост:      %HOST%:%PORT%
echo   Слои GPU:  %NGL%
echo   Контекст:  %CTX_SIZE% токенов
echo.

"%LLAMA_BIN%" --model "%MODEL_PATH%" --mmproj "%MMPROJ_PATH%" -ngl %NGL% --port %PORT% --host %HOST% --ctx-size %CTX_SIZE%

if errorlevel 1 (
    echo.
    echo [ОШИБКА] Процесс llama-server завершился с кодом %ERRORLEVEL%.
    if not "%1"=="--no-pause" pause
    exit /b %ERRORLEVEL%
)

endlocal
