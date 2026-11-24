"""
Скрипт автоматизированной компиляции инсталлятора Inno Setup для GupkaCommander.

Проверяет наличие компилятора Inno Setup (ISCC.exe) в системе,
запускает сборку scripts/installer.iss и генерирует
готовый дистрибутив dist/GupkaCommander-Setup-v2.0.0.exe.
"""

import shutil
import subprocess
import sys
from pathlib import Path


def find_iscc() -> Path | None:
    """
    Ищет исполнимый файл компилятора Inno Setup (ISCC.exe) в системе.

    Returns:
        Путь к ISCC.exe или None, если компилятор не найден.
    """
    which_iscc = shutil.which("iscc")
    if which_iscc:
        return Path(which_iscc)

    candidate_paths = [
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 5\ISCC.exe"),
    ]

    for candidate in candidate_paths:
        if candidate.exists():
            return candidate

    return None


def build_installer() -> int:
    """
    Компилирует скрипт installer.iss в однофайловый установщик Windows.

    Returns:
        0 при успешной сборке, 2 если ISCC не установлен, 1 при ошибке сборки.
    """
    project_root = Path(__file__).resolve().parent.parent
    iss_path = project_root / "scripts" / "installer.iss"
    dist_dir = project_root / "dist" / "GupkaCommander"

    if not dist_dir.exists():
        print("❌ Директория dist/GupkaCommander не найдена! Сначала запустите сборку PyInstaller:")
        print("   uv run python scripts/build_executable.py")
        return 1

    if not iss_path.exists():
        print(f"❌ Файл спецификации {iss_path} не найден!")
        return 1

    iscc_path = find_iscc()
    if not iscc_path:
        print("\n⚠️ Inno Setup Compiler (ISCC.exe) не обнаружен в системе.")
        print("ℹ️ Автономная сборка dist/GupkaCommander/ готова к использованию напрямую.")
        print("👉 Для создания однофайлового инсталлятора Setup.exe установите Inno Setup:")
        print("   winget install JRSoftware.InnoSetup")
        print("   После установки повторите: uv run python scripts/build_installer.py")
        return 2

    print(f"🚀 Запуск компиляции инсталлятора через {iscc_path}...")
    cmd = [str(iscc_path), str(iss_path)]
    result = subprocess.run(cmd, cwd=str(project_root / "scripts"))

    if result.returncode != 0:
        print(f"❌ Ошибка компиляции Inno Setup! Код: {result.returncode}")
        return result.returncode

    setup_exe = project_root / "dist" / "GupkaCommander-Setup-v2.0.0.exe"
    if not setup_exe.exists():
        print(f"❌ Файл инсталлятора не найден по пути: {setup_exe}")
        return 1

    size_mb = setup_exe.stat().st_size / (1024 * 1024)
    print("\n✅ Инсталлятор успешно создан!")
    print(f"📦 Файл установщика: {setup_exe.name} ({size_mb:.2f} MB)")
    print(f"📁 Путь: {setup_exe}")
    return 0


if __name__ == "__main__":
    sys.exit(build_installer())
