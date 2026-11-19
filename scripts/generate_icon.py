"""
> [!NOTE]
> Скрипт scripts.generate_icon: Генерация многослойной системной Windows-иконки
> и мастер-изображения GupkaCommander в стиле Windows 11 Fluent Dark.
> Создает файлы assets/gupka_commander.ico и assets/gupka_commander.png с размерами
> от 16x16 до 256x256, градиентной подложкой, неоновой эмблемой 'G' и радиоволнами.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Final

from PIL import Image, ImageDraw, ImageFilter

logger = logging.getLogger("generate_icon")

# Константы геометрии и палитры
CANVAS_SIZE: Final[int] = 512
DEFAULT_SIZES: Final[tuple[int, ...]] = (16, 32, 48, 64, 128, 256)

# Цветовая палитра Fluent Dark (#1E1E1E -> #121212) и неоновый акцент (#60CDFF)
COLOR_BG_TOP: Final[tuple[int, int, int]] = (30, 30, 30)  # #1E1E1E
COLOR_BG_BOTTOM: Final[tuple[int, int, int]] = (18, 18, 18)  # #121212
COLOR_BORDER: Final[tuple[int, int, int, int]] = (60, 60, 60, 200)
COLOR_ACCENT: Final[tuple[int, int, int, int]] = (96, 205, 255, 255)  # #60CDFF
COLOR_ACCENT_GLOW: Final[tuple[int, int, int, int]] = (96, 205, 255, 75)  # Свечение
COLOR_ACCENT_CORE: Final[tuple[int, int, int, int]] = (215, 243, 255, 240)  # Ядро неона


def create_gradient_background(size: int, radius: int) -> Image.Image:
    """
    Создает скругленную подложку с вертикальным градиентом в стиле Fluent Dark.

    Args:
        size: Размер квадратного холста в пикселях.
        radius: Радиус скругления углов squircle.

    Returns:
        Image.Image: Сформированное RGBA изображение подложки.
    """
    # 1. Формируем вертикальный градиент
    gradient = Image.new("RGBA", (size, size))
    grad_draw = ImageDraw.Draw(gradient)

    r_top, g_top, b_top = COLOR_BG_TOP
    r_bot, g_bot, b_bot = COLOR_BG_BOTTOM

    for y in range(size):
        ratio = y / max(size - 1, 1)
        r = int(r_top + (r_bot - r_top) * ratio)
        g = int(g_top + (g_bot - g_top) * ratio)
        b = int(b_top + (b_bot - b_top) * ratio)
        grad_draw.line([(0, y), (size, y)], fill=(r, g, b, 255))

    # 2. Создаем маску скругленного прямоугольника
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    padding = int(size * 0.04)
    rect_box = [padding, padding, size - padding, size - padding]
    mask_draw.rounded_rectangle(rect_box, radius=radius, fill=255)

    # 3. Применяем маску к градиенту
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(gradient, (0, 0), mask)

    # 4. Рисуем тонкую полупрозрачную рамку границы Fluent Design
    res_draw = ImageDraw.Draw(result)
    res_draw.rounded_rectangle(rect_box, radius=radius, outline=COLOR_BORDER, width=3)
    return result


def draw_emblem_geometry(draw: ImageDraw.ImageDraw, is_glow: bool = False) -> None:
    """
    Отрисовывает неоновую эмблему буквы 'G' и радиоволны на холсте.

    Args:
        draw: Контекст рисования ImageDraw.
        is_glow: Флаг отрисовки слоя рассеянного свечения (увеличенная толщина).
    """
    main_color = COLOR_ACCENT_GLOW if is_glow else COLOR_ACCENT
    g_width = 46 if is_glow else 32
    wave_width = 30 if is_glow else 20

    # Центр и габариты буквы 'G'
    g_cx, g_cy = 205, 256
    g_radius = 116
    g_box = [g_cx - g_radius, g_cy - g_radius, g_cx + g_radius, g_cy + g_radius]

    # Основная округлая дуга 'G' (от 35 градусов по часовой стрелке до 325 градусов)
    draw.arc(g_box, start=35, end=325, fill=main_color, width=g_width)

    # Горизонтальная перекладина буквы 'G'
    bar_y = g_cy + 8
    bar_x_start = g_cx + 8
    bar_x_end = g_cx + g_radius - 6
    draw.line([(bar_x_start, bar_y), (bar_x_end, bar_y)], fill=main_color, width=g_width)

    # Вертикальная стойка перекладины 'G'
    spur_x = bar_x_end
    draw.line([(spur_x, bar_y), (spur_x, g_cy + 68)], fill=main_color, width=g_width)

    # Радиоволны (концентрические дуги сигнала), исходящие вправо
    wave_cx, wave_cy = 220, 256
    for wave_r in (152, 198, 242):
        wave_box = [wave_cx - wave_r, wave_cy - wave_r, wave_cx + wave_r, wave_cy + wave_r]
        draw.arc(wave_box, start=328, end=32, fill=main_color, width=wave_width)


def render_master_icon(size: int = CANVAS_SIZE) -> Image.Image:
    """
    Рендерит мастер-изображение иконки высокого разрешения с неоновым свечением.

    Args:
        size: Размер холста (по умолчанию 512).

    Returns:
        Image.Image: Готовое композитное изображение RGBA.
    """
    corner_radius = int(size * 0.22)
    bg_image = create_gradient_background(size, corner_radius)

    # 1. Создаем слой рассеянного неонового свечения
    glow_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_layer)
    draw_emblem_geometry(glow_draw, is_glow=True)
    glow_blurred = glow_layer.filter(ImageFilter.GaussianBlur(radius=14))

    # 2. Создаем слой четкой неоновой эмблемы
    core_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    core_draw = ImageDraw.Draw(core_layer)
    draw_emblem_geometry(core_draw, is_glow=False)

    # 3. Компонуем слои воедино
    composite = Image.alpha_composite(bg_image, glow_blurred)
    return Image.alpha_composite(composite, core_layer)


def build_icon_layers(
    master: Image.Image,
    sizes: tuple[int, ...] = DEFAULT_SIZES,
) -> list[Image.Image]:
    """
    Создает набор масштабированных слоев иконки высокого качества (Lanczos).

    Args:
        master: Исходное мастер-изображение.
        sizes: Кортеж целевых размеров иконки.

    Returns:
        list[Image.Image]: Список изображений для каждого разрешения.
    """
    layers: list[Image.Image] = []
    for s in sizes:
        resized = master.resize((s, s), Image.Resampling.LANCZOS)
        layers.append(resized)
    return layers


def generate_icon_files(
    output_ico: Path | str,
    output_png: Path | str | None = None,
    sizes: tuple[int, ...] = DEFAULT_SIZES,
) -> tuple[Path, Path | None]:
    """
    Генерирует файлы Windows .ico и PNG в заданной директории.

    Args:
        output_ico: Путь к целевому файлу .ico.
        output_png: Необязательный путь к целевому файлу .png.
        sizes: Разрешения для включения в .ico файл.

    Returns:
        tuple[Path, Path | None]: Кортеж путей сгенерированных файлов.
    """
    ico_path = Path(output_ico).resolve()
    ico_path.parent.mkdir(parents=True, exist_ok=True)

    master = render_master_icon(CANVAS_SIZE)
    layers = build_icon_layers(master, sizes)

    # Сохраняем многослойный .ico файл с гарантированными размерами
    ico_sizes = [(s, s) for s in sizes]
    layers[-1].save(
        ico_path,
        format="ICO",
        append_images=layers[:-1],
        sizes=ico_sizes,
    )
    logger.info("Успешно сохранен Windows ICO файл: %s (размеры: %s)", ico_path, sizes)

    png_path: Path | None = None
    if output_png is not None:
        png_path = Path(output_png).resolve()
        png_path.parent.mkdir(parents=True, exist_ok=True)
        # Сохраняем PNG в разрешении 256x256
        png_image = master.resize((256, 256), Image.Resampling.LANCZOS)
        png_image.save(png_path, format="PNG")
        logger.info("Успешно сохранен PNG файл: %s", png_path)

    return ico_path, png_path


def main() -> None:
    """Точка входа CLI для генерации иконки."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    project_root = Path(__file__).resolve().parent.parent
    default_ico = project_root / "assets" / "gupka_commander.ico"
    default_png = project_root / "assets" / "gupka_commander.png"

    parser = argparse.ArgumentParser(
        description="Генерация многослойной иконки GupkaCommander Fluent Dark",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=str(default_ico),
        help="Путь для сохранения файла .ico (по умолчанию: assets/gupka_commander.ico)",
    )
    parser.add_argument(
        "--png",
        type=str,
        default=str(default_png),
        help="Путь для сохранения мастер-изображения .png",
    )
    args = parser.parse_args()

    ico_res, png_res = generate_icon_files(args.output, args.png, DEFAULT_SIZES)
    print(f"Готово: Сгенерирована иконка {ico_res}")
    if png_res:
        print(f"Готово: Сгенерировано PNG изображение {png_res}")


if __name__ == "__main__":
    main()
