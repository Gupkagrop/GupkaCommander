"""
Скрипт автоматизированной сборки автономного исполняемого пакета GupkaCommander.

Выполняет:
1. Проверку и генерацию иконки assets/gupka_commander.ico при отсутствии.
2. Запуск PyInstaller с конфигурацией GupkaCommander.spec.
3. Валидацию результирующего бинарного файла dist/GupkaCommander/GupkaCommander.exe.
4. Упаковку портативного архива dist/GupkaCommander-v2.0.0-win64-portable.zip.
5. Вывод статистики размера дистрибутива и архива.
"""

import subprocess
import sys
import zipfile
from pathlib import Path

PORTABLE_ZIP_NAME = "GupkaCommander-v2.0.0-win64-portable.zip"


def ensure_icon(project_root: Path) -> Path:
    """
    Проверяет наличие фирменной иконки и генерирует её при необходимости.

    Args:
        project_root: Корневая директория проекта.

    Returns:
        Путь к файлу assets/gupka_commander.ico.
    """
    icon_path = project_root / "assets" / "gupka_commander.ico"
    if icon_path.exists():
        return icon_path

    print("ℹ️ Иконка assets/gupka_commander.ico не найдена. Запуск генератора...")
    from scripts.generate_icon import generate_icon_files

    generate_icon_files(output_ico=icon_path)
    return icon_path


def get_dir_size_mb(directory: Path) -> float:
    """
    Вычисляет общий размер директории в мегабайтах.

    Args:
        directory: Целевая папка.

    Returns:
        Размер в мегабайтах (MB).
    """
    total_bytes = sum(f.stat().st_size for f in directory.rglob("*") if f.is_file())
    return total_bytes / (1024 * 1024)


def create_portable_zip(dist_dir: Path, output_zip: Path | None = None) -> Path:
    """Упаковывает автономную директорию dist_dir в портативный ZIP-архив.

    Args:
        dist_dir: Путь к собранному пакету (обычно dist/GupkaCommander).
        output_zip: Целевой путь к архиву (по умолчанию GupkaCommander-v2.0.0-win64-portable.zip).

    Returns:
        Путь к созданному ZIP-архиву.
    """
    target_zip = output_zip if output_zip is not None else (dist_dir.parent / PORTABLE_ZIP_NAME)
    target_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(dist_dir.rglob("*")):
            if file_path.is_file():
                arcname = file_path.relative_to(dist_dir.parent).as_posix()
                zf.write(file_path, arcname=arcname)
    return target_zip


def build_executable() -> int:
    """
    Выполняет компиляцию проекта через PyInstaller.

    Returns:
        Код завершения (0 при успехе, иначе ненулевой).
    """
    project_root = Path(__file__).resolve().parent.parent
    spec_path = project_root / "GupkaCommander.spec"

    if not spec_path.exists():
        print(f"❌ Файл спецификации {spec_path} не найден!")
        return 1

    ensure_icon(project_root)

    print("🚀 Запуск сборки PyInstaller...")
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(spec_path),
        "--noconfirm",
        "--clean",
    ]

    result = subprocess.run(cmd, cwd=str(project_root))
    if result.returncode != 0:
        print(f"❌ Ошибка сборки PyInstaller! Код возврата: {result.returncode}")
        return result.returncode

    exe_path = project_root / "dist" / "GupkaCommander" / "GupkaCommander.exe"
    if not exe_path.exists():
        print(f"❌ Исполняемый файл не найден по пути: {exe_path}")
        return 1

    dist_dir = project_root / "dist" / "GupkaCommander"
    espeak_dir = dist_dir / "_internal" / "piper" / "espeak-ng-data"
    alt_espeak = dist_dir / "piper" / "espeak-ng-data"
    if espeak_dir.exists() or alt_espeak.exists():
        found = espeak_dir if espeak_dir.exists() else alt_espeak
        print(f"🗣️ Данные фонемизатора espeak-ng-data подтверждены: {found.name}")
    else:
        print("⚠️ Внимание: espeak-ng-data не найден в директории dist.")

    exe_size_mb = exe_path.stat().st_size / (1024 * 1024)
    total_size_mb = get_dir_size_mb(dist_dir)
    zip_path = create_portable_zip(dist_dir)
    zip_size_mb = zip_path.stat().st_size / (1024 * 1024)

    print("\n✅ Сборка успешно завершена!")
    print(f"📁 Директория дистрибутива: {dist_dir}")
    print(f"🎯 Исполняемый файл: {exe_path.name} ({exe_size_mb:.2f} MB)")
    print(f"📦 Общий объем автономного пакета: {total_size_mb:.2f} MB")
    print(f"🗜️ Портативный архив: {zip_path.name} ({zip_size_mb:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(build_executable())
