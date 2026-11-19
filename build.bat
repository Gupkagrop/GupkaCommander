@echo off
chcp 65001 >nul
echo ===================================================
echo   GupkaCommander — Сборка автономного дистрибутива
echo ===================================================
echo.

where uv >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [1/2] Компиляция автономного исполняемого пакета PyInstaller...
    uv run python scripts/build_executable.py
    if %ERRORLEVEL% neq 0 (
        echo [ОШИБКА] Сборка PyInstaller завершилась с ошибкой!
        pause
        exit /b 1
    )
    echo.
    echo [2/2] Компиляция инсталлятора Inno Setup...
    uv run python scripts/build_installer.py
) else (
    echo [1/2] Компиляция автономного исполняемого пакета PyInstaller (python)...
    python scripts/build_executable.py
    if %ERRORLEVEL% neq 0 (
        echo [ОШИБКА] Сборка PyInstaller завершилась с ошибкой!
        pause
        exit /b 1
    )
    echo.
    echo [2/2] Компиляция инсталлятора Inno Setup (python)...
    python scripts/build_installer.py
)

echo.
echo ===================================================
echo   Сборка завершена! Проверьте папку dist/
echo ===================================================
pause
