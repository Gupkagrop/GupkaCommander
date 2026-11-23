"""Менеджер автоматического жизненного цикла локального сервера llama-server.exe.

> [!NOTE]
> **Назначение файла:** Автоматически обнаруживает скачанные веса Qwen2.5-VL-3B GGUF и бинарник
> llama-server.exe, безопасно поднимает его в фоне без окна консоли (CREATE_NO_WINDOW) при
> старте приложения и корректно завершает дочерний процесс при выходе.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests

from core.bootstrap import MIN_VLM_BYTES, DependencyManager, _is_valid_file
from core.paths import get_bin_dir, get_config_path, get_models_dir

logger = logging.getLogger(__name__)

DEFAULT_MODEL_FILENAME = "qwen2.5-vl-3b-instruct-q4_k_m.gguf"
DEFAULT_MMPROJ_FILENAME = "qwen2.5-vl-3b-mmproj-f16.gguf"


class LlamaServerManager:
    """Управляет фоновым процессом llama-server.exe для автономной работы локального VLM."""

    def __init__(
        self,
        models_dir: Path | None = None,
        bin_dir: Path | None = None,
        settings_path: Path | None = None,
        host: str = "127.0.0.1",
        port: int = 8080,
        popen_factory: Callable[..., Any] | None = None,
        health_checker: Callable[[], bool] | None = None,
    ) -> None:
        """Инициализация менеджера сервера инференса llama.cpp."""
        self.models_dir: Path = models_dir if models_dir is not None else get_models_dir()
        self.bin_dir: Path = bin_dir if bin_dir is not None else get_bin_dir()
        self.settings_path: Path = (
            settings_path if settings_path is not None else get_config_path("settings.json")
        )
        self.host = host
        self.port = port
        self._popen_factory: Callable[..., Any] = popen_factory or subprocess.Popen
        self._health_checker = health_checker
        self._process: Any | None = None
        self._lock = threading.Lock()
        self._dep_manager = DependencyManager(models_dir=self.models_dir, bin_dir=self.bin_dir)

    def _read_llm_settings(self) -> tuple[bool, int, int]:
        """Считывает параметры (autostart, n_gpu_layers, context_window) из settings.json."""
        autostart = True
        n_gpu_layers = 99
        ctx_size = 4096

        if not self.settings_path.is_file():
            return autostart, n_gpu_layers, ctx_size

        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            llm_cfg = data.get("models", {}).get("llm", {})
            if isinstance(llm_cfg, dict):
                autostart = bool(llm_cfg.get("autostart", True))
                n_gpu_layers = int(llm_cfg.get("n_gpu_layers", 99))
                ctx_size = int(llm_cfg.get("context_window", 4096))
        except Exception as exc:
            logger.debug("Не удалось прочитать настройки LLM из %s: %s", self.settings_path, exc)

        return autostart, n_gpu_layers, ctx_size

    def find_binary(self) -> Path | None:
        """Находит исполняемый файл llama-server.exe."""
        return self._dep_manager.find_llama_server()

    def find_model_paths(self) -> tuple[Path | None, Path | None]:
        """Возвращает пути к основной GGUF-модели и проектору mmproj (если найдены)."""
        llm_dir = self.models_dir / "llm"
        model_path = llm_dir / DEFAULT_MODEL_FILENAME
        mmproj_path = llm_dir / DEFAULT_MMPROJ_FILENAME

        found_model = model_path if _is_valid_file(model_path, MIN_VLM_BYTES) else None
        found_mmproj = mmproj_path if _is_valid_file(mmproj_path, MIN_VLM_BYTES) else None
        return found_model, found_mmproj

    def is_available(self) -> bool:
        """Проверяет наличие и бинарника llama-server.exe, и основной GGUF-модели."""
        model_path, _ = self.find_model_paths()
        return self.find_binary() is not None and model_path is not None

    def is_running_managed(self) -> bool:
        """Проверяет, активен ли дочерний процесс, запущенный этим менеджером."""
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def is_server_healthy(self) -> bool:
        """Проверяет, отвечает ли локальный HTTP-сервер llama.cpp."""
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

    def build_command(
        self,
        binary: Path,
        model: Path,
        mmproj: Path | None,
        n_gpu_layers: int,
        ctx_size: int,
    ) -> list[str]:
        """Формирует список аргументов командной строки для запуска llama-server.exe."""
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
        ]
        if mmproj is not None:
            cmd.extend(["--mmproj", str(mmproj)])
        return cmd

    def start(self, force: bool = False) -> bool:
        """Запускает llama-server.exe в фоновом режиме без консоли, если он еще не запущен.

        Args:
            force: Игнорировать флаг autostart=False в settings.json.

        Returns:
            True, если сервер уже работает или успешно запущен; иначе False.
        """
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return True

        if self.is_server_healthy():
            logger.info("Локальный llama-server уже запущен на %s:%d.", self.host, self.port)
            return True

        autostart, n_gpu_layers, ctx_size = self._read_llm_settings()
        if not force and not autostart:
            logger.debug("Автозапуск llama-server отключен в settings.json.")
            return False

        binary = self.find_binary()
        model, mmproj = self.find_model_paths()
        if binary is None or model is None:
            logger.debug("llama-server.exe или веса Qwen2.5-VL не найдены — автозапуск пропущен.")
            return False

        cmd = self.build_command(binary, model, mmproj, n_gpu_layers, ctx_size)
        kwargs: dict[str, Any] = {
            "cwd": str(binary.parent),
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return True
            try:
                self._process = self._popen_factory(cmd, **kwargs)
                logger.info("Автоматически запущен фоновый llama-server: %s", binary.name)
                return True
            except Exception as exc:
                logger.warning("Не удалось запустить фоновый llama-server: %s", exc)
                self._process = None
                return False

    def stop(self) -> None:
        """Корректно завершает дочерний процесс llama-server.exe (если он был запущен нами)."""
        with self._lock:
            proc = self._process
            self._process = None

        if proc is None or proc.poll() is not None:
            return

        try:
            proc.terminate()
            proc.wait(timeout=2.0)
            logger.info("Фоновый llama-server корректно остановлен.")
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception as exc:
                logger.debug("Ошибка принудительного завершения llama-server: %s", exc)
