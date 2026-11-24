"""Модуль визуальной разметки интерфейса Set-of-Mark (SoM) для мультимодальных моделей.

Предоставляет:
- SoMElement: структура размеченного элемента UI с привязкой к глобальным координатам и монитору.
- SoMAnnotationResult: результат разметки (изображение, кадр, манифест и словарь id -> SoMElement).
- SoMAnnotator: отрисовка пронумерованных рамок поверх RGB/PIL-кадра и генерация
  компактного текстового манифеста элементов для LLM.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from core.vision.monitor_manager import MonitorInfo
from core.vision.screen_capture import ScreenFrame, compute_dhash, screen_to_norm

_PALETTE: tuple[tuple[int, int, int], ...] = (
    (220, 38, 38),
    (37, 99, 235),
    (22, 163, 74),
    (217, 119, 6),
    (147, 51, 234),
    (8, 145, 178),
)


@dataclasses.dataclass(frozen=True)
class SoMElement:
    """Размеченный элемент UI с привязкой к глобальным и нормализованным координатам."""

    mark_id: int
    name: str
    control_type: str
    global_x: int
    global_y: int
    norm_bbox: tuple[int, int, int, int]
    monitor_index: int
    automation_id: str = ""


@dataclasses.dataclass
class SoMAnnotationResult:
    """Итог разметки Set-of-Mark: изображение, кадр, элементы, индекс и манифест."""

    image: Image.Image
    frame: ScreenFrame
    elements: list[SoMElement]
    lookup: dict[int, SoMElement]
    manifest: str


def _short_control_type(control_type: str) -> str:
    """Сокращает имя типа контрола UIA (например, ButtonControl -> Button)."""
    cleaned = control_type.strip()
    if cleaned.endswith("Control") and len(cleaned) > 7:
        return cleaned[:-7]
    return cleaned or "Element"


def _compute_norm_bbox(
    raw_el: Mapping[str, Any],
    rect: Mapping[str, Any],
    monitor: MonitorInfo | None,
) -> tuple[int, int, int, int]:
    """Извлекает или вычисляет нормализованную рамку [0..1000] в координатах монитора."""
    existing = raw_el.get("norm_bbox")
    if isinstance(existing, (list, tuple)) and len(existing) == 4:
        return (int(existing[0]), int(existing[1]), int(existing[2]), int(existing[3]))

    width = monitor.width if monitor else 1920
    height = monitor.height if monitor else 1080
    left = monitor.left if monitor else 0
    top = monitor.top if monitor else 0

    x1, y1 = screen_to_norm(
        int(rect.get("left", 0)),
        int(rect.get("top", 0)),
        width,
        height,
        left=left,
        top=top,
    )
    x2, y2 = screen_to_norm(
        int(rect.get("right", 0)),
        int(rect.get("bottom", 0)),
        width,
        height,
        left=left,
        top=top,
    )
    return (x1, y1, x2, y2)


class SoMAnnotator:
    """Аннотатор Set-of-Mark для отрисовки числовых тегов поверх снимка монитора."""

    def __init__(self, max_elements: int = 80, box_width: int = 2) -> None:
        """Инициализация аннотатора SoM.

        Args:
            max_elements: Максимальное число размечаемых элементов на один кадр.
            box_width: Толщина рамки выделения элемента в пикселях.
        """
        self._max_elements = max(1, max_elements)
        self._box_width = max(1, box_width)
        self._font = ImageFont.load_default()
        self._last_lookup: dict[int, SoMElement] = {}

    @property
    def last_lookup(self) -> dict[int, SoMElement]:
        """Словарь mark_id -> SoMElement от последней выполненной разметки."""
        return dict(self._last_lookup)

    def get_element(self, mark_id: int) -> SoMElement | None:
        """Возвращает SoMElement по его номеру метки из последнего кадра."""
        return self._last_lookup.get(mark_id)

    def build_elements(
        self,
        uia_elements: Sequence[Mapping[str, Any]],
        monitor: MonitorInfo | None = None,
        monitor_index: int = 1,
        max_elements: int | None = None,
    ) -> tuple[list[SoMElement], dict[int, SoMElement]]:
        """Преобразует список элементов UIA в пронумерованный список SoMElement и lookup."""
        limit = max_elements if max_elements is not None else self._max_elements
        target_mon_idx = monitor.index if monitor is not None else monitor_index
        elements: list[SoMElement] = []
        lookup: dict[int, SoMElement] = {}

        for raw_el in uia_elements:
            if len(elements) >= limit:
                break
            rect = raw_el.get("rect")
            if not isinstance(rect, Mapping):
                continue

            left = int(rect.get("left", 0))
            top = int(rect.get("top", 0))
            right = int(rect.get("right", 0))
            bottom = int(rect.get("bottom", 0))
            if right <= left or bottom <= top:
                continue

            mark_id = len(elements) + 1
            global_x = (left + right) // 2
            global_y = (top + bottom) // 2
            norm_bbox = _compute_norm_bbox(raw_el, rect, monitor)
            el_mon_idx = int(raw_el.get("monitor_index", target_mon_idx))
            som_el = SoMElement(
                mark_id=mark_id,
                name=str(raw_el.get("name", "") or "").strip(),
                control_type=_short_control_type(str(raw_el.get("control_type", ""))),
                global_x=global_x,
                global_y=global_y,
                norm_bbox=norm_bbox,
                monitor_index=el_mon_idx,
                automation_id=str(raw_el.get("automation_id", "") or "").strip(),
            )
            elements.append(som_el)
            lookup[mark_id] = som_el

        self._last_lookup = dict(lookup)
        return (elements, lookup)

    def build_manifest(self, elements: Sequence[SoMElement]) -> str:
        """Формирует компактный текстовый манифест элементов для контекста LLM."""
        if not elements:
            return "(интерактивные элементы не найдены)"

        lines: list[str] = []
        for el in elements:
            cx_norm = (el.norm_bbox[0] + el.norm_bbox[2]) // 2
            cy_norm = (el.norm_bbox[1] + el.norm_bbox[3]) // 2
            label = el.name or el.automation_id or "unnamed"
            lines.append(f'[{el.mark_id}] {el.control_type} "{label}" @ ({cx_norm}, {cy_norm})')
        return "\n".join(lines)

    def _draw_single_tag(
        self,
        draw: ImageDraw.ImageDraw,
        el: SoMElement,
        img_w: int,
        img_h: int,
    ) -> None:
        """Отрисовывает рамку и номерную плашку одного элемента SoM."""
        color = _PALETTE[(el.mark_id - 1) % len(_PALETTE)]
        x1 = max(0, min(img_w - 1, int(round(el.norm_bbox[0] * img_w / 1000))))
        y1 = max(0, min(img_h - 1, int(round(el.norm_bbox[1] * img_h / 1000))))
        x2 = max(x1 + 1, min(img_w - 1, int(round(el.norm_bbox[2] * img_w / 1000))))
        y2 = max(y1 + 1, min(img_h - 1, int(round(el.norm_bbox[3] * img_h / 1000))))

        draw.rectangle((x1, y1, x2, y2), outline=color, width=self._box_width)

        tag_text = str(el.mark_id)
        bbox = draw.textbbox((0, 0), tag_text, font=self._font)
        text_w = max(6, int(bbox[2] - bbox[0]))
        text_h = max(8, int(bbox[3] - bbox[1]))
        pad_x, pad_y = 3, 2
        badge_w = text_w + pad_x * 2
        badge_h = text_h + pad_y * 2

        tag_x = max(0, min(img_w - badge_w, x1))
        tag_y = y1 - badge_h if y1 >= badge_h else y1
        tag_y = max(0, min(img_h - badge_h, tag_y))

        draw.rectangle((tag_x, tag_y, tag_x + badge_w, tag_y + badge_h), fill=color)
        draw.text((tag_x + pad_x, tag_y + pad_y), tag_text, fill=(255, 255, 255), font=self._font)

    def draw_tags(
        self,
        image: Image.Image,
        elements: Sequence[SoMElement],
    ) -> Image.Image:
        """Рисует пронумерованные рамки SoM на копии PIL-изображения."""
        canvas = image.convert("RGB")
        draw = ImageDraw.Draw(canvas)
        for el in elements:
            self._draw_single_tag(draw, el, canvas.width, canvas.height)
        return canvas

    def annotate_image(
        self,
        image: Image.Image,
        uia_elements: Sequence[Mapping[str, Any]],
        monitor: MonitorInfo | None = None,
        monitor_index: int = 1,
        max_elements: int | None = None,
    ) -> SoMAnnotationResult:
        """Размечает PIL-изображение тегами SoM и строит манифест и словарь поиска."""
        target_monitor = monitor or MonitorInfo(
            index=monitor_index,
            left=0,
            top=0,
            width=max(1, image.width),
            height=max(1, image.height),
        )
        elements, lookup = self.build_elements(
            uia_elements,
            monitor=target_monitor,
            monitor_index=target_monitor.index,
            max_elements=max_elements,
        )
        annotated_img = self.draw_tags(image, elements)
        rgb_bytes = annotated_img.tobytes()
        annotated_frame = ScreenFrame(
            width=annotated_img.width,
            height=annotated_img.height,
            rgb=rgb_bytes,
            dhash=compute_dhash(rgb_bytes, annotated_img.width, annotated_img.height),
            monitor_index=target_monitor.index,
            left=target_monitor.left,
            top=target_monitor.top,
            dpi_scale=target_monitor.dpi_scale,
        )
        manifest = self.build_manifest(elements)
        return SoMAnnotationResult(
            image=annotated_img,
            frame=annotated_frame,
            elements=elements,
            lookup=lookup,
            manifest=manifest,
        )

    def annotate_frame(
        self,
        frame: ScreenFrame,
        uia_elements: Sequence[Mapping[str, Any]],
        monitor: MonitorInfo | None = None,
        max_elements: int | None = None,
    ) -> SoMAnnotationResult:
        """Размечает захваченный ScreenFrame тегами SoM и возвращает результат с манифестом."""
        target_monitor = monitor or MonitorInfo(
            index=frame.monitor_index,
            left=frame.left,
            top=frame.top,
            width=max(1, frame.width),
            height=max(1, frame.height),
            dpi_scale=frame.dpi_scale,
        )
        expected_len = frame.width * frame.height * 3
        if frame.width > 0 and frame.height > 0 and len(frame.rgb) == expected_len:
            base_img = Image.frombytes("RGB", (frame.width, frame.height), frame.rgb)
        else:
            base_img = Image.new("RGB", (max(1, frame.width), max(1, frame.height)), (0, 0, 0))

        return self.annotate_image(
            base_img,
            uia_elements,
            monitor=target_monitor,
            monitor_index=target_monitor.index,
            max_elements=max_elements,
        )
