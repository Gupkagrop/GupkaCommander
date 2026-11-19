"""
> [!NOTE]
> Модуль тестов tests.test_run_llama_server: Проверка скриптов запуска
> локального сервера инференса llama.cpp (run_llama_server.bat и run_llama_server.ps1).
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class TestRunLlamaServerScripts:
    """Тестирование скриптов запуска llama-server."""

    def test_scripts_exist_and_contain_required_arguments(self) -> None:
        """Проверка наличия файлов скриптов и обязательных аргументов VLM инференса."""
        project_root = Path(__file__).resolve().parent.parent
        bat_file = project_root / "scripts" / "run_llama_server.bat"
        ps1_file = project_root / "scripts" / "run_llama_server.ps1"

        assert bat_file.exists(), "Скрипт run_llama_server.bat должен существовать"
        assert ps1_file.exists(), "Скрипт run_llama_server.ps1 должен существовать"

        required_snippets = [
            "qwen2.5-vl-3b-instruct-q4_k_m.gguf",
            "qwen2.5-vl-3b-mmproj-f16.gguf",
            "-ngl",
            "99",
            "8080",
            "127.0.0.1",
            "4096",
            "https://github.com/ggerganov/llama.cpp/releases",
        ]

        bat_content = bat_file.read_text(encoding="utf-8")
        for snippet in required_snippets:
            assert snippet in bat_content, f"В bat файле отсутствует: {snippet}"

        ps1_content = ps1_file.read_text(encoding="utf-8")
        for snippet in required_snippets:
            assert snippet in ps1_content, f"В ps1 файле отсутствует: {snippet}"

    def test_bat_script_execution_without_binary(self) -> None:
        """Проверка вежливого сообщения об отсутствии бинарника при запуске .bat файла."""
        project_root = Path(__file__).resolve().parent.parent
        bat_file = project_root / "scripts" / "run_llama_server.bat"

        result = subprocess.run(
            ["cmd.exe", "/c", str(bat_file), "--no-pause"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        assert result.returncode == 1
        assert "llama-server.exe" in result.stdout
        assert "https://github.com/ggerganov/llama.cpp/releases" in result.stdout

    def test_ps1_script_execution_without_binary(self) -> None:
        """Проверка вежливого сообщения об отсутствии бинарника при запуске .ps1 файла."""
        project_root = Path(__file__).resolve().parent.parent
        ps1_file = project_root / "scripts" / "run_llama_server.ps1"

        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ps1_file),
                "-NoPause",
            ],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        assert result.returncode == 1
        assert "llama-server.exe" in result.stdout
        assert "https://github.com/ggerganov/llama.cpp/releases" in result.stdout
