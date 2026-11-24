@echo off
setlocal

rem ==============================================================================
rem GupkaCommander: Скрипт запуска локальных серверов llama.cpp (llama-server.exe)
rem VLM (:8080): Qwen3.5-4B (GGUF Q4_K_M + mmproj F16, -fa on -ctk q8_0 -ctv q8_0)
rem TTS (:8081): Qwen3-TTS-12Hz-1.7B (GGUF Q4_K_M + mmproj Q8_0, --tts-lang ru)
rem ==============================================================================

rem Переход в корневую директорию проекта
cd /d "%~dp0\.."

set "MODEL_PATH=models\llm\Qwen3.5-4B-Q4_K_M.gguf"
set "MMPROJ_PATH=models\llm\mmproj-qwen3.5-4b-f16.gguf"
set "TTS_MODEL_PATH=models\tts\Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M.gguf"
set "TTS_MMPROJ_PATH=models\tts\mmproj-Qwen3-TTS-12Hz-1.7B-Base-Q8_0.gguf"
set "PORT=8080"
set "TTS_PORT=8081"
set "HOST=127.0.0.1"
set "NGL=99"
set "CTX_SIZE=8192"
set "TTS_MODE=0"
set "NO_PAUSE=0"

for %%A in (%*) do (
    if "%%~A"=="--tts" set "TTS_MODE=1"
    if "%%~A"=="--no-pause" set "NO_PAUSE=1"
)

echo ====================================================================
echo   GupkaCommander -- Запуск локального сервера llama.cpp (2.0)
echo ====================================================================

if not exist "%MODEL_PATH%" if exist "%LOCALAPPDATA%\GupkaCommander\models\llm\Qwen3.5-4B-Q4_K_M.gguf" (
    set "MODEL_PATH=%LOCALAPPDATA%\GupkaCommander\models\llm\Qwen3.5-4B-Q4_K_M.gguf"
)
if not exist "%MMPROJ_PATH%" if exist "%LOCALAPPDATA%\GupkaCommander\models\llm\mmproj-qwen3.5-4b-f16.gguf" (
    set "MMPROJ_PATH=%LOCALAPPDATA%\GupkaCommander\models\llm\mmproj-qwen3.5-4b-f16.gguf"
)
if not exist "%TTS_MODEL_PATH%" if exist "%LOCALAPPDATA%\GupkaCommander\models\tts\Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M.gguf" (
    set "TTS_MODEL_PATH=%LOCALAPPDATA%\GupkaCommander\models\tts\Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M.gguf"
)
if not exist "%TTS_MMPROJ_PATH%" if exist "%LOCALAPPDATA%\GupkaCommander\models\tts\mmproj-Qwen3-TTS-12Hz-1.7B-Base-Q8_0.gguf" (
    set "TTS_MMPROJ_PATH=%LOCALAPPDATA%\GupkaCommander\models\tts\mmproj-Qwen3-TTS-12Hz-1.7B-Base-Q8_0.gguf"
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
echo Для автономной работы когнитивного зрения и GPU TTS GupkaCommander
echo требуется бинарный сервер llama.cpp с поддержкой аппаратного ускорения GPU.
echo.
echo Пожалуйста, выполните следующие шаги:
echo 1. Перейдите на страницу официальных релизов llama.cpp:
echo    https://github.com/ggerganov/llama.cpp/releases
echo 2. Скачайте архив для Windows с поддержкой CUDA:
echo    llama-bXXXX-bin-win-cuda-cu12.4-x64.zip
echo 3. Распакуйте llama-server.exe в корень проекта или добавьте путь к нему в PATH.
echo.
if "%NO_PAUSE%"=="0" pause
exit /b 1

:check_model
echo [OK] Найден бинарный файл: %LLAMA_BIN%

if "%TTS_MODE%"=="1" goto :launch_tts

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
echo Запуск VLM сервера:
echo   Хост:      %HOST%:%PORT%
echo   Слои GPU:  %NGL%
echo   Контекст:  %CTX_SIZE% токенов
echo.

"%LLAMA_BIN%" --model "%MODEL_PATH%" --mmproj "%MMPROJ_PATH%" -ngl %NGL% --port %PORT% --host %HOST% --ctx-size %CTX_SIZE% -fa on -ctk q8_0 -ctv q8_0 --image-min-tokens 1024 --jinja
goto :after_launch

:launch_tts
if not exist "%TTS_MODEL_PATH%" (
    echo [ПРЕДУПРЕЖДЕНИЕ] Файл модели Qwen3-TTS не найден: %TTS_MODEL_PATH%
    echo Скачайте модель командой: uv run python scripts/download_models.py --with-gpu-voice
)
echo.
echo Запуск TTS сервера (Qwen3-TTS):
echo   Хост:      %HOST%:%TTS_PORT%
echo   Слои GPU:  %NGL%
echo.
"%LLAMA_BIN%" --model "%TTS_MODEL_PATH%" --mmproj "%TTS_MMPROJ_PATH%" -ngl %NGL% --port %TTS_PORT% --host %HOST% -fa on --tts-lang ru

:after_launch
if errorlevel 1 (
    echo.
    echo [ОШИБКА] Процесс llama-server завершился с кодом %ERRORLEVEL%.
    if "%NO_PAUSE%"=="0" pause
    exit /b %ERRORLEVEL%
)

endlocal
