"""
> [!NOTE]
> Модуль тестов tests.test_deployment_scripts: Комплексная верификация инструментов
> развертывания Спринта 5 (скрипты загрузки моделей, генерации системных иконок,
> создания Windows-ярлыков и подхвата путей моделей в компонентах речевого контура).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from PIL import Image

from core.voice.synthesizer import PiperSynthesizer
from core.voice.transcriber import FasterWhisperTranscriber
from core.voice.vad_detector import VADDetector
from scripts.create_shortcut import (
    ShortcutConfig,
    create_shortcut_com,
    create_shortcut_file,
    create_shortcut_powershell,
    setup_shortcuts,
)
from scripts.create_shortcut import (
    main as create_shortcut_main,
)
from scripts.download_models import (
    _should_skip_download,
    build_argument_parser,
    download_file,
    download_piper_tts,
    download_silero_vad,
    download_vlm_models,
    download_voice_models,
)
from scripts.download_models import (
    main as download_models_main,
)
from scripts.generate_icon import (
    build_icon_layers,
    create_gradient_background,
    generate_icon_files,
    render_master_icon,
)
from scripts.generate_icon import (
    main as generate_icon_main,
)


class TestDownloadModelsCLI:
    """Тестирование CLI-аргументов и алгоритмов загрузки моделей."""

    def test_build_argument_parser_defaults(self) -> None:
        """Проверка значений аргументов парсера по умолчанию."""
        parser = build_argument_parser()
        args = parser.parse_args([])

        assert args.models_dir == "models"
        assert args.all is False
        assert args.voice_only is False
        assert args.vlm_only is False
        assert args.with_vlm is False
        assert args.force is False

    def test_build_argument_parser_custom_flags(self) -> None:
        """Проверка парсинга пользовательских CLI-флагов."""
        parser = build_argument_parser()
        args = parser.parse_args(
            [
                "--models-dir",
                "custom_models",
                "--voice-only",
                "--force",
            ]
        )

        assert args.models_dir == "custom_models"
        assert args.voice_only is True
        assert args.force is True
        assert args.all is False

    @pytest.mark.parametrize(
        ("file_exists", "file_size", "force", "expected"),
        [
            (False, 0, False, False),
            (True, 1024, False, True),
            (True, 1024, True, False),
            (True, 0, False, False),
        ],
    )
    def test_should_skip_download(
        self,
        tmp_path: Path,
        file_exists: bool,
        file_size: int,
        force: bool,
        expected: bool,
    ) -> None:
        """Проверка условий пропуска скачивания существующего файла."""
        test_file = tmp_path / "test_model.onnx"
        if file_exists:
            test_file.write_bytes(b"x" * file_size)

        result = _should_skip_download(test_file, force=force, description="Тестовая модель")
        assert result is expected

    def test_download_file_skip_existing(self, tmp_path: Path) -> None:
        """Проверка мгновенного пропуска загрузки при наличии готового файла."""
        existing_file = tmp_path / "model.onnx"
        existing_file.write_bytes(b"content")

        with patch("requests.Session.get") as mock_get:
            success = download_file("http://example.com/model.onnx", existing_file, force=False)
            assert success is True
            mock_get.assert_not_called()

    def test_download_file_stream_success(self, tmp_path: Path) -> None:
        """Проверка успешной потоковой загрузки файла через requests."""
        target_file = tmp_path / "sub" / "downloaded.bin"

        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Length": "12"}
        mock_resp.iter_content.return_value = [b"chunk1_", b"chunk2"]
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        mock_session = MagicMock(spec=requests.Session)
        mock_session.get.return_value = mock_resp

        success = download_file(
            url="http://example.com/file.bin",
            dest_path=target_file,
            description="Тестовый бинарник",
            force=True,
            session=mock_session,
        )

        assert success is True
        assert target_file.is_file()
        assert target_file.read_bytes() == b"chunk1_chunk2"
        # Временный .part файл должен быть перемещен
        assert not target_file.with_suffix(".bin.part").exists()

    def test_download_file_range_resume(self, tmp_path: Path) -> None:
        """Проверка докачки при наличии частичного файла .part."""
        target_file = tmp_path / "resume_test.bin"
        part_file = target_file.with_suffix(".bin.part")
        part_file.write_bytes(b"initial_")

        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.status_code = 206
        mock_resp.headers = {"Content-Length": "8"}
        mock_resp.iter_content.return_value = [b"appended"]
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        mock_session = MagicMock(spec=requests.Session)
        mock_session.get.return_value = mock_resp

        success = download_file(
            url="http://example.com/file.bin",
            dest_path=target_file,
            force=False,
            session=mock_session,
        )

        assert success is True
        mock_session.get.assert_called_once()
        headers = mock_session.get.call_args[1].get("headers", {})
        assert headers.get("Range") == "bytes=8-"
        assert target_file.read_bytes() == b"initial_appended"

    def test_download_file_network_error(self, tmp_path: Path) -> None:
        """Проверка обработки сетевой ошибки при скачивании."""
        target_file = tmp_path / "failed.bin"

        mock_session = MagicMock(spec=requests.Session)
        mock_session.get.side_effect = requests.RequestException("Connection refused")

        success = download_file(
            url="http://example.com/fail.bin",
            dest_path=target_file,
            session=mock_session,
        )

        assert success is False
        assert not target_file.exists()

    def test_download_functions_destinations(self, tmp_path: Path) -> None:
        """Проверка формирования корректных целевых путей функциями загрузки."""
        with (
            patch("scripts.download_models.download_file", return_value=True) as mock_dl,
            patch(
                "faster_whisper.download_model", return_value=str(tmp_path / "whisper")
            ) as mock_whisper_dl,
        ):
            # 1. Silero VAD
            vad_res = download_silero_vad(tmp_path)
            assert vad_res == tmp_path / "silero_vad.onnx"

            # 2. Piper TTS
            piper_model, piper_cfg = download_piper_tts(tmp_path)
            assert piper_model == tmp_path / "piper" / "ru_RU-dmitri-medium.onnx"
            assert piper_cfg == tmp_path / "piper" / "ru_RU-dmitri-medium.onnx.json"

            # 3. VLM Models
            vlm_files = download_vlm_models(tmp_path)
            assert len(vlm_files) == 2
            assert vlm_files[0].name.endswith(".gguf")

            # 4. Voice Models Combo
            voice_files = download_voice_models(tmp_path)
            assert len(voice_files) >= 4

            assert mock_dl.call_count >= 5
            mock_whisper_dl.assert_called_once()

    def test_download_models_main_cli_modes(self, tmp_path: Path) -> None:
        """Проверка ветвления выполнения в функции main() по CLI-аргументам."""
        with (
            patch("scripts.download_models.download_voice_models") as mock_voice,
            patch("scripts.download_models.download_vlm_models") as mock_vlm,
        ):
            # Режим только голос
            code_voice = download_models_main(["--voice-only", "--models-dir", str(tmp_path)])
            assert code_voice == 0
            mock_voice.assert_called_once()
            mock_vlm.assert_not_called()

            mock_voice.reset_mock()
            mock_vlm.reset_mock()

            # Режим только VLM
            code_vlm = download_models_main(["--vlm-only", "--models-dir", str(tmp_path)])
            assert code_vlm == 0
            mock_voice.assert_not_called()
            mock_vlm.assert_called_once()

            mock_voice.reset_mock()
            mock_vlm.reset_mock()

            # Режим all
            code_all = download_models_main(["--all", "--models-dir", str(tmp_path)])
            assert code_all == 0
            mock_voice.assert_called_once()
            mock_vlm.assert_called_once()


class TestGenerateIcon:
    """Тестирование генерации системной иконки Windows и мастер-изображений."""

    def test_gradient_and_master_render(self) -> None:
        """Проверка генерации базовых слоев изображения и их параметров."""
        bg = create_gradient_background(size=64, radius=12)
        assert isinstance(bg, Image.Image)
        assert bg.size == (64, 64)
        assert bg.mode == "RGBA"

        master = render_master_icon(size=128)
        assert isinstance(master, Image.Image)
        assert master.size == (128, 128)
        assert master.mode == "RGBA"

    def test_build_icon_layers_count_and_sizes(self) -> None:
        """Проверка корректного масштабирования всех слоев иконки."""
        master = render_master_icon(size=256)
        sizes = (16, 32, 48, 64)
        layers = build_icon_layers(master, sizes=sizes)

        assert len(layers) == len(sizes)
        for layer, exp_size in zip(layers, sizes, strict=True):
            assert layer.size == (exp_size, exp_size)

    def test_generate_icon_files_integrity(self, tmp_path: Path) -> None:
        """Проверка создания валидного файла .ico и .png на диске."""
        out_ico = tmp_path / "icon_output.ico"
        out_png = tmp_path / "icon_output.png"
        custom_sizes = (16, 32, 64)

        ico_res, png_res = generate_icon_files(
            output_ico=out_ico,
            output_png=out_png,
            sizes=custom_sizes,
        )

        assert ico_res.is_file()
        assert out_ico.stat().st_size > 0
        assert png_res is not None
        assert png_res.is_file()

        # Валидация Pillow
        with Image.open(out_ico) as img:
            assert img.format == "ICO"
            actual_sizes = img.info.get("sizes")
            assert actual_sizes == {(16, 16), (32, 32), (64, 64)}

        with Image.open(out_png) as p_img:
            assert p_img.format == "PNG"
            assert p_img.mode == "RGBA"

    def test_generate_icon_main_cli(self, tmp_path: Path) -> None:
        """Проверка точки входа CLI скрипта генерации иконки."""
        out_ico = tmp_path / "cli_icon.ico"
        out_png = tmp_path / "cli_icon.png"

        test_args = ["generate_icon.py", "-o", str(out_ico), "--png", str(out_png)]
        with patch.object(sys, "argv", test_args):
            generate_icon_main()

        assert out_ico.is_file()
        assert out_png.is_file()


class TestCreateShortcut:
    """Тестирование создания ярлыков Windows (.lnk) через COM и PowerShell."""

    def test_create_shortcut_com_success(self, tmp_path: Path) -> None:
        """Проверка успешного вызова COM-объекта WScript.Shell через win32com."""
        target_lnk = tmp_path / "AppShortcut.lnk"
        cfg = ShortcutConfig(
            target_path="C:\\Windows\\System32\\cmd.exe",
            arguments="/c echo hello",
            working_dir=str(tmp_path),
            icon_location="C:\\icon.ico,0",
            description="Описание ярлыка",
        )

        mock_shell = MagicMock()
        mock_shortcut = MagicMock()
        mock_shell.CreateShortcut.return_value = mock_shortcut

        with patch("win32com.client.Dispatch", return_value=mock_shell):
            create_shortcut_com(target_lnk, cfg)

            mock_shell.CreateShortcut.assert_called_once_with(str(target_lnk))
            assert mock_shortcut.TargetPath == cfg.target_path
            assert mock_shortcut.Arguments == cfg.arguments
            assert mock_shortcut.WorkingDirectory == cfg.working_dir
            assert mock_shortcut.IconLocation == cfg.icon_location
            assert mock_shortcut.Description == cfg.description
            mock_shortcut.Save.assert_called_once()

    def test_create_shortcut_powershell_execution(self, tmp_path: Path) -> None:
        """Проверка команды создания ярлыка через PowerShell."""
        target_lnk = tmp_path / "PsShortcut.lnk"
        cfg = ShortcutConfig(
            target_path="C:\\app.exe",
            arguments="--flag",
            working_dir=str(tmp_path),
            icon_location="",
            description="PS Shortcut",
        )

        with patch("subprocess.run") as mock_subproc:
            create_shortcut_powershell(target_lnk, cfg)

            mock_subproc.assert_called_once()
            cmd = mock_subproc.call_args[0][0]
            assert cmd[0] == "powershell"
            assert "New-Object -ComObject WScript.Shell" in cmd[4]
            assert str(target_lnk) in cmd[4]

    def test_create_shortcut_file_com_fallback_to_powershell(self, tmp_path: Path) -> None:
        """Проверка каскадного переключения на PowerShell при сбое win32com."""
        target_lnk = tmp_path / "Fallback.lnk"
        cfg = ShortcutConfig(
            target_path="C:\\app.exe",
            arguments="",
            working_dir=str(tmp_path),
            icon_location="",
            description="Fallback",
        )

        with (
            patch("win32com.client.Dispatch", side_effect=ImportError("No win32com")),
            patch("subprocess.run") as mock_run,
        ):
            res = create_shortcut_file(target_lnk, cfg, location_label="TestDesk")

            assert res.success is True
            assert res.method == "powershell"
            mock_run.assert_called_once()

    def test_create_shortcut_file_complete_failure(self, tmp_path: Path) -> None:
        """Проверка обработки ситуации, когда и COM, и PowerShell завершаются сбоем."""
        target_lnk = tmp_path / "Fail.lnk"
        cfg = ShortcutConfig(
            target_path="C:\\app.exe",
            arguments="",
            working_dir=str(tmp_path),
            icon_location="",
            description="Fail",
        )

        with (
            patch("win32com.client.Dispatch", side_effect=RuntimeError("COM err")),
            patch("subprocess.run", side_effect=RuntimeError("PS err")),
        ):
            res = create_shortcut_file(target_lnk, cfg, location_label="TestFail")

            assert res.success is False
            assert res.method == "failed"
            assert res.error_message is not None

    def test_setup_shortcuts_desktop_and_startup(self, tmp_path: Path) -> None:
        """Проверка вызова настройки ярлыков для рабочего стола и автозагрузки."""
        with (
            patch(
                "scripts.create_shortcut.get_desktop_dir",
                return_value=tmp_path / "Desktop",
            ),
            patch(
                "scripts.create_shortcut.get_startup_dir",
                return_value=tmp_path / "Startup",
            ),
            patch("scripts.create_shortcut.create_shortcut_file") as mock_create,
        ):
            mock_create.return_value = MagicMock(success=True)

            results = setup_shortcuts(desktop=True, startup=True, target_mode="auto")

            assert len(results) == 2
            assert mock_create.call_count == 2

    def test_create_shortcut_main_cli(self) -> None:
        """Проверка точки входа CLI main() для create_shortcut."""
        test_args = ["create_shortcut.py", "--desktop", "--startup", "--target-mode", "auto"]
        with (
            patch.object(sys, "argv", test_args),
            patch("scripts.create_shortcut.setup_shortcuts") as mock_setup,
        ):
            mock_setup.return_value = [
                MagicMock(
                    location_name="Desktop", file_path="C:\\d.lnk", success=True, method="com"
                )
            ]
            create_shortcut_main()
            mock_setup.assert_called_once_with(
                desktop=True,
                startup=True,
                target_mode="auto",
                shortcut_name="GupkaCommander",
            )


class TestModelsPathPickup:
    """Тестирование автоматического подхвата путей моделей из каталога models/."""

    def test_vad_detector_picks_up_models_directory(self) -> None:
        """Проверка автоматического обнаружения silero_vad.onnx в каталоге models/."""
        vad = VADDetector()
        # В проекте файл models/silero_vad.onnx существует
        assert vad.model_path is not None
        assert "silero_vad.onnx" in str(vad.model_path)
        assert Path(vad.model_path).is_file()

    def test_vad_detector_explicit_path_and_fallback(self, tmp_path: Path) -> None:
        """Проверка явного указания пути к модели и корректного fallback при отсутствии."""
        # 1. Существующий путь
        custom_model = tmp_path / "custom_vad.onnx"
        custom_model.write_bytes(b"dummy")
        vad_custom = VADDetector(model_path=custom_model)
        assert vad_custom.model_path == custom_model.resolve()

        # 2. Несуществующий путь
        vad_missing = VADDetector(model_path=tmp_path / "missing.onnx")
        assert vad_missing.model_path is None
        assert not vad_missing.using_onnx

    def test_piper_synthesizer_picks_up_models_directory(self) -> None:
        """Проверка путей по умолчанию к модели и конфигурации Piper TTS."""
        synth = PiperSynthesizer()
        assert synth.model_path is not None
        assert "ru_RU-dmitri-medium.onnx" in str(synth.model_path)
        assert synth.config_path is not None
        assert "ru_RU-dmitri-medium.onnx.json" in str(synth.config_path)

    def test_piper_synthesizer_custom_paths(self, tmp_path: Path) -> None:
        """Проверка переопределения путей к моделям синтезатора речи."""
        custom_m = tmp_path / "voice.onnx"
        custom_c = tmp_path / "voice.onnx.json"
        synth = PiperSynthesizer(model_path=custom_m, config_path=custom_c)

        assert synth.model_path == custom_m
        assert synth.config_path == custom_c

    def test_faster_whisper_picks_up_download_root(self) -> None:
        """Проверка указания models/whisper в качестве корневого каталога кэша."""
        transcriber = FasterWhisperTranscriber()
        assert transcriber.download_root == "models/whisper"

        custom_transcriber = FasterWhisperTranscriber(download_root="custom/cache")
        assert custom_transcriber.download_root == "custom/cache"
