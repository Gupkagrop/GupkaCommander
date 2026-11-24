"""Модуль автоматической проверки и загрузки внешних зависимостей GupkaCommander.

> [!NOTE]
> **Назначение файла:** Диагностика наличия локальных речевых моделей (Silero VAD, Piper TTS,
> Faster-Whisper, GigaAM v3 ONNX, Qwen3-TTS), мультимодальной модели VLM (Qwen3.5-4B GGUF)
> и бинарного сервера llama-server.exe, а также их безопасная потоковая загрузка с докачкой,
> отменой и проверкой свободного места на диске для автономного .exe дистрибутива.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import requests

from core.paths import get_bin_dir, get_bundle_root, get_config_path, get_models_dir

logger = logging.getLogger(__name__)

# Проверенные источники моделей и бинарных компонентов
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
WHISPER_REPO_BASE = "https://huggingface.co/Systran/faster-whisper-base/resolve/main"
WHISPER_FILES: tuple[str, ...] = (
    "model.bin",
    "config.json",
    "vocabulary.txt",
    "tokenizer.json",
)
VLM_MODEL_URL = "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf"
VLM_MMPROJ_URL = "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/mmproj-F16.gguf"
VLM_MODEL_FILENAME = "Qwen3.5-4B-Q4_K_M.gguf"
VLM_MMPROJ_FILENAME = "mmproj-qwen3.5-4b-f16.gguf"
QWEN_TTS_MODEL_URL = (
    "https://huggingface.co/ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF/resolve/main/"
    "Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M.gguf"
)
QWEN_TTS_MMPROJ_URL = (
    "https://huggingface.co/ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF/resolve/main/"
    "mmproj-Qwen3-TTS-12Hz-1.7B-Base-Q8_0.gguf"
)
QWEN_TTS_MODEL_FILENAME = "Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M.gguf"
QWEN_TTS_MMPROJ_FILENAME = "mmproj-Qwen3-TTS-12Hz-1.7B-Base-Q8_0.gguf"
GIGAAM_MODEL_URL = (
    "https://huggingface.co/istupakov/gigaam-v3-onnx/resolve/main/v3_e2e_ctc.int8.onnx"
)
GIGAAM_VOCAB_URL = (
    "https://huggingface.co/istupakov/gigaam-v3-onnx/resolve/main/v3_e2e_ctc_vocab.txt"
)
GIGAAM_MODEL_FILENAME = "v3_e2e_ctc.int8.onnx"
GIGAAM_VOCAB_FILENAME = "v3_e2e_ctc_vocab.txt"
LLAMA_RELEASES_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=5"

BOOTSTRAP_STATE_FILE = "bootstrap_state.json"
DOWNLOAD_CHUNK_BYTES = 64 * 1024
HTTP_TIMEOUT_S = 30
MIN_VAD_BYTES = 100 * 1024
MIN_PIPER_BYTES = 1 * 1024 * 1024
MIN_WHISPER_BYTES = 10 * 1024 * 1024
MIN_GIGAAM_BYTES = 50 * 1024 * 1024
MIN_TTS_BYTES = 100 * 1024 * 1024
MIN_VLM_BYTES = 100 * 1024 * 1024


class DependencyGroup(StrEnum):
    """Логическая группа внешних зависимостей."""

    VOICE = "voice"
    VLM = "vlm"
    RUNTIME = "runtime"


@dataclass(frozen=True)
class FileSpec:
    """Описание одного скачиваемого файла."""

    url: str
    relative_path: str
    label: str
    backup_url: str | None = None
    min_bytes: int = 1


@dataclass
class DependencyItem:
    """Метаданные и текущее состояние одной внешней зависимости."""

    id: str
    name: str
    description: str
    group: DependencyGroup
    approx_size_mb: float
    required_for_default: bool
    installed: bool = False
    target_paths: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class DownloadProgress:
    """Снимок прогресса загрузки для отображения в GUI."""

    item_id: str
    item_name: str
    file_label: str
    downloaded_bytes: int
    total_bytes: int
    item_index: int
    total_items: int
    overall_percent: float
    speed_mbps: float
    status_text: str


@dataclass
class DownloadResult:
    """Итог пакетной загрузки зависимостей."""

    succeeded: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        """Возвращает True, если не было ошибок и отмены."""
        return not self.cancelled and len(self.failed) == 0


@dataclass
class DeleteResult:
    """Итог безопасного удаления локальных зависимостей."""

    deleted_items: list[str] = field(default_factory=list)
    deleted_files: int = 0
    freed_bytes: int = 0
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def freed_mb(self) -> float:
        """Объем освобожденного места на диске в мегабайтах."""
        return self.freed_bytes / (1024 * 1024)

    @property
    def ok(self) -> bool:
        """Возвращает True, если удаление завершилось без ошибок."""
        return len(self.failed) == 0


@dataclass(frozen=True)
class BootstrapReport:
    """Отчет инспекции локального окружения и установленных зависимостей."""

    items: list[DependencyItem]
    models_dir: Path
    bin_dir: Path
    free_space_mb: float
    skip_prompt_saved: bool

    @property
    def missing_required(self) -> list[DependencyItem]:
        """Недостающие базовые зависимости (речевой контур)."""
        return [it for it in self.items if it.required_for_default and not it.installed]

    @property
    def missing_optional(self) -> list[DependencyItem]:
        """Недостающие опциональные зависимости (VLM, llama-server)."""
        return [it for it in self.items if not it.required_for_default and not it.installed]

    @property
    def missing_all(self) -> list[DependencyItem]:
        """Все отсутствующие зависимости."""
        return [it for it in self.items if not it.installed]

    @property
    def total_missing_required_mb(self) -> float:
        """Суммарный размер недостающих базовых зависимостей в МБ."""
        return sum(it.approx_size_mb for it in self.missing_required)


ProgressCallback = Callable[[DownloadProgress], None]


def format_size_ru(size_mb: float) -> str:
    """Форматирует объем данных на русском языке (МБ или ГБ)."""
    if size_mb >= 1024.0:
        return f"{size_mb / 1024.0:.1f} ГБ"
    return f"{size_mb:.0f} МБ" if size_mb >= 10.0 else f"{size_mb:.1f} МБ"


def _is_valid_file(path: Path, min_bytes: int = 1) -> bool:
    """Проверяет существование файла и минимальный допустимый размер."""
    try:
        return path.is_file() and path.stat().st_size >= min_bytes
    except OSError:
        return False


def _get_free_space_mb(directory: Path) -> float:
    """Возвращает объем свободного места на диске для целевой папки в МБ."""
    try:
        target = directory if directory.exists() else directory.parent
        usage = shutil.disk_usage(target)
        return usage.free / (1024 * 1024)
    except OSError:
        return 0.0


def _select_llama_asset(releases: list[dict[str, Any]]) -> tuple[str, str] | None:
    """Выбирает наилучший Windows x64 архив llama.cpp (приоритет: CUDA -> Vulkan -> CPU)."""
    for release in releases:
        assets = release.get("assets", [])
        if not isinstance(assets, list) or not assets:
            continue
        # 1. Приоритет: NVIDIA CUDA (RTX 5050 и аналоги)
        for asset in assets:
            name = str(asset.get("name", ""))
            url = str(asset.get("browser_download_url", ""))
            low = name.lower()
            if (
                low.startswith("llama-")
                and "bin-win-cuda-" in low
                and low.endswith("-x64.zip")
                and url
            ):
                return name, url
        # 2. Резерв: Vulkan -> CPU
        for token in ("bin-win-vulkan-x64.zip", "bin-win-cpu-x64.zip"):
            for asset in assets:
                name = str(asset.get("name", ""))
                url = str(asset.get("browser_download_url", ""))
                if name.lower().endswith(token) and url:
                    return name, url
    return None


def _safe_extract_zip(zip_path: Path, dest_dir: Path) -> list[Path]:
    """Безопасно распаковывает zip-архив с защитой от обхода каталогов (Zip Slip)."""
    dest_resolved = dest_dir.resolve()
    dest_resolved.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            member_path = (dest_resolved / member.filename).resolve()
            if not str(member_path).startswith(str(dest_resolved) + os.sep):
                raise ValueError(f"Обнаружен небезопасный путь в архиве: {member.filename}")
            member_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as src, open(member_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted.append(member_path)

    return extracted


def _assert_strictly_inside(path: Path, allowed_resolved: Path) -> None:
    """Проверяет, что путь и его реальный target находятся строго внутри allowed_resolved."""
    lexical = Path(os.path.abspath(path))
    if lexical == allowed_resolved or not lexical.is_relative_to(allowed_resolved):
        raise ValueError(f"Запрещенное удаление вне '{allowed_resolved}': {path}")
    resolved = path.resolve(strict=False)
    if resolved == allowed_resolved or not resolved.is_relative_to(allowed_resolved):
        raise ValueError(f"Путь '{resolved}' выходит за пределы '{allowed_resolved}'.")


def _safe_remove_path_stats(path: Path, allowed_root: Path) -> tuple[int, int]:
    """Удаляет файл/подкаталог внутри allowed_root, возвращая (freed_bytes, deleted_files)."""
    allowed_resolved = allowed_root.resolve(strict=False)
    _assert_strictly_inside(path, allowed_resolved)

    is_junction = hasattr(path, "is_junction") and path.is_junction()
    is_link = path.is_symlink() or is_junction
    if not path.exists() and not is_link:
        return 0, 0

    if is_link:
        try:
            path.unlink()
        except OSError:
            path.rmdir()
        return 0, 1

    if path.is_file():
        size = path.stat().st_size
        path.unlink()
        return size, 1

    if path.is_dir():
        freed = 0
        files_count = 0
        for child in list(path.iterdir()):
            c_freed, c_files = _safe_remove_path_stats(child, allowed_resolved)
            freed += c_freed
            files_count += c_files
        path.rmdir()
        return freed, files_count

    return 0, 0


def _safe_remove_path(path: Path, allowed_root: Path) -> int:
    """Безопасно удаляет файл или каталог строго внутри allowed_root и возвращает байты."""
    freed_bytes, _ = _safe_remove_path_stats(path, allowed_root)
    return freed_bytes


class DependencyManager:
    """Менеджер инспекции и потоковой загрузки внешних зависимостей GupkaCommander."""

    def __init__(
        self,
        models_dir: Path | None = None,
        bin_dir: Path | None = None,
        state_file: Path | None = None,
    ) -> None:
        """Инициализация менеджера зависимостей с поддержкой DI для тестов."""
        self.models_dir: Path = models_dir if models_dir is not None else get_models_dir()
        self.bin_dir: Path = bin_dir if bin_dir is not None else get_bin_dir()
        self.state_file: Path = (
            state_file if state_file is not None else get_config_path(BOOTSTRAP_STATE_FILE)
        )

    def find_llama_server(self) -> Path | None:
        """Ищет установленный бинарник llama-server.exe в локальных каталогах и PATH."""
        bundle_root = get_bundle_root()
        candidates = [
            self.bin_dir / "llama-server.exe",
            bundle_root / "llama-server.exe",
            bundle_root / "tools" / "llama-server.exe",
            bundle_root / "bin" / "llama-server.exe",
            self.models_dir / "llama-server.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate

        # Поиск внутри подпапок bin_dir (если архив распаковался во вложенную папку)
        if self.bin_dir.exists():
            for found in self.bin_dir.rglob("llama-server.exe"):
                if found.is_file():
                    return found

        in_path = shutil.which("llama-server.exe")
        return Path(in_path) if in_path else None

    def _is_whisper_installed(self) -> bool:
        """Проверяет наличие локальных файлов Faster-Whisper в models/whisper."""
        whisper_dir = self.models_dir / "whisper"
        if _is_valid_file(whisper_dir / "model.bin", MIN_WHISPER_BYTES):
            return _is_valid_file(whisper_dir / "config.json", 10)
        if not whisper_dir.exists():
            return False
        for model_bin in whisper_dir.rglob("model.bin"):
            if _is_valid_file(model_bin, MIN_WHISPER_BYTES):
                return True
        return False

    def inspect(self) -> BootstrapReport:
        """Выполняет полную диагностику всех внешних зависимостей."""
        vad_path = self.models_dir / "silero_vad.onnx"
        piper_onnx = self.models_dir / "piper" / "ru_RU-dmitri-medium.onnx"
        piper_json = self.models_dir / "piper" / "ru_RU-dmitri-medium.onnx.json"
        whisper_dir = self.models_dir / "whisper"
        gigaam_model = self.models_dir / "gigaam" / GIGAAM_MODEL_FILENAME
        gigaam_vocab = self.models_dir / "gigaam" / GIGAAM_VOCAB_FILENAME
        tts_model = self.models_dir / "tts" / QWEN_TTS_MODEL_FILENAME
        tts_mmproj = self.models_dir / "tts" / QWEN_TTS_MMPROJ_FILENAME
        vlm_model = self.models_dir / "llm" / VLM_MODEL_FILENAME
        vlm_mmproj = self.models_dir / "llm" / VLM_MMPROJ_FILENAME
        llama_bin = self.find_llama_server()

        items = [
            DependencyItem(
                id="silero_vad",
                name="Silero VAD v5 (ONNX)",
                description="Детекция голосовой активности на CPU (< 3 мс).",
                group=DependencyGroup.VOICE,
                approx_size_mb=2.2,
                required_for_default=True,
                installed=_is_valid_file(vad_path, MIN_VAD_BYTES),
                target_paths=[vad_path],
            ),
            DependencyItem(
                id="piper_tts",
                name="Piper TTS (русский голос Dmitri)",
                description="Легковесный офлайн-синтез русской речи (fallback / CPU).",
                group=DependencyGroup.VOICE,
                approx_size_mb=60.3,
                required_for_default=True,
                installed=(
                    _is_valid_file(piper_onnx, MIN_PIPER_BYTES) and _is_valid_file(piper_json, 50)
                ),
                target_paths=[piper_onnx, piper_json],
            ),
            DependencyItem(
                id="faster_whisper",
                name="Faster-Whisper Base (INT8 CPU)",
                description="Резервное офлайн-распознавание русской и английской речи.",
                group=DependencyGroup.VOICE,
                approx_size_mb=138.5,
                required_for_default=True,
                installed=self._is_whisper_installed(),
                target_paths=[whisper_dir / f for f in WHISPER_FILES],
            ),
            DependencyItem(
                id="gigaam_v3",
                name="GigaAM v3 E2E CTC (INT8 ONNX)",
                description="Рекомендуемое быстрое распознавание русской и IT-речи без PyTorch.",
                group=DependencyGroup.VOICE,
                approx_size_mb=215.0,
                required_for_default=False,
                installed=(
                    _is_valid_file(gigaam_model, MIN_GIGAAM_BYTES)
                    and _is_valid_file(gigaam_vocab, 50)
                ),
                target_paths=[gigaam_model, gigaam_vocab],
            ),
            DependencyItem(
                id="qwen3_tts",
                name="Qwen3-TTS 1.7B (GPU llama-server :8081)",
                description="Реалистичный GPU-синтез русской речи через llama-server /tts.",
                group=DependencyGroup.VOICE,
                approx_size_mb=1414.0,
                required_for_default=False,
                installed=(
                    _is_valid_file(tts_model, MIN_TTS_BYTES)
                    and _is_valid_file(tts_mmproj, MIN_TTS_BYTES)
                ),
                target_paths=[tts_model, tts_mmproj],
            ),
            DependencyItem(
                id="vlm_qwen",
                name="Qwen3.5-4B (Q4_K_M + Vision F16)",
                description="Локальный ИИ-мозг для диалога, планирования и точного GUI-зрения.",
                group=DependencyGroup.VLM,
                approx_size_mb=3255.0,
                required_for_default=False,
                installed=(
                    _is_valid_file(vlm_model, MIN_VLM_BYTES)
                    and _is_valid_file(vlm_mmproj, MIN_VLM_BYTES)
                ),
                target_paths=[vlm_model, vlm_mmproj],
            ),
            DependencyItem(
                id="llama_server",
                name="Сервер инференса llama.cpp (CUDA/Vulkan GPU)",
                description="Движок llama-server.exe для запуска Qwen3.5-4B и Qwen3-TTS.",
                group=DependencyGroup.RUNTIME,
                approx_size_mb=37.0,
                required_for_default=False,
                installed=llama_bin is not None,
                target_paths=[llama_bin or (self.bin_dir / "llama-server.exe")],
            ),
        ]

        return BootstrapReport(
            items=items,
            models_dir=self.models_dir,
            bin_dir=self.bin_dir,
            free_space_mb=_get_free_space_mb(self.models_dir),
            skip_prompt_saved=self.is_skip_prompt_saved(),
        )

    def is_skip_prompt_saved(self) -> bool:
        """Возвращает True, если пользователь ранее попросил не показывать окно загрузки."""
        if not self.state_file.is_file():
            return False
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return bool(data.get("skip_prompt", False))
        except Exception as exc:
            logger.debug("Не удалось прочитать %s: %s", self.state_file, exc)
            return False

    def save_skip_prompt(self, skip: bool) -> None:
        """Сохраняет выбор пользователя о пропуске стартового окна загрузки."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {"skip_prompt": bool(skip)}
            self.state_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Не удалось сохранить %s: %s", self.state_file, exc)

    def should_prompt_user(
        self,
        *,
        force_setup: bool = False,
        skip_setup: bool = False,
        headless: bool = False,
        quiet: bool = False,
        enable_voice: bool = True,
    ) -> bool:
        """Определяет, нужно ли показывать окно загрузки зависимостей при старте."""
        if headless or skip_setup:
            return False
        if force_setup:
            return True
        if quiet or not enable_voice:
            return False
        report = self.inspect()
        if report.skip_prompt_saved:
            return False
        return len(report.missing_required) > 0

    def _get_file_specs(self, item_id: str) -> list[FileSpec]:
        """Возвращает список файлов для скачивания по идентификатору зависимости."""
        if item_id == "silero_vad":
            return [
                FileSpec(
                    url=SILERO_VAD_URL,
                    backup_url=SILERO_VAD_BACKUP_URL,
                    relative_path="silero_vad.onnx",
                    label="Silero VAD v5",
                    min_bytes=MIN_VAD_BYTES,
                )
            ]
        if item_id == "piper_tts":
            return [
                FileSpec(
                    url=PIPER_MODEL_URL,
                    relative_path="piper/ru_RU-dmitri-medium.onnx",
                    label="Piper TTS модель",
                    min_bytes=MIN_PIPER_BYTES,
                ),
                FileSpec(
                    url=PIPER_CONFIG_URL,
                    relative_path="piper/ru_RU-dmitri-medium.onnx.json",
                    label="Piper TTS конфиг",
                    min_bytes=50,
                ),
            ]
        if item_id == "faster_whisper":
            return [
                FileSpec(
                    url=f"{WHISPER_REPO_BASE}/{filename}",
                    relative_path=f"whisper/{filename}",
                    label=f"Faster-Whisper ({filename})",
                    min_bytes=MIN_WHISPER_BYTES if filename == "model.bin" else 10,
                )
                for filename in WHISPER_FILES
            ]
        if item_id == "gigaam_v3":
            return [
                FileSpec(
                    url=GIGAAM_MODEL_URL,
                    relative_path=f"gigaam/{GIGAAM_MODEL_FILENAME}",
                    label="GigaAM v3 E2E CTC INT8",
                    min_bytes=MIN_GIGAAM_BYTES,
                ),
                FileSpec(
                    url=GIGAAM_VOCAB_URL,
                    relative_path=f"gigaam/{GIGAAM_VOCAB_FILENAME}",
                    label="GigaAM v3 Словарь",
                    min_bytes=50,
                ),
            ]
        if item_id == "qwen3_tts":
            return [
                FileSpec(
                    url=QWEN_TTS_MODEL_URL,
                    relative_path=f"tts/{QWEN_TTS_MODEL_FILENAME}",
                    label="Qwen3-TTS 1.7B Q4_K_M",
                    min_bytes=MIN_TTS_BYTES,
                ),
                FileSpec(
                    url=QWEN_TTS_MMPROJ_URL,
                    relative_path=f"tts/{QWEN_TTS_MMPROJ_FILENAME}",
                    label="Qwen3-TTS Projector Q8_0",
                    min_bytes=MIN_TTS_BYTES,
                ),
            ]
        if item_id == "vlm_qwen":
            return [
                FileSpec(
                    url=VLM_MODEL_URL,
                    relative_path=f"llm/{VLM_MODEL_FILENAME}",
                    label="Qwen3.5-4B Q4_K_M",
                    min_bytes=MIN_VLM_BYTES,
                ),
                FileSpec(
                    url=VLM_MMPROJ_URL,
                    relative_path=f"llm/{VLM_MMPROJ_FILENAME}",
                    label="Qwen3.5-4B Projector F16",
                    min_bytes=MIN_VLM_BYTES,
                ),
            ]
        return []

    def _download_single_file(
        self,
        spec: FileSpec,
        dest_path: Path,
        session: requests.Session,
        cancel_event: threading.Event,
        emit_progress: Callable[[int, int, float], None],
    ) -> None:
        """Потоковая загрузка файла с поддержкой докачки (HTTP Range) и отмены."""
        if _is_valid_file(dest_path, spec.min_bytes):
            size = dest_path.stat().st_size
            emit_progress(size, size, 0.0)
            return

        urls = [spec.url] + ([spec.backup_url] if spec.backup_url else [])
        last_error: Exception | None = None

        for url in urls:
            try:
                self._stream_url_to_file(
                    url,
                    dest_path,
                    session,
                    cancel_event,
                    emit_progress,
                    min_bytes=spec.min_bytes,
                )
                return
            except InterruptedError:
                raise
            except Exception as exc:
                last_error = exc
                logger.warning("Ошибка загрузки %s (%s): %s", spec.label, url, exc)

        raise RuntimeError(f"Не удалось скачать {spec.label}: {last_error}")

    def _stream_url_to_file(
        self,
        url: str,
        dest_path: Path,
        session: requests.Session,
        cancel_event: threading.Event,
        emit_progress: Callable[[int, int, float], None],
        min_bytes: int = 1,
    ) -> None:
        """Выполняет потоковую запись HTTP-ответа в .part файл с последующей атомарной заменой."""
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        part_path = dest_path.with_suffix(dest_path.suffix + ".part")
        existing_bytes = part_path.stat().st_size if part_path.is_file() else 0

        headers: dict[str, str] = {"User-Agent": "GupkaCommander/1.0"}
        if existing_bytes > 0:
            headers["Range"] = f"bytes={existing_bytes}-"

        with session.get(url, stream=True, timeout=HTTP_TIMEOUT_S, headers=headers) as resp:
            if resp.status_code == 416 and existing_bytes > 0:
                part_path.unlink(missing_ok=True)
                self._stream_url_to_file(
                    url,
                    dest_path,
                    session,
                    cancel_event,
                    emit_progress,
                    min_bytes=min_bytes,
                )
                return
            resp.raise_for_status()
            is_partial = resp.status_code == 206
            mode = "ab" if is_partial else "wb"
            downloaded = existing_bytes if is_partial else 0
            content_len = int(resp.headers.get("Content-Length", 0) or 0)
            total_bytes = (downloaded + content_len) if content_len > 0 else 0

            self._write_response_chunks(
                resp, part_path, mode, downloaded, total_bytes, cancel_event, emit_progress
            )

        if not _is_valid_file(part_path, min_bytes):
            actual = part_path.stat().st_size if part_path.is_file() else 0
            part_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Загруженный файл поврежден или неполный ({actual} байт < {min_bytes} байт)."
            )

        os.replace(part_path, dest_path)

    def _write_response_chunks(
        self,
        resp: requests.Response,
        part_path: Path,
        mode: str,
        initial_bytes: int,
        total_bytes: int,
        cancel_event: threading.Event,
        emit_progress: Callable[[int, int, float], None],
    ) -> None:
        """Записывает чанки потока на диск, контролируя отмену и рассчитывая скорость."""
        downloaded = initial_bytes
        session_bytes = 0
        start_time = time.monotonic()
        last_report = 0.0

        with open(part_path, mode) as dst:
            for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK_BYTES):
                if cancel_event.is_set():
                    raise InterruptedError("Загрузка отменена пользователем.")
                if not chunk:
                    continue
                dst.write(chunk)
                downloaded += len(chunk)
                session_bytes += len(chunk)

                now = time.monotonic()
                if now - last_report >= 0.1 or (total_bytes > 0 and downloaded >= total_bytes):
                    elapsed = max(now - start_time, 0.001)
                    speed_mbps = (session_bytes / (1024 * 1024)) / elapsed
                    emit_progress(downloaded, total_bytes, speed_mbps)
                    last_report = now

        elapsed = max(time.monotonic() - start_time, 0.001)
        speed_mbps = (session_bytes / (1024 * 1024)) / elapsed
        emit_progress(downloaded, total_bytes, speed_mbps)

    def _download_llama_server(
        self,
        session: requests.Session,
        cancel_event: threading.Event,
        emit_progress: Callable[[int, int, float], None],
    ) -> None:
        """Загружает и безопасно распаковывает актуальный Windows-бинарник llama-server.exe."""
        if self.find_llama_server() is not None:
            emit_progress(1, 1, 0.0)
            return

        headers = {"User-Agent": "GupkaCommander/1.0", "Accept": "application/vnd.github+json"}
        resp = session.get(LLAMA_RELEASES_API, timeout=HTTP_TIMEOUT_S, headers=headers)
        resp.raise_for_status()
        releases = resp.json()
        if not isinstance(releases, list):
            raise RuntimeError("Некорректный ответ GitHub API для llama.cpp.")

        selected = _select_llama_asset(releases)
        if selected is None:
            raise RuntimeError("Не найден подходящий Windows x64 архив llama.cpp.")

        asset_name, download_url = selected
        archive_path = self.bin_dir / asset_name
        spec = FileSpec(
            url=download_url, relative_path=asset_name, label=asset_name, min_bytes=1024
        )
        self._download_single_file(spec, archive_path, session, cancel_event, emit_progress)

        try:
            _safe_extract_zip(archive_path, self.bin_dir)
        finally:
            archive_path.unlink(missing_ok=True)

        if self.find_llama_server() is None:
            raise RuntimeError("Архив llama.cpp распакован, но llama-server.exe не найден.")

    def download_items(
        self,
        item_ids: list[str],
        on_progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        session: requests.Session | None = None,
    ) -> DownloadResult:
        """Загружает выбранный пользователем набор зависимостей с отчетом о прогрессе."""
        result = DownloadResult()
        if not item_ids:
            return result

        cancel = cancel_event or threading.Event()
        http = session or requests.Session()
        report_map = {it.id: it for it in self.inspect().items}
        total_items = len(item_ids)

        for idx, item_id in enumerate(item_ids):
            if cancel.is_set():
                result.cancelled = True
                break
            item_info = report_map.get(item_id)
            item_name = item_info.name if item_info else item_id
            try:
                self._download_one_item(
                    item_id, item_name, idx, total_items, http, cancel, on_progress
                )
                result.succeeded.append(item_id)
            except InterruptedError:
                result.cancelled = True
                break
            except Exception as exc:
                logger.error("Ошибка загрузки компонента '%s': %s", item_id, exc)
                result.failed[item_id] = str(exc)

        return result

    def _download_one_item(
        self,
        item_id: str,
        item_name: str,
        idx: int,
        total_items: int,
        http: requests.Session,
        cancel: threading.Event,
        on_progress: ProgressCallback | None,
    ) -> None:
        """Скачивает одну логическую зависимость (один или несколько файлов)."""

        def _notify(
            file_label: str,
            downloaded: int,
            total: int,
            speed: float,
            file_frac: float,
        ) -> None:
            if on_progress is None:
                return
            overall = ((idx + file_frac) / max(total_items, 1)) * 100.0
            dl_mb = downloaded / (1024 * 1024)
            tot_mb = total / (1024 * 1024)
            if total > 0:
                status = f"{file_label}: {dl_mb:.1f} / {tot_mb:.1f} МБ ({speed:.1f} МБ/с)"
            else:
                status = f"{file_label}: {dl_mb:.1f} МБ ({speed:.1f} МБ/с)"
            on_progress(
                DownloadProgress(
                    item_id=item_id,
                    item_name=item_name,
                    file_label=file_label,
                    downloaded_bytes=downloaded,
                    total_bytes=total,
                    item_index=idx + 1,
                    total_items=total_items,
                    overall_percent=min(100.0, max(0.0, overall)),
                    speed_mbps=speed,
                    status_text=status,
                )
            )

        if item_id == "llama_server":
            self._download_llama_server(
                http,
                cancel,
                lambda d, t, s: _notify(
                    "llama.cpp (llama-server.exe)", d, t, s, (d / t) if t > 0 else 0.5
                ),
            )
            return

        specs = self._get_file_specs(item_id)
        if not specs:
            raise ValueError(f"Неизвестный идентификатор зависимости: {item_id}")

        total_files = len(specs)
        for file_idx, spec in enumerate(specs):
            dest_path = self.models_dir / spec.relative_path

            def _emit_file_progress(
                downloaded: int,
                total: int,
                speed: float,
                f_idx: int = file_idx,
                lbl: str = spec.label,
            ) -> None:
                frac = (f_idx + ((downloaded / total) if total > 0 else 0.5)) / total_files
                _notify(lbl, downloaded, total, speed, frac)

            self._download_single_file(spec, dest_path, http, cancel, _emit_file_progress)

    @staticmethod
    def _sum_path_bytes(path: Path) -> int:
        """Суммирует размер обычного файла или всех файлов в каталоге (без симлинков)."""
        if path.is_symlink():
            return 0
        if path.is_file():
            try:
                return path.stat().st_size
            except OSError:
                return 0
        if not path.is_dir():
            return 0
        total = 0
        for child in path.rglob("*"):
            if child.is_file() and not child.is_symlink():
                try:
                    total += child.stat().st_size
                except OSError:
                    continue
        return total

    def _collect_item_targets(self, item_id: str) -> tuple[Path, list[Path], list[Path]]:
        """Возвращает (allowed_root, target_paths, prune_dirs) для указанного компонента."""
        if item_id == "llama_server":
            targets = list(self.bin_dir.iterdir()) if self.bin_dir.is_dir() else []
            return self.bin_dir, targets, []

        if item_id == "faster_whisper":
            whisper_dir = self.models_dir / "whisper"
            return self.models_dir, [whisper_dir, self.models_dir / "whisper.part"], []

        specs = self._get_file_specs(item_id)
        if not specs:
            raise ValueError(f"Неизвестный идентификатор зависимости: {item_id}")

        raw_targets: list[Path] = []
        for spec in specs:
            dest = self.models_dir / spec.relative_path
            raw_targets.append(dest)
            raw_targets.append(dest.with_suffix(dest.suffix + ".part"))

        prune_dirs: list[Path] = []
        if item_id in {"piper_tts", "gigaam_v3", "qwen3_tts", "vlm_qwen"}:
            subdir_map = {
                "piper_tts": "piper",
                "gigaam_v3": "gigaam",
                "qwen3_tts": "tts",
                "vlm_qwen": "llm",
            }
            sub_dir = self.models_dir / subdir_map[item_id]
            prune_dirs.append(sub_dir)
            if item_id == "vlm_qwen" and sub_dir.is_dir():
                raw_targets.extend(self._collect_vlm_legacy_paths(sub_dir))

        return self.models_dir, self._dedupe_paths(raw_targets), prune_dirs

    @staticmethod
    def _collect_vlm_legacy_paths(llm_dir: Path) -> list[Path]:
        """Собирает все GGUF/mmproj и .part файлы VLM (включая legacy v1) в models/llm."""
        matched: list[Path] = []
        for entry in llm_dir.iterdir():
            low = entry.name.lower()
            is_legacy_v1 = low.startswith("qwen3-vl-4b") or low.startswith("mmproj-qwen3-vl")
            is_gguf = low.endswith(".gguf") or low.endswith(".gguf.part")
            if is_legacy_v1 or is_gguf:
                matched.append(entry)
        return matched

    @staticmethod
    def _dedupe_paths(paths: list[Path]) -> list[Path]:
        """Удаляет дубликаты путей, сохраняя исходный порядок."""
        seen: set[Path] = set()
        unique: list[Path] = []
        for p in paths:
            norm = Path(os.path.abspath(p))
            if norm not in seen:
                seen.add(norm)
                unique.append(p)
        return unique

    @staticmethod
    def _prune_empty_dirs(allowed_root: Path, dirs: list[Path]) -> None:
        """Удаляет пустые подкаталоги строго внутри allowed_root."""
        root_resolved = allowed_root.resolve(strict=False)
        for d in dirs:
            try:
                d_resolved = d.resolve(strict=False)
                if d_resolved == root_resolved or not d_resolved.is_relative_to(root_resolved):
                    continue
                if d.is_dir() and not d.is_symlink() and not any(d.iterdir()):
                    d.rmdir()
            except OSError:
                continue

    def get_installed_size_mb(self, item_id: str | None = None) -> float:
        """Возвращает реальный объем на диске (МБ) для элемента или всех моделей и bin."""
        if item_id is None:
            total_bytes = self._sum_path_bytes(self.models_dir) + self._sum_path_bytes(self.bin_dir)
            return total_bytes / (1024 * 1024)

        try:
            _root, targets, _prune = self._collect_item_targets(item_id)
        except ValueError:
            return 0.0
        total_bytes = sum(self._sum_path_bytes(t) for t in targets)
        return total_bytes / (1024 * 1024)

    def delete_item(self, item_id: str) -> DeleteResult:
        """Безопасно удаляет файлы указанной зависимости и очищает пустые подкаталоги."""
        result = DeleteResult()
        try:
            allowed_root, targets, prune_dirs = self._collect_item_targets(item_id)
        except ValueError as exc:
            result.failed[item_id] = str(exc)
            return result

        for target in targets:
            try:
                freed, files_removed = _safe_remove_path_stats(target, allowed_root)
                result.freed_bytes += freed
                result.deleted_files += files_removed
            except (OSError, ValueError) as exc:
                logger.warning("Не удалось удалить '%s' (%s): %s", target, item_id, exc)
                result.failed[item_id] = str(exc)

        self._prune_empty_dirs(allowed_root, prune_dirs)
        if result.deleted_files > 0 and item_id not in result.failed:
            result.deleted_items.append(item_id)
        return result

    def delete_all_dependencies(self, *, reset_skip_prompt: bool = True) -> DeleteResult:
        """Безопасно удаляет все содержимое models_dir и bin_dir без затрагивания config/."""
        pre_installed = [it.id for it in self.inspect().items if it.installed]
        result = DeleteResult()

        for root in (self.models_dir, self.bin_dir):
            if not root.is_dir():
                continue
            for child in list(root.iterdir()):
                try:
                    freed, files_removed = _safe_remove_path_stats(child, root)
                    result.freed_bytes += freed
                    result.deleted_files += files_removed
                except (OSError, ValueError) as exc:
                    logger.warning("Не удалось удалить '%s': %s", child, exc)
                    result.failed[child.name] = str(exc)

        post_installed = {it.id for it in self.inspect().items if it.installed}
        result.deleted_items = [iid for iid in pre_installed if iid not in post_installed]

        if reset_skip_prompt:
            self.save_skip_prompt(False)

        return result
