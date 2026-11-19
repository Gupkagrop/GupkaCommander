"""
> [!NOTE]
> Модуль тестов tests.test_generate_icon: Проверка генератора системных иконок
> Fluent Dark и валидация структуры многослойного Windows ICO файла.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from scripts.generate_icon import (
    CANVAS_SIZE,
    DEFAULT_SIZES,
    build_icon_layers,
    create_gradient_background,
    generate_icon_files,
    render_master_icon,
)


class TestIconGeneration:
    """Тестирование конвейера генерации иконок."""

    def test_gradient_background_dimensions(self) -> None:
        """Проверка генерации градиентной подложки с заданным размером и альфа-каналом."""
        bg = create_gradient_background(size=128, radius=24)
        assert bg.size == (128, 128)
        assert bg.mode == "RGBA"

    def test_render_master_icon(self) -> None:
        """Проверка формирования мастер-изображения с эмблемой и радиоволнами."""
        master = render_master_icon(size=CANVAS_SIZE)
        assert master.size == (CANVAS_SIZE, CANVAS_SIZE)
        assert master.mode == "RGBA"

    def test_build_icon_layers(self) -> None:
        """Проверка масштабирования слоев для всех стандартных размеров Windows ICO."""
        master = render_master_icon(size=256)
        test_sizes = (16, 32, 64)
        layers = build_icon_layers(master, sizes=test_sizes)

        assert len(layers) == len(test_sizes)
        for layer, expected_size in zip(layers, test_sizes, strict=True):
            assert layer.size == (expected_size, expected_size)

    def test_generate_icon_files(self, tmp_path: Path) -> None:
        """Проверка сохранения файлов .ico и .png на диск с валидацией метаданных ICO."""
        target_ico = tmp_path / "test_icon.ico"
        target_png = tmp_path / "test_icon.png"

        ico_res, png_res = generate_icon_files(
            output_ico=target_ico,
            output_png=target_png,
            sizes=DEFAULT_SIZES,
        )

        assert ico_res.exists()
        assert target_ico.stat().st_size > 0
        assert png_res is not None
        assert png_res.exists()

        # Валидация структуры ICO файла через Pillow
        with Image.open(target_ico) as img:
            assert img.format == "ICO"
            actual_sizes = img.info.get("sizes")
            assert actual_sizes is not None
            expected_set = {(s, s) for s in DEFAULT_SIZES}
            assert actual_sizes == expected_set
