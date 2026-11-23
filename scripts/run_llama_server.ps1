<#
.SYNOPSIS
    GupkaCommander: Скрипт запуска локального инференс-сервера llama-server.exe.
.DESCRIPTION
    Запускает OpenAI-совместимый HTTP сервер llama.cpp для мультимодальной модели
    Qwen3-VL-4B-Instruct (GGUF Q4_K_M + mmproj Q8_0) с выгрузкой всех слоев на GPU NVIDIA.
#>

[CmdletBinding()]
param(
    [string]$ModelPath = "models\llm\qwen3-vl-4b-instruct-q4_k_m.gguf",
    [string]$MmprojPath = "models\llm\mmproj-qwen3-vl-4b-instruct-q8_0.gguf",
    [int]$Port = 8080,
    [string]$HostAddress = "127.0.0.1",
    [int]$Ngl = 99,
    [int]$CtxSize = 4096,
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"

# Переход в корневую директорию проекта
$ProjectRoot = Resolve-Path "$PSScriptRoot\.."
Set-Location $ProjectRoot

Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host "  GupkaCommander -- Запуск локального VLM сервера llama.cpp" -ForegroundColor Cyan
Write-Host "====================================================================" -ForegroundColor Cyan

# 1. Поиск исполняемого файла llama-server.exe
$CandidatePaths = @(
    $env:LLAMA_SERVER_PATH,
    "$ProjectRoot\llama-server.exe",
    "$ProjectRoot\tools\llama-server.exe",
    "$ProjectRoot\bin\llama-server.exe",
    "$ProjectRoot\models\llama-server.exe"
)

$LlamaBin = $null
foreach ($path in $CandidatePaths) {
    if (![string]::IsNullOrWhiteSpace($path) -and (Test-Path $path)) {
        $LlamaBin = (Resolve-Path $path).Path
        break
    }
}

if (-not $LlamaBin) {
    $CommandCheck = Get-Command "llama-server.exe" -ErrorAction SilentlyContinue
    if ($CommandCheck) {
        $LlamaBin = $CommandCheck.Source
    }
}

if (-not $LlamaBin) {
    Write-Host "`n[ОШИБКА] Исполняемый файл llama-server.exe не обнаружен." -ForegroundColor Red
    Write-Host "`nДля автономной работы когнитивного зрения и планировщика GupkaCommander"
    Write-Host "требуется бинарный сервер llama.cpp с поддержкой аппаратного ускорения GPU."
    Write-Host "`nПожалуйста, выполните следующие шаги:"
    Write-Host "1. Перейдите на страницу официальных релизов llama.cpp:" -ForegroundColor Yellow
    Write-Host "   https://github.com/ggerganov/llama.cpp/releases" -ForegroundColor Cyan
    Write-Host "2. Скачайте архив для Windows с поддержкой CUDA (для NVIDIA RTX GPU):" -ForegroundColor Yellow
    Write-Host "   Например: llama-bXXXX-bin-win-cuda-cu12.4-x64.zip" -ForegroundColor White
    Write-Host "3. Распакуйте llama-server.exe в корень проекта или добавьте путь к нему в PATH.`n" -ForegroundColor Yellow

    if (-not $NoPause -and [Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
        Read-Host "Нажмите Enter для выхода..."
    }
    exit 1
}

Write-Host "[OK] Найден бинарный файл: $LlamaBin" -ForegroundColor Green

# 2. Проверка наличия моделей
if (-not (Test-Path $ModelPath)) {
    Write-Host "`n[ПРЕДУПРЕЖДЕНИЕ] Файл модели не найден: $ModelPath" -ForegroundColor Yellow
    Write-Host "Вы можете скачать его автоматически с помощью команды:"
    Write-Host "   uv run python scripts/download_models.py --with-vlm`n" -ForegroundColor Cyan
}

if (-not (Test-Path $MmprojPath)) {
    Write-Host "[ПРЕДУПРЕЖДЕНИЕ] Файл проектора мультимодальности не найден: $MmprojPath`n" -ForegroundColor Yellow
}

Write-Host "`nЗапуск сервера:" -ForegroundColor Gray
Write-Host "  Хост:      ${HostAddress}:${Port}" -ForegroundColor Gray
Write-Host "  Слои GPU:  $Ngl (полная выгрузка в VRAM)" -ForegroundColor Gray
Write-Host "  Контекст:  $CtxSize токенов`n" -ForegroundColor Gray

$ArgsList = @(
    "--model", $ModelPath,
    "--mmproj", $MmprojPath,
    "-ngl", $Ngl,
    "--port", $Port,
    "--host", $HostAddress,
    "--ctx-size", $CtxSize,
    "--image-min-tokens", "1024",
    "--jinja"
)

& $LlamaBin @ArgsList
