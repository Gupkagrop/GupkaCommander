<#
.SYNOPSIS
    GupkaCommander - Скрипт запуска приложения для PowerShell Windows 11.
.DESCRIPTION
    Запускает GupkaCommander с проверкой окружения (uv/python), автоматической
    проверкой моделей и поддержкой тихого запуска в фоне.
.PARAMETER Background
    Запустить приложение в скрытом режиме (в системном трее без окна консоли).
.PARAMETER Setup
    Выполнить первоначальную настройку (скачивание моделей, создание ярлыка).
.EXAMPLE
    .\start.ps1
    .\start.ps1 -Background
    .\start.ps1 -Setup
#>

[CmdletBinding()]
param(
    [switch]$Background,
    [switch]$Setup
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host "  🚀 GupkaCommander - Автономный оператор Windows 11" -ForegroundColor Cyan
Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host ""

# Определение команды запуска
$runner = $null
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $runner = "uv run python"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $runner = "python"
} else {
    Write-Host "[ОШИБКА] Ни 'uv', ни 'python' не найдены в системном PATH." -ForegroundColor Red
    Write-Host "Установите uv: https://astral.sh/uv" -ForegroundColor Yellow
    exit 1
}

# Если передан флаг -Setup
if ($Setup) {
    Write-Host "[ИНФО] Выполнение первоначальной настройки..." -ForegroundColor Green
    cmd.exe /c "$scriptDir\setup.bat"
    exit 0
}

# Проверка иконки
$iconPath = Join-Path $scriptDir "assets\gupka_commander.ico"
if (-not (Test-Path $iconPath)) {
    Write-Host "[ИНФО] Генерация фирменной иконки..." -ForegroundColor Yellow
    Invoke-Expression "$runner scripts\generate_icon.py" | Out-Null
}

# Проверка моделей
$vadPath = Join-Path $scriptDir "models\silero_vad.onnx"
if (-not (Test-Path $vadPath)) {
    Write-Host "[ИНФО] Речевые модели не найдены в папке models/." -ForegroundColor Yellow
    $ans = Read-Host "Загрузить легкие речевые модели CPU (VAD, Piper, Whisper ~200МБ)? [Y/n]"
    if ([string]::IsNullOrWhiteSpace($ans) -or $ans.ToUpper() -eq "Y") {
        Invoke-Expression "$runner scripts\download_models.py --voice-only"
    }
}

if ($Background) {
    Write-Host "[ИНФО] Запуск в фоновом режиме (системный трей)..." -ForegroundColor Green
    $pythonw = Join-Path $scriptDir ".venv\Scripts\pythonw.exe"
    if (Test-Path $pythonw) {
        Start-Process -FilePath $pythonw -ArgumentList "main.py" -WorkingDirectory $scriptDir
    } else {
        wscript.exe "$scriptDir\start_quiet.vbs"
    }
    Write-Host "[OK] Приложение запущено в фоне. Нажмите Alt+Space для вызова." -ForegroundColor Cyan
    exit 0
}

Write-Host "[ИНФО] Запуск интерфейса..." -ForegroundColor Green
Write-Host "[СОВЕТ] Нажмите Alt+Space в любом месте Windows для вызова Spotlight Bar." -ForegroundColor Gray
Write-Host "[СОВЕТ] Нажмите Esc или покачайте мышь для экстренного прерывания (Fail-Safe)." -ForegroundColor Gray
Write-Host ""

Invoke-Expression "$runner main.py"
