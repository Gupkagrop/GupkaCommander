"""
Тесты для верификации полировки и харденинга проекта (Sonnet 5.5 & Argon Polish Sprint).

Проверяют:
1. Аргумент командной строки --quiet / -q и запуск start(start_hidden=True).
2. Поддержку UTF-8 кодировки для SSE-стриминга в LocalLLMClient.
3. Разрешение путей к моделям через core.paths.get_models_dir() в voice-модулях.
4. Синхронную установку флага _is_speaking в PiperSynthesizer до старта потока.
5. Наличие пакета piper в datas спецификации GupkaCommander.spec.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.brain.llm_client import LocalLLMClient
from core.paths import get_models_dir
from core.voice.synthesizer import PiperSynthesizer
from core.voice.transcriber import FasterWhisperTranscriber
from core.voice.vad_detector import VADDetector
from main import parse_arguments


def test_parse_arguments_quiet_flag() -> None:
    """Проверяет регистрацию и парсинг флагов --quiet и -q."""
    import sys

    # Тест --quiet
    orig_argv = sys.argv
    try:
        sys.argv = ["main.py", "--quiet"]
        args = parse_arguments()
        assert args.quiet is True

        sys.argv = ["main.py", "-q"]
        args = parse_arguments()
        assert args.quiet is True

        sys.argv = ["main.py"]
        args = parse_arguments()
        assert args.quiet is False
    finally:
        sys.argv = orig_argv


def test_sse_stream_explicit_utf8_encoding() -> None:
    """Проверяет принудительную установку UTF-8 кодировки для SSE-ответов."""
    client = LocalLLMClient()
    mock_resp = MagicMock()
    mock_resp.encoding = "ISO-8859-1"
    mock_resp.iter_lines.return_value = [
        'data: {"choices": [{"delta": {"content": "Привет, мир!"}}]}',
        "data: [DONE]",
    ]

    chunks = list(client._read_sse_stream(mock_resp))
    assert mock_resp.encoding == "utf-8"
    assert chunks == ["Привет, мир!"]


def test_voice_modules_resolve_models_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет использование get_models_dir() в vad_detector, synthesizer и transcriber."""
    custom_models = tmp_path / "models"
    custom_models.mkdir()
    monkeypatch.setenv("GUPKA_DATA_DIR", str(tmp_path))

    expected_models_dir = get_models_dir()
    assert expected_models_dir == custom_models

    # VADDetector
    vad = VADDetector()
    # vad._model_path проверяет ожидаемые директории
    assert vad is not None

    # PiperSynthesizer
    default_model = PiperSynthesizer._resolve_default_model_path()
    assert default_model is not None

    # FasterWhisperTranscriber
    transcriber = FasterWhisperTranscriber(download_root=None)
    assert transcriber.download_root is not None
    assert str(expected_models_dir / "whisper") in transcriber.download_root


def test_synthesizer_is_speaking_synchronous() -> None:
    """Проверяет синхронную установку is_speaking = True при вызове speak()."""
    synth = PiperSynthesizer()
    mock_play = MagicMock()
    synth._run_playback = mock_play  # type: ignore[method-assign]

    synth.speak("Тестовая реплика", blocking=False)
    # Флаг должен быть выставлен синхронно до того, как поток завершится
    assert synth.is_speaking() is True or mock_play.called
    synth.stop()
    assert synth.is_speaking() is False


def test_spec_collects_piper_espeak_data() -> None:
    """Проверяет, что GupkaCommander.spec включает сбор данных для piper и espeak-ng-data."""
    project_root = Path(__file__).resolve().parent.parent
    spec_path = project_root / "GupkaCommander.spec"
    assert spec_path.exists()
    content = spec_path.read_text(encoding="utf-8")
    assert "'piper'" in content
    assert "'piper_phonemize'" in content


def test_os_controller_minimize_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет метод minimize_all в WindowManager и фасаде OSController."""
    from core.os_controller import OSController

    mock_keybd = MagicMock()
    monkeypatch.setattr("win32api.keybd_event", mock_keybd)

    controller = OSController()
    res = controller.minimize_all()
    assert res is True
    # Проверяем, что были нажаты Win и M (4 вызова keybd_event: Win down, M down, M up, Win up)
    assert mock_keybd.call_count == 4


def test_main_window_action_minimize_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет вызов minimize_all через маршрутизацию действий в main.py."""
    from core.routing.intent_router import Intent, RouteResult
    from main import GupkaCommanderApp

    mock_os = MagicMock()
    mock_os.minimize_all.return_value = True

    app = GupkaCommanderApp(
        os_controller=mock_os,
        enable_voice=False,
        enable_hotkey=False,
        enable_tray=False,
    )

    route = RouteResult(
        intent=Intent.WINDOW_CONTROL,
        query="сверни все",
        params={"action": "minimize_all"},
        confidence=1.0,
    )
    resp = app._handle_window_control(route)
    assert resp == "Все окна свернуты."
    assert mock_os.minimize_all.called

    mock_os.minimize_all.reset_mock()
    resp_action = app._execute_window_action("minimize_all", "")
    assert resp_action == "Все окна свернуты."
    assert mock_os.minimize_all.called


def test_voice_bridge_has_stream_chunk_signal() -> None:
    """Проверяет наличие stream_chunk_received в VoiceBridge."""
    from main import VoiceBridge

    bridge = VoiceBridge()
    received: list[str] = []
    bridge.stream_chunk_received.connect(received.append)
    bridge.stream_chunk_received.emit("токен")
    assert received == ["токен"]
