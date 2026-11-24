"""Менеджер автоматического жизненного цикла серверов llama-server.exe (VLM + TTS).

> [!NOTE]
> **Назначение файла:** Управляет двумя GPU-сервисами `llama-server.exe` под бюджет 8 ГБ VRAM
> (RTX 5050):
> 1) `:8080` — VLM/LLM `Qwen3.5-4B-Q4_K_M.gguf` + `mmproj` (`-fa on -ctk q8_0 -ctv q8_0`);
> 2) `:8081` — TTS `Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M.gguf` + `mmproj` (`--tts-lang ru`).
> Включает `VRAMBudgetGuard` (через `nvidia-smi` с таймаутом): при общей VRAM < 6.5 ГБ
> запускает только VLM и оставляет TTS в резервном режиме Piper.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from core.bootstrap import (
    MIN_TTS_BYTES,
    MIN_VLM_BYTES,
    QWEN_TTS_MMPROJ_FILENAME,
    QWEN_TTS_MODEL_FILENAME,
    VLM_MMPROJ_FILENAME,
    VLM_MODEL_FILENAME,
    DependencyManager,
    _is_valid_file,
)
from core.paths import get_bin_dir, get_config_path, get_models_dir

logger = logging.getLogger(__name__)

DEFAULT_MODEL_FILENAME = VLM_MODEL_FILENAME
DEFAULT_MMPROJ_FILENAME = VLM_MMPROJ_FILENAME
DEFAULT_MMPROJ_ALT_FILENAME = "mmproj-F16.gguf"
DEFAULT_TTS_MODEL_FILENAME = QWEN_TTS_MODEL_FILENAME
DEFAULT_TTS_MMPROJ_FILENAME = QWEN_TTS_MMPROJ_FILENAME


@dataclass(frozen=True)
class VRAMStatus:
    """Снимок доступной видеопамяти GPU (в МиБ и ГБ)."""

    gpu_available: bool
    total_mb: float
    free_mb: float

    @property
    def total_gb(self) -> float:
        """Общий объем VRAM в гигабайтах."""
        return self.total_mb / 1024.0

    @property
    def free_gb(self) -> float:
        """Свободный объем VRAM в гигабайтах."""
        return self.free_mb / 1024.0


class VRAMBudgetGuard:
    """Контроллер бюджета видеопамяти GPU для одновременного запуска VLM и Qwen3-TTS."""

    def __init__(
        self,
        min_total_gb: float = 6.5,
        min_free_gb: float = 1.5,
        timeout_seconds: float = 2.0,
        vram_probe: Callable[[], VRAMStatus] | None = None,
    ) -> None:
        """Инициализация стража бюджета VRAM.

        Args:
            min_total_gb: Минимальный общий объем VRAM (ГБ) для запуска второго GPU-сервиса TTS.
            min_free_gb: Минимальный свободный объем VRAM (ГБ) для безопасного старта TTS.
            timeout_seconds: Таймаут вызова `nvidia-smi`.
            vram_probe: Внедряемый зонд VRAM (для тестирования без реальной GPU).
        """
        self.min_total_gb = float(min_total_gb)
        self.min_free_gb = float(min_free_gb)
        self.timeout_seconds = max(0.3, float(timeout_seconds))
        self._vram_probe = vram_probe

    def query_vram(self) -> VRAMStatus:
        """Безопасно запрашивает объем VRAM через `nvidia-smi` с защитой от зависаний."""
        if self._vram_probe is not None:
            try:
                return self._vram_probe()
            except Exception as exc:
                logger.debug("Ошибка пользовательского vram_probe: %s", exc)
                return VRAMStatus(gpu_available=False, total_mb=0.0, free_mb=0.0)

        kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": self.timeout_seconds,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        try:
            proc = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.total,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                **kwargs,
            )
            if proc.returncode != 0 or not proc.stdout:
                return VRAMStatus(gpu_available=False, total_mb=0.0, free_mb=0.0)

            best: VRAMStatus | None = None
            for raw_line in proc.stdout.strip().splitlines():
                parts = [p.strip() for p in raw_line.split(",")]
                if len(parts) < 2:
                    continue
                total_mb = float(parts[0])
                free_mb = float(parts[1])
                candidate = VRAMStatus(gpu_available=True, total_mb=total_mb, free_mb=free_mb)
                if best is None or candidate.total_mb > best.total_mb:
                    best = candidate

            return best or VRAMStatus(gpu_available=False, total_mb=0.0, free_mb=0.0)
        except Exception as exc:
            logger.debug("nvidia-smi недоступен или превысил таймаут: %s", exc)
            return VRAMStatus(gpu_available=False, total_mb=0.0, free_mb=0.0)

    def can_run_tts(self) -> bool:
        """Возвращает True, если VRAM достаточно для запуска :8081 Qwen3-TTS поверх VLM."""
        status = self.query_vram()
        if not status.gpu_available:
            logger.info("VRAMBudgetGuard: NVIDIA GPU не обнаружена — TTS остается на Piper CPU.")
            return False
        if status.total_gb < self.min_total_gb:
            logger.info(
                "VRAMBudgetGuard: всего %.2f ГБ VRAM (< %.2f ГБ) — "
                "запускаем только VLM, TTS -> Piper.",
                status.total_gb,
                self.min_total_gb,
            )
            return False
        if status.free_gb < self.min_free_gb:
            logger.info(
                "VRAMBudgetGuard: свободно %.2f ГБ VRAM (< %.2f ГБ) — TTS отложен на Piper.",
                status.free_gb,
                self.min_free_gb,
            )
            return False
        return True


class LlamaServerManager:
    """Управляет фоновыми процессами llama-server.exe (:8080 VLM и :8081 Qwen3-TTS)."""

    def __init__(
        self,
        models_dir: Path | None = None,
        bin_dir: Path | None = None,
        settings_path: Path | None = None,
        host: str = "127.0.0.1",
        port: int = 8080,
        tts_port: int = 8081,
        popen_factory: Callable[..., Any] | None = None,
        health_checker: Callable[[], bool] | None = None,
        tts_health_checker: Callable[[], bool] | None = None,
        vram_guard: VRAMBudgetGuard | None = None,
    ) -> None:
        """Инициализация пула локальных серверов инференса llama.cpp."""
        self.models_dir: Path = models_dir if models_dir is not None else get_models_dir()
        self.bin_dir: Path = bin_dir if bin_dir is not None else get_bin_dir()
        self.settings_path: Path = (
            settings_path if settings_path is not None else get_config_path("settings.json")
        )
        self.host = host
        self.port = port
        self.tts_port = tts_port
        self._popen_factory: Callable[..., Any] = popen_factory or subprocess.Popen
        self._health_checker = health_checker
        self._tts_health_checker = tts_health_checker
        self._vram_guard = vram_guard if vram_guard is not None else VRAMBudgetGuard()
        self._process: Any | None = None
        self._tts_process: Any | None = None
        self._lock = threading.Lock()
        self._dep_manager = DependencyManager(models_dir=self.models_dir, bin_dir=self.bin_dir)

    def _read_llm_settings(self) -> tuple[bool, int, int]:
        """Считывает параметры (autostart, n_gpu_layers, context_window) из settings.json."""
        autostart = True
        n_gpu_layers = 99
        ctx_size = 8192

        if not self.settings_path.is_file():
            return autostart, n_gpu_layers, ctx_size

        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            llm_cfg = data.get("models", {}).get("llm", {})
            if isinstance(llm_cfg, dict):
                autostart = bool(llm_cfg.get("autostart", True))
                n_gpu_layers = int(llm_cfg.get("n_gpu_layers", 99))
                ctx_size = int(llm_cfg.get("context_window", 8192))
        except Exception as exc:
            logger.debug("Не удалось прочитать настройки LLM из %s: %s", self.settings_path, exc)

        return autostart, n_gpu_layers, ctx_size

    def _read_tts_settings(self) -> tuple[bool, str, int]:
        """Считывает параметры (enabled, lang, port) секции models.tts из settings.json."""
        enabled = True
        lang = "ru"
        port = self.tts_port

        if not self.settings_path.is_file():
            return enabled, lang, port

        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            tts_cfg = data.get("models", {}).get("tts", {})
            if isinstance(tts_cfg, dict):
                engine = str(tts_cfg.get("engine", "qwen3-tts")).lower()
                enabled = engine in {"qwen3-tts", "qwen-tts", "qwen_tts", "auto"}
                lang = str(tts_cfg.get("lang", "ru"))
                port = int(tts_cfg.get("port", self.tts_port))
        except Exception as exc:
            logger.debug("Не удалось прочитать настройки TTS из %s: %s", self.settings_path, exc)

        return enabled, lang, port

    def find_binary(self) -> Path | None:
        """Находит исполняемый файл llama-server.exe."""
        return self._dep_manager.find_llama_server()

    def find_model_paths(self) -> tuple[Path | None, Path | None]:
        """Возвращает пути к основной GGUF-модели VLM и проектору mmproj (если найдены)."""
        llm_dir = self.models_dir / "llm"
        model_path = llm_dir / DEFAULT_MODEL_FILENAME
        mmproj_path = llm_dir / DEFAULT_MMPROJ_FILENAME
        mmproj_alt_path = llm_dir / DEFAULT_MMPROJ_ALT_FILENAME

        found_model = model_path if _is_valid_file(model_path, MIN_VLM_BYTES) else None
        if _is_valid_file(mmproj_path, MIN_VLM_BYTES):
            found_mmproj: Path | None = mmproj_path
        elif _is_valid_file(mmproj_alt_path, MIN_VLM_BYTES):
            found_mmproj = mmproj_alt_path
        else:
            found_mmproj = None
        return found_model, found_mmproj

    def find_tts_model_paths(self) -> tuple[Path | None, Path | None]:
        """Возвращает пути к GGUF-модели Qwen3-TTS и её mmproj-проектору."""
        for subdir in ("tts", "llm"):
            base_dir = self.models_dir / subdir
            model_path = base_dir / DEFAULT_TTS_MODEL_FILENAME
            mmproj_path = base_dir / DEFAULT_TTS_MMPROJ_FILENAME
            if _is_valid_file(model_path, MIN_TTS_BYTES):
                found_mmproj = mmproj_path if _is_valid_file(mmproj_path, MIN_TTS_BYTES) else None
                return model_path, found_mmproj
        return None, None

    def is_available(self) -> bool:
        """Проверяет наличие и бинарника llama-server.exe, и основной GGUF-модели VLM."""
        model_path, _ = self.find_model_paths()
        return self.find_binary() is not None and model_path is not None

    def is_tts_available(self) -> bool:
        """Проверяет наличие llama-server.exe и весов Qwen3-TTS."""
        tts_model, _ = self.find_tts_model_paths()
        return self.find_binary() is not None and tts_model is not None

    def is_running_managed(self) -> bool:
        """Проверяет, активен ли дочерний VLM-процесс, запущенный этим менеджером."""
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def is_tts_running_managed(self) -> bool:
        """Проверяет, активен ли дочерний TTS-процесс, запущенный этим менеджером."""
        with self._lock:
            return self._tts_process is not None and self._tts_process.poll() is None

    def is_server_healthy(self) -> bool:
        """Проверяет, отвечает ли локальный HTTP-сервер VLM (:8080)."""
        if self._health_checker is not None:
            try:
                return bool(self._health_checker())
            except Exception:
                return False

        try:
            resp = requests.get(f"http://{self.host}:{self.port}/health", timeout=0.4)
            return resp.status_code == 200
        except Exception:
            return False

    def is_tts_healthy(self) -> bool:
        """Проверяет, отвечает ли локальный HTTP-сервер TTS (:8081)."""
        if self._tts_health_checker is not None:
            try:
                return bool(self._tts_health_checker())
            except Exception:
                return False

        try:
            resp = requests.get(f"http://{self.host}:{self.tts_port}/health", timeout=0.4)
            return resp.status_code == 200
        except Exception:
            return False

    def should_use_qwen_tts(self) -> bool:
        """Возвращает True, если TTS-сервер (:8081) активен и готов к запросам /tts."""
        return self.is_tts_running_managed() or self.is_tts_healthy()

    def build_command(
        self,
        binary: Path,
        model: Path,
        mmproj: Path | None,
        n_gpu_layers: int,
        ctx_size: int,
    ) -> list[str]:
        """Формирует список аргументов командной строки для запуска VLM llama-server.exe."""
        cmd = [
            str(binary),
            "--model",
            str(model),
            "-ngl",
            str(n_gpu_layers),
            "--port",
            str(self.port),
            "--host",
            self.host,
            "--ctx-size",
            str(ctx_size),
            "-fa",
            "on",
            "-ctk",
            "q8_0",
            "-ctv",
            "q8_0",
            "--image-min-tokens",
            "1024",
            "--jinja",
        ]
        if mmproj is not None:
            cmd.extend(["--mmproj", str(mmproj)])
        return cmd

    def build_tts_command(
        self,
        binary: Path,
        model: Path,
        mmproj: Path | None,
        n_gpu_layers: int,
        tts_lang: str = "ru",
        port: int | None = None,
    ) -> list[str]:
        """Формирует список аргументов для запуска сервиса Qwen3-TTS (:8081)."""
        target_port = port if port is not None else self.tts_port
        cmd = [
            str(binary),
            "--model",
            str(model),
            "-ngl",
            str(n_gpu_layers),
            "--port",
            str(target_port),
            "--host",
            self.host,
            "-fa",
            "on",
            "--tts-lang",
            tts_lang,
        ]
        if mmproj is not None:
            cmd.extend(["--mmproj", str(mmproj)])
        return cmd

    def _spawn_background(self, binary: Path, cmd: list[str]) -> Any:
        """Запускает дочерний процесс без окна консоли."""
        kwargs: dict[str, Any] = {
            "cwd": str(binary.parent),
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return self._popen_factory(cmd, **kwargs)

    def start_tts(self, force: bool = False) -> bool:
        """Запускает сервис Qwen3-TTS (:8081) при наличии моделей и достаточной VRAM."""
        with self._lock:
            if self._tts_process is not None and self._tts_process.poll() is None:
                return True

        if self.is_tts_healthy():
            logger.info(
                "Локальный llama-server TTS уже запущен на %s:%d.",
                self.host,
                self.tts_port,
            )
            return True

        tts_enabled, tts_lang, tts_port = self._read_tts_settings()
        self.tts_port = tts_port
        if not force and not tts_enabled:
            logger.debug("Qwen3-TTS отключен в settings.json — используется Piper.")
            return False

        binary = self.find_binary()
        tts_model, tts_mmproj = self.find_tts_model_paths()
        if binary is None or tts_model is None:
            logger.debug("Веса Qwen3-TTS не найдены — используется резервный Piper TTS.")
            return False

        if not self._vram_guard.can_run_tts():
            return False

        _, n_gpu_layers, _ = self._read_llm_settings()
        cmd = self.build_tts_command(
            binary,
            tts_model,
            tts_mmproj,
            n_gpu_layers=n_gpu_layers,
            tts_lang=tts_lang,
            port=tts_port,
        )

        with self._lock:
            if self._tts_process is not None and self._tts_process.poll() is None:
                return True
            try:
                self._tts_process = self._spawn_background(binary, cmd)
                logger.info("Запущен фоновый llama-server TTS на порту %d.", tts_port)
                return True
            except Exception as exc:
                logger.warning("Не удалось запустить фоновый llama-server TTS: %s", exc)
                self._tts_process = None
                return False

    def start(self, force: bool = False) -> bool:
        """Запускает VLM (:8080) и, если позволяет бюджет VRAM, сервис Qwen3-TTS (:8081)."""
        vlm_ok = self._start_vlm(force=force)
        if not vlm_ok:
            return False

        # Пытаемся поднять TTS (:8081) только если веса TTS реально присутствуют и хватает VRAM
        tts_model, _ = self.find_tts_model_paths()
        if tts_model is not None:
            self.start_tts(force=force)
        return True

    def _start_vlm(self, force: bool = False) -> bool:
        """Запускает основной VLM-сервер (:8080)."""
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return True

        if self.is_server_healthy():
            logger.info("Локальный llama-server VLM уже запущен на %s:%d.", self.host, self.port)
            return True

        autostart, n_gpu_layers, ctx_size = self._read_llm_settings()
        if not force and not autostart:
            logger.debug("Автозапуск llama-server отключен в settings.json.")
            return False

        binary = self.find_binary()
        model, mmproj = self.find_model_paths()
        if binary is None or model is None:
            logger.debug("llama-server.exe или веса Qwen3.5-4B не найдены — автозапуск пропущен.")
            return False

        cmd = self.build_command(binary, model, mmproj, n_gpu_layers, ctx_size)
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return True
            try:
                self._process = self._spawn_background(binary, cmd)
                logger.info("Автоматически запущен фоновый llama-server VLM: %s", binary.name)
                return True
            except Exception as exc:
                logger.warning("Не удалось запустить фоновый llama-server VLM: %s", exc)
                self._process = None
                return False

    def _terminate_process(self, proc: Any | None, label: str) -> None:
        """Корректно завершает дочерний процесс llama-server.exe."""
        if proc is None or proc.poll() is not None:
            return

        try:
            proc.terminate()
            proc.wait(timeout=2.0)
            logger.info("Фоновый llama-server (%s) корректно остановлен.", label)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception as exc:
                logger.debug("Ошибка принудительного завершения llama-server (%s): %s", label, exc)

    def stop_tts(self) -> None:
        """Останавливает управляемый процесс Qwen3-TTS (:8081)."""
        with self._lock:
            proc = self._tts_process
            self._tts_process = None
        self._terminate_process(proc, "TTS")

    def stop(self) -> None:
        """Корректно завершает дочерние процессы VLM (:8080) и TTS (:8081)."""
        with self._lock:
            vlm_proc = self._process
            tts_proc = self._tts_process
            self._process = None
            self._tts_process = None

        self._terminate_process(tts_proc, "TTS")
        self._terminate_process(vlm_proc, "VLM")
