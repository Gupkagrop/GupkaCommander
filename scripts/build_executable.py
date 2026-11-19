"""
Скрипт автоматизированной сборки автономного исполняемого пакета GupkaCommander.

Выполняет:
1. Проверку и генерацию иконки assets/gupka_commander.ico при отсутствии.
2. Запуск PyInstaller с конфигурацией GupkaCommander.spec.
3. Валидацию результирующего бинарного файла dist/GupkaCommander/GupkaCommander.exe.
4. Вывод статистики размера дистрибутива.
"""

import subprocess
import sys
from pathlib import Path


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

    exe_size_mb = exe_path.stat().st_size / (1024 * 1024)
    dist_dir = project_root / "dist" / "GupkaCommander"
    total_size_mb = get_dir_size_mb(dist_dir)

    print("\n✅ Сборка успешно завершена!")
    print(f"📁 Директория дистрибутива: {dist_dir}")
    print(f"🎯 Исполняемый файл: {exe_path.name} ({exe_size_mb:.2f} MB)")
    print(f"📦 Общий объем автономного пакета: {total_size_mb:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(build_executable())
