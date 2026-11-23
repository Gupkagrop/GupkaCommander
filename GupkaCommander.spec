# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller спецификация для GupkaCommander.

Формирует автономную директорию dist/GupkaCommander/ с исполнимым файлом GupkaCommander.exe
без консольного окна, с встроенной иконкой, необходимыми бинарными зависимостями
и Windows Per-Monitor DPI манифестом.
"""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None
project_root = Path('.').resolve()

# 1. Сбор статических данных (assets, конфиги)
datas = [
    (str(project_root / 'assets'), 'assets'),
    (str(project_root / 'config' / 'commands.json'), 'config'),
    (str(project_root / 'config' / 'settings.json'), 'config'),
]

# Сбор данных пакетов (sounddevice, piper, faster_whisper) при их наличии
for package_name in ['sounddevice', 'piper', 'piper_phonemize', 'faster_whisper']:
    try:
        collected = collect_data_files(package_name)
        if collected:
            datas.extend(collected)
    except Exception:
        pass

# 2. Скрытые и динамические импорты для всех подсистем ядра и GUI
hiddenimports = [
    'core',
    'core.paths',
    'core.bootstrap',
    'core.app_finder',
    'core.os_controller',
    'core.failsafe',
    'core.routing.intent_router',
    'core.brain.llm_client',
    'core.brain.llama_manager',
    'core.vision.screen_capture',
    'core.vision.vision_agent',
    'core.uia.uia_controller',
    'core.voice.audio_capture',
    'core.voice.vad_detector',
    'core.voice.transcriber',
    'core.voice.synthesizer',
    'core.voice.sentence_streamer',
    'core.voice.voice_pipeline',
    'core.memory.db',
    'core.memory.memory_manager',
    'gui.spotlight_bar',
    'gui.waveform_widget',
    'gui.chat_drawer',
    'gui.hotkey_manager',
    'gui.tray_icon',
    'gui.setup_dialog',
    'gui.settings_dialog',
    'gui.styles',
    'onnxruntime',
    'ctranslate2',
    'sounddevice',
    '_sounddevice_data',
    'pynput.keyboard._win32',
    'pynput.mouse._win32',
    'uiautomation',
    'win32clipboard',
    'win32con',
    'win32gui',
    'win32process',
    'win32api',
    'pywintypes',
    'pythoncom',
]

a = Analysis(
    ['main.py'],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'notebook', 'IPython'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

icon_path = str(project_root / 'assets' / 'gupka_commander.ico')

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GupkaCommander',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path if Path(icon_path).exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='GupkaCommander',
)
