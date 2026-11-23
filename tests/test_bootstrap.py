"""Тесты для системы автоматической проверки и загрузки зависимостей при запуске .exe.

Проверяют:
1. Диагностику недостающих и установленных зависимостей (DependencyManager.inspect).
2. Логику показа окна разрешения (should_prompt_user, skip_prompt, --setup, --skip-setup).
3. Потоковую загрузку, докачку HTTP 206, резервное зеркало, отмену (cancel_event).
4. Выбор релиза llama.cpp (Vulkan -> CPU) и защиту распаковки от Zip Slip.
5. Автоподхват локальной папки Faster-Whisper после скачивания.
6. Fluent Dark GUI диалог DependencySetupDialog (выбор, автовыбор llama-server, прогресс, трей).
"""

from __future__ import annotations

import os
import sys
import threading
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from core.bootstrap import (
    MIN_PIPER_BYTES,
    MIN_VAD_BYTES,
    MIN_VLM_BYTES,
    MIN_WHISPER_BYTES,
    DependencyManager,
    DownloadProgress,
    _safe_extract_zip,
    _select_llama_asset,
    format_size_ru,
)
from core.paths import get_bin_dir
from core.voice.transcriber import FasterWhisperTranscriber
from gui.setup_dialog import DependencySetupDialog, run_startup_dependency_check
from gui.tray_icon import TrayManager
from main import parse_arguments


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """Обеспечивает offscreen QApplication для headless-тестирования GUI-диалога."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    return app


def _make_fake_response(
    chunks: list[bytes],
    status_code: int = 200,
    content_length: int | None = None,
    json_payload: Any = None,
) -> MagicMock:
    """Создает мок-объект requests.Response для контекстного менеджера."""
    resp = MagicMock()
    resp.status_code = status_code
    total = content_length if content_length is not None else sum(len(c) for c in chunks)
    resp.headers = {"Content-Length": str(total)}
    resp.iter_content.return_value = iter(chunks)
    resp.raise_for_status.return_value = None
    if json_payload is not None:
        resp.json.return_value = json_payload
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class TestDependencyManagerCore:
    """Модульные тесты ядра core.bootstrap."""

    def test_inspect_empty_vs_populated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Проверка обнаружения отсутствующих и установленных компонентов."""
        models_dir = tmp_path / "models"
        bin_dir = tmp_path / "bin"
        state_file = tmp_path / "config" / "bootstrap_state.json"

        # Изолируем поиск llama-server от реального PATH/корня
        monkeypatch.setattr("core.bootstrap.shutil.which", lambda _name: None)
        monkeypatch.setattr("core.bootstrap.get_bundle_root", lambda: tmp_path / "bundle")

        manager = DependencyManager(models_dir=models_dir, bin_dir=bin_dir, state_file=state_file)
        report = manager.inspect()

        assert len(report.items) == 5
        assert {it.id for it in report.missing_required} == {
            "silero_vad",
            "piper_tts",
            "faster_whisper",
        }
        assert {it.id for it in report.missing_optional} == {"vlm_qwen", "llama_server"}

        # Создаем валидные заглушки всех файлов
        models_dir.mkdir(parents=True, exist_ok=True)
        (models_dir / "silero_vad.onnx").write_bytes(b"v" * (MIN_VAD_BYTES + 1))

        piper_dir = models_dir / "piper"
        piper_dir.mkdir(parents=True)
        (piper_dir / "ru_RU-dmitri-medium.onnx").write_bytes(b"p" * (MIN_PIPER_BYTES + 1))
        (piper_dir / "ru_RU-dmitri-medium.onnx.json").write_text(
            '{"audio": {"sample_rate": 22050}, "espeak": {"voice": "ru"}}',
            encoding="utf-8",
        )

        whisper_dir = models_dir / "whisper"
        whisper_dir.mkdir(parents=True)
        (whisper_dir / "model.bin").write_bytes(b"w" * (MIN_WHISPER_BYTES + 1))
        (whisper_dir / "config.json").write_text('{"model": "base"}', encoding="utf-8")

        llm_dir = models_dir / "llm"
        llm_dir.mkdir(parents=True)
        (llm_dir / "qwen3-vl-4b-instruct-q4_k_m.gguf").write_bytes(b"0")
        (llm_dir / "mmproj-qwen3-vl-4b-instruct-q8_0.gguf").write_bytes(b"0")
        # Для VLM минимальный порог 100MB, подменяем порог проверкой через sparse/mock
        with patch("core.bootstrap.MIN_VLM_BYTES", 1):
            bin_dir.mkdir(parents=True, exist_ok=True)
            (bin_dir / "llama-server.exe").write_bytes(b"MZ")
            report_full = manager.inspect()

        assert report_full.missing_required == []
        assert report_full.missing_optional == []
        assert MIN_VLM_BYTES >= 100 * 1024 * 1024

    def test_should_prompt_user_rules_and_persistence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Проверка правил показа стартового окна загрузки и сохранения skip_prompt."""
        monkeypatch.setattr("core.bootstrap.shutil.which", lambda _name: None)
        monkeypatch.setattr("core.bootstrap.get_bundle_root", lambda: tmp_path / "bundle")

        manager = DependencyManager(
            models_dir=tmp_path / "models",
            bin_dir=tmp_path / "bin",
            state_file=tmp_path / "config" / "bootstrap_state.json",
        )

        # В интерактивном режиме при отсутствии голосовых моделей должен спрашивать
        assert manager.should_prompt_user(headless=False, quiet=False, enable_voice=True) is True
        # В headless / quiet / skip_setup / no_voice — не блокирует запуск
        assert manager.should_prompt_user(headless=True) is False
        assert manager.should_prompt_user(skip_setup=True) is False
        assert manager.should_prompt_user(quiet=True) is False
        assert manager.should_prompt_user(enable_voice=False) is False

        # После сохранения «Не показывать снова» не спрашивает автоматически
        manager.save_skip_prompt(True)
        assert manager.is_skip_prompt_saved() is True
        assert manager.should_prompt_user(headless=False, quiet=False) is False
        # Но --setup принудительно открывает окно
        assert manager.should_prompt_user(force_setup=True) is True

    def test_download_with_resume_and_backup_mirror(self, tmp_path: Path) -> None:
        """Проверка потоковой загрузки с переходом на резервное зеркало и докачкой HTTP 206."""
        models_dir = tmp_path / "models"
        manager = DependencyManager(
            models_dir=models_dir,
            bin_dir=tmp_path / "bin",
            state_file=tmp_path / "state.json",
        )

        # Создаем частичный .part файл для проверки Range-докачки
        models_dir.mkdir(parents=True, exist_ok=True)
        part_file = models_dir / "silero_vad.onnx.part"
        part_file.write_bytes(b"HEAD")

        fail_resp = MagicMock()
        fail_resp.__enter__.side_effect = ConnectionError("Primary mirror down")
        ok_resp = _make_fake_response([b"TAIL" * 30000], status_code=206)

        mock_session = MagicMock()
        mock_session.get.side_effect = [fail_resp, ok_resp]

        progress_events: list[DownloadProgress] = []
        result = manager.download_items(
            ["silero_vad"],
            on_progress=progress_events.append,
            session=mock_session,
        )

        assert result.ok is True
        assert result.succeeded == ["silero_vad"]
        dest = models_dir / "silero_vad.onnx"
        assert dest.is_file()
        assert dest.read_bytes().startswith(b"HEADTAIL")
        assert not part_file.exists()
        assert len(progress_events) > 0
        # Проверяем, что передавался заголовок Range для докачки
        second_call_headers = mock_session.get.call_args_list[1].kwargs["headers"]
        assert second_call_headers["Range"] == "bytes=4-"

    def test_download_cancellation(self, tmp_path: Path) -> None:
        """Проверка немедленной отмены загрузки через threading.Event."""
        manager = DependencyManager(
            models_dir=tmp_path / "models",
            bin_dir=tmp_path / "bin",
            state_file=tmp_path / "state.json",
        )
        cancel_event = threading.Event()

        def _chunk_gen() -> Any:
            yield b"a" * 1024
            cancel_event.set()
            yield b"b" * 1024

        resp = _make_fake_response([], status_code=200, content_length=2048)
        resp.iter_content.return_value = _chunk_gen()
        mock_session = MagicMock()
        mock_session.get.return_value = resp

        result = manager.download_items(
            ["silero_vad"],
            cancel_event=cancel_event,
            session=mock_session,
        )
        assert result.cancelled is True
        assert result.ok is False
        assert not (tmp_path / "models" / "silero_vad.onnx").exists()

    def test_download_rejects_truncated_file(self, tmp_path: Path) -> None:
        """Проверка отклонения урезанного или поврежденного файла (меньше min_bytes)."""
        manager = DependencyManager(
            models_dir=tmp_path / "models",
            bin_dir=tmp_path / "bin",
            state_file=tmp_path / "state.json",
        )
        # Оба зеркала возвращают HTTP 200 с HTML-заглушкой 20 байт вместо ONNX (>100 КБ)
        mock_session = MagicMock()
        mock_session.get.side_effect = [
            _make_fake_response([b"<html>error</html>"], status_code=200),
            _make_fake_response([b"<html>error</html>"], status_code=200),
        ]

        result = manager.download_items(["silero_vad"], session=mock_session)
        assert result.ok is False
        assert "silero_vad" in result.failed
        assert not (tmp_path / "models" / "silero_vad.onnx").exists()
        assert not (tmp_path / "models" / "silero_vad.onnx.part").exists()

    def test_select_llama_asset_and_zip_slip_protection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Проверка выбора архива llama.cpp (Vulkan -> CPU) и защиты от Zip Slip."""
        releases = [
            {
                "tag_name": "v1.0.0",
                "assets": [{"name": "nightly-tag.txt", "browser_download_url": "https://x/1"}],
            },
            {
                "tag_name": "b12651",
                "assets": [
                    {
                        "name": "llama-b12651-bin-win-cpu-x64.zip",
                        "browser_download_url": "https://x/cpu.zip",
                    },
                    {
                        "name": "llama-b12651-bin-win-vulkan-x64.zip",
                        "browser_download_url": "https://x/vulkan.zip",
                    },
                ],
            },
        ]
        selected = _select_llama_asset(releases)
        assert selected == ("llama-b12651-bin-win-vulkan-x64.zip", "https://x/vulkan.zip")

        # Проверка защиты от Zip Slip
        bad_zip = tmp_path / "evil.zip"
        with zipfile.ZipFile(bad_zip, "w") as zf:
            zf.writestr("../../escaped.txt", "evil")

        with pytest.raises(ValueError, match="небезопасный путь"):
            _safe_extract_zip(bad_zip, tmp_path / "extract")

        # Проверка полной загрузки и распаковки llama_server
        good_zip = tmp_path / "good.zip"
        with zipfile.ZipFile(good_zip, "w") as zf:
            zf.writestr("llama-server.exe", b"MZ-FAKE-BINARY" * 200)
        zip_bytes = good_zip.read_bytes()

        monkeypatch.setattr("core.bootstrap.shutil.which", lambda _name: None)
        monkeypatch.setattr("core.bootstrap.get_bundle_root", lambda: tmp_path / "bundle")

        api_resp = MagicMock()
        api_resp.raise_for_status.return_value = None
        api_resp.json.return_value = releases
        dl_resp = _make_fake_response([zip_bytes], status_code=200)

        mock_session = MagicMock()
        mock_session.get.side_effect = [api_resp, dl_resp]

        manager = DependencyManager(
            models_dir=tmp_path / "models",
            bin_dir=tmp_path / "bin",
            state_file=tmp_path / "state.json",
        )
        res = manager.download_items(["llama_server"], session=mock_session)
        assert res.ok is True
        assert manager.find_llama_server() == tmp_path / "bin" / "llama-server.exe"

    def test_format_size_and_get_bin_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Проверка форматирования размеров и виртуализации пути bin/."""
        assert format_size_ru(2.2) == "2.2 МБ"
        assert format_size_ru(138.5) == "138 МБ" or format_size_ru(138.5) == "139 МБ"
        assert format_size_ru(3117.0) == "3.0 ГБ"

        monkeypatch.setenv("GUPKA_DATA_DIR", str(tmp_path))
        bin_dir = get_bin_dir()
        assert bin_dir == tmp_path / "bin"
        assert bin_dir.is_dir()

    def test_transcriber_picks_up_local_whisper_dir(self, tmp_path: Path) -> None:
        """Проверка загрузки Faster-Whisper напрямую из скачанной папки download_root."""
        whisper_dir = tmp_path / "whisper"
        whisper_dir.mkdir()
        (whisper_dir / "model.bin").write_bytes(b"fake")
        (whisper_dir / "config.json").write_text("{}", encoding="utf-8")

        mock_factory = MagicMock(return_value=MagicMock())
        transcriber = FasterWhisperTranscriber(
            model_size_or_path="base",
            download_root=str(whisper_dir),
            model_factory=mock_factory,
        )
        assert transcriber.initialize() is True
        called_target = mock_factory.call_args.args[0]
        assert called_target == str(whisper_dir)


class TestDependencySetupDialogGUI:
    """Headless GUI тесты окна разрешения и загрузки зависимостей."""

    def test_dialog_defaults_and_vlm_auto_links_llama_server(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Проверка выбора голосовых моделей по умолчанию и автовыбора llama-server для VLM."""
        monkeypatch.setattr("core.bootstrap.shutil.which", lambda _name: None)
        monkeypatch.setattr("core.bootstrap.get_bundle_root", lambda: tmp_path / "bundle")

        manager = DependencyManager(
            models_dir=tmp_path / "models",
            bin_dir=tmp_path / "bin",
            state_file=tmp_path / "state.json",
        )
        dialog = DependencySetupDialog(manager=manager)

        # По умолчанию отмечены только 3 базовых голосовых компонента
        assert set(dialog.get_selected_missing_ids()) == {
            "silero_vad",
            "piper_tts",
            "faster_whisper",
        }
        assert dialog.checkboxes["vlm_qwen"].isChecked() is False
        assert dialog.checkboxes["llama_server"].isChecked() is False

        # При выборе VLM автоматически отмечается llama-server
        dialog.checkboxes["vlm_qwen"].setChecked(True)
        assert dialog.checkboxes["llama_server"].isChecked() is True
        dialog.close()

    def test_dialog_download_completion_updates_ui(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Проверка полного цикла обновления прогресса и статуса карточек после загрузки."""
        monkeypatch.setattr("core.bootstrap.shutil.which", lambda _name: None)
        monkeypatch.setattr("core.bootstrap.get_bundle_root", lambda: tmp_path / "bundle")

        manager = DependencyManager(
            models_dir=tmp_path / "models",
            bin_dir=tmp_path / "bin",
            state_file=tmp_path / "state.json",
        )
        dialog = DependencySetupDialog(manager=manager)
        # Снимаем все, кроме silero_vad
        dialog.checkboxes["piper_tts"].setChecked(False)
        dialog.checkboxes["faster_whisper"].setChecked(False)

        def _fake_download(
            item_ids: list[str],
            on_progress: Any = None,
            cancel_event: Any = None,
            session: Any = None,
        ) -> Any:
            from core.bootstrap import DownloadResult

            target = tmp_path / "models" / "silero_vad.onnx"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"v" * (MIN_VAD_BYTES + 1))
            if on_progress is not None:
                on_progress(
                    DownloadProgress(
                        item_id="silero_vad",
                        item_name="Silero VAD v5 (ONNX)",
                        file_label="Silero VAD v5",
                        downloaded_bytes=MIN_VAD_BYTES,
                        total_bytes=MIN_VAD_BYTES,
                        item_index=1,
                        total_items=1,
                        overall_percent=100.0,
                        speed_mbps=25.0,
                        status_text="Готово",
                    )
                )
            return DownloadResult(succeeded=list(item_ids))

        monkeypatch.setattr(manager, "download_items", _fake_download)

        dialog._on_primary_clicked()
        assert dialog._worker is not None
        dialog._worker.wait(3000)
        qapp.processEvents()

        assert dialog.last_result is not None
        assert dialog.last_result.ok is True
        assert dialog.checkboxes["silero_vad"].isEnabled() is False
        dialog.close()

    def test_startup_check_headless_skips_modal_and_cli_flags(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        """Проверка флагов --setup / --skip-setup, трея и спецификации PyInstaller."""
        manager = DependencyManager(
            models_dir=tmp_path / "models",
            bin_dir=tmp_path / "bin",
            state_file=tmp_path / "state.json",
        )
        assert run_startup_dependency_check(manager=manager, headless=True) is False
        assert run_startup_dependency_check(manager=manager, skip_setup=True) is False

        # Проверка CLI-аргументов main.py
        orig_argv = sys.argv
        try:
            sys.argv = ["main.py", "--setup", "--skip-setup"]
            args = parse_arguments()
            assert args.setup is True
            assert args.skip_setup is True
        finally:
            sys.argv = orig_argv

        # Проверка пункта меню в системном трее
        tray = TrayManager()
        triggered = [False]
        tray.models_triggered.connect(lambda: triggered.__setitem__(0, True))
        assert tray.models_action.text() == "Модели и зависимости..."
        tray.models_action.trigger()
        assert triggered[0] is True

        # Проверка включения новых модулей в GupkaCommander.spec
        spec_text = (Path(__file__).resolve().parent.parent / "GupkaCommander.spec").read_text(
            encoding="utf-8"
        )
        assert "'core.bootstrap'" in spec_text
        assert "'gui.setup_dialog'" in spec_text
