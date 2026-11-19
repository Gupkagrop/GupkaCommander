"""Скрипт загрузки локальных моделей машинного обучения для GupkaCommander.

> [!NOTE]
> **Назначение файла:** Загрузка легковесных моделей (Silero VAD, Piper TTS, Faster-Whisper,
> Qwen2.5-VL GGUF) в локальный каталог проекта с поддержкой возобновления, пропуска существующих
> файлов и отображения прогресса.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import requests

logger = logging.getLogger("model_downloader")

SILERO_VAD_URL = (
    "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"
)
SILERO_VAD_BACKUP_URL = (
    "https://huggingface.co/snakers4/silero-vad/resolve/main/src/silero_vad/data/silero_vad.onnx"
)
PIPER_MODEL_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/"
    "dmitri/medium/ru_RU-dmitri-medium.onnx"
)
PIPER_CONFIG_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/"
    "dmitri/medium/ru_RU-dmitri-medium.onnx.json"
)
VLM_MODEL_URL = (
    "https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct-GGUF/resolve/main/"
    "qwen2.5-vl-3b-instruct-q4_k_m.gguf"
)
VLM_MMPROJ_URL = (
    "https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct-GGUF/resolve/main/mmproj-model-f16.gguf"
)
WHISPER_REPO_BASE = "https://huggingface.co/Systran/faster-whisper-base/resolve/main"
WHISPER_FILES = ["model.bin", "config.json", "vocabulary.json", "tokenizer.json"]


def _should_skip_download(dest_path: Path, force: bool, description: str) -> bool:
    """Проверяет, существует ли уже валидный файл для пропуска загрузки."""
    if force or not dest_path.is_file():
        return False
    size_mb = dest_path.stat().st_size / (1024 * 1024)
    if size_mb <= 0:
        return False
    print(f"[ПРОПУСК] {description}: '{dest_path.name}' уже существует ({size_mb:.2f} МБ)")
    return True


def _format_progress(description: str, current_bytes: int, total_bytes: int) -> None:
    """Отображает текущий статус загрузки в консоли."""
    curr_mb = current_bytes / (1024 * 1024)
    if total_bytes > 0:
        tot_mb = total_bytes / (1024 * 1024)
        pct = (current_bytes / total_bytes) * 100.0
        msg = f"\r[СКАЧИВАНИЕ] {description}: {curr_mb:.1f}/{tot_mb:.1f} МБ ({pct:.1f}%)"
    else:
        msg = f"\r[СКАЧИВАНИЕ] {description}: {curr_mb:.1f} МБ"
    sys.stdout.write(msg)
    sys.stdout.flush()


def _stream_chunks_to_file(
    response: requests.Response,
    part_path: Path,
    mode: str,
    initial_bytes: int,
    total_bytes: int,
    description: str,
) -> None:
    """Записывает входящий поток данных в файл с обновлением индикатора прогресса."""
    downloaded = initial_bytes
    chunk_size = 64 * 1024
    with open(part_path, mode) as dst:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if not chunk:
                continue
            dst.write(chunk)
            downloaded += len(chunk)
            _format_progress(description, downloaded, total_bytes)
    sys.stdout.write("\n")
    sys.stdout.flush()


def download_file(
    url: str,
    dest_path: Path,
    description: str = "",
    force: bool = False,
    session: requests.Session | None = None,
) -> bool:
    """Скачивает файл по URL с поддержкой возобновления, пропуска и индикатора прогресса."""
    label = description or dest_path.name
    if _should_skip_download(dest_path, force, label):
        return True

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = dest_path.with_suffix(dest_path.suffix + ".part")
    existing_bytes = part_path.stat().st_size if part_path.is_file() and not force else 0

    headers: dict[str, str] = {}
    if existing_bytes > 0:
        headers["Range"] = f"bytes={existing_bytes}-"

    http = session or requests.Session()
    try:
        with http.get(url, stream=True, timeout=60, headers=headers) as resp:
            resp.raise_for_status()
            is_partial = resp.status_code == 206
            mode = "ab" if is_partial else "wb"
            initial_bytes = existing_bytes if is_partial else 0
            content_len = int(resp.headers.get("Content-Length", 0))
            total_bytes = initial_bytes + content_len

            _stream_chunks_to_file(resp, part_path, mode, initial_bytes, total_bytes, label)

        os.replace(part_path, dest_path)
        print(f"[ГОТОВО] {label}: сохранен в {dest_path}")
        return True
    except Exception as exc:
        logger.error("Ошибка скачивания %s: %s", label, exc)
        print(f"[ОШИБКА] Не удалось скачать {label}: {exc}")
        return False


def download_silero_vad(models_dir: Path, force: bool = False) -> Path:
    """Загружает модель Silero VAD v5 ONNX."""
    target_path = models_dir / "silero_vad.onnx"
    success = download_file(
        SILERO_VAD_URL,
        target_path,
        description="Silero VAD v5 (ONNX)",
        force=force,
    )
    if not success:
        download_file(
            SILERO_VAD_BACKUP_URL,
            target_path,
            description="Silero VAD v5 (Резервное зеркало)",
            force=force,
        )
    return target_path


def download_piper_tts(models_dir: Path, force: bool = False) -> tuple[Path, Path]:
    """Загружает модель Piper TTS ru_RU Dmitri Medium и конфигурационный файл."""
    piper_dir = models_dir / "piper"
    model_path = piper_dir / "ru_RU-dmitri-medium.onnx"
    config_path = piper_dir / "ru_RU-dmitri-medium.onnx.json"

    download_file(
        PIPER_MODEL_URL,
        model_path,
        description="Piper TTS ru_RU Dmitri Medium (ONNX)",
        force=force,
    )
    download_file(
        PIPER_CONFIG_URL,
        config_path,
        description="Piper TTS Конфигурация (JSON)",
        force=force,
    )
    return model_path, config_path


def download_faster_whisper(
    models_dir: Path,
    model_size: str = "base",
    force: bool = False,
) -> Path:
    """Инициализирует кэш модели Faster-Whisper."""
    whisper_dir = models_dir / "whisper"
    whisper_dir.mkdir(parents=True, exist_ok=True)
    marker_file = whisper_dir / "model.bin"

    if marker_file.is_file() and not force:
        print(f"[ПРОПУСК] Faster-Whisper: кэш модели '{model_size}' уже инициализирован")
        return whisper_dir

    try:
        import faster_whisper  # type: ignore[import-untyped]

        print(
            f"[СКАЧИВАНИЕ] Faster-Whisper: инициализация модели '{model_size}' через библиотеку..."
        )
        faster_whisper.download_model(model_size, output_dir=str(whisper_dir))
        print(f"[ГОТОВО] Faster-Whisper: кэш модели сохранен в {whisper_dir}")
        return whisper_dir
    except Exception as exc:
        logger.warning("Загрузка через faster_whisper не удалась: %s. Скачивание напрямую.", exc)

    for filename in WHISPER_FILES:
        url = f"{WHISPER_REPO_BASE}/{filename}"
        download_file(
            url,
            whisper_dir / filename,
            description=f"Faster-Whisper ({filename})",
            force=force,
        )

    return whisper_dir


def download_vlm_models(models_dir: Path, force: bool = False) -> list[Path]:
    """Загружает GGUF квантование Qwen2.5-VL-3B-Instruct и проектор для локального LLM-сервера."""
    llm_dir = models_dir / "llm"
    model_path = llm_dir / "qwen2.5-vl-3b-instruct-q4_k_m.gguf"
    mmproj_path = llm_dir / "qwen2.5-vl-3b-mmproj-f16.gguf"

    download_file(
        VLM_MODEL_URL,
        model_path,
        description="Qwen2.5-VL-3B-Instruct Q4_K_M (GGUF)",
        force=force,
    )
    download_file(
        VLM_MMPROJ_URL,
        mmproj_path,
        description="Qwen2.5-VL-3B Multimodal Projector F16 (GGUF)",
        force=force,
    )
    return [model_path, mmproj_path]


def download_voice_models(models_dir: Path, force: bool = False) -> list[Path]:
    """Загружает весь речевой контур (Silero VAD, Piper TTS, Faster-Whisper)."""
    results: list[Path] = []
    results.append(download_silero_vad(models_dir, force=force))
    model, cfg = download_piper_tts(models_dir, force=force)
    results.extend([model, cfg])
    results.append(download_faster_whisper(models_dir, force=force))
    return results


def build_argument_parser() -> argparse.ArgumentParser:
    """Создает парсер аргументов командной строки."""
    parser = argparse.ArgumentParser(
        description="Утилита загрузки легковесных локальных моделей для GupkaCommander.",
    )
    parser.add_argument(
        "--models-dir",
        type=str,
        default="models",
        help="Путь к корневому каталогу моделей (по умолчанию 'models').",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Скачать все модели (речевой стек + локальный VLM мозг).",
    )
    parser.add_argument(
        "--voice-only",
        action="store_true",
        help="Скачать только модели речевого контура (Silero VAD, Piper TTS, Faster-Whisper).",
    )
    parser.add_argument(
        "--vlm-only",
        action="store_true",
        help="Скачать только модели локального VLM (Qwen2.5-VL-3B GGUF).",
    )
    parser.add_argument(
        "--with-vlm",
        action="store_true",
        help="Скачать речевые модели вместе с VLM (эквивалентно --all).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Принудительно перезаписать существующие файлы.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Точка входа скрипта загрузки моделей."""
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    models_dir = Path(args.models_dir).resolve()
    models_dir.mkdir(parents=True, exist_ok=True)
    force: bool = bool(args.force)

    include_voice = not args.vlm_only
    include_vlm = bool(args.all or args.with_vlm or args.vlm_only)

    print(f"=== GupkaCommander: Загрузка моделей в '{models_dir}' ===")
    if include_voice:
        print("\n--- Загрузка речевого контура (CPU) ---")
        download_voice_models(models_dir, force=force)

    if include_vlm:
        print("\n--- Загрузка локального VLM мозга (GPU) ---")
        download_vlm_models(models_dir, force=force)

    print("\n=== Все запланированные модели успешно обработаны ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
