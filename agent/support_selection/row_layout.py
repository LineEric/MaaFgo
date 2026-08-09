"""根据 MaaFramework 模板匹配结果建立助战列表的动态行布局。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

_LIST_TOP = 165
_LIST_LEFT = 42
_LIST_RIGHT = 1215
_ROW_TOP_FROM_BUTTON = -14
_ROW_HEIGHT = 180


@dataclass(frozen=True)
class RectBox:
    x: int
    y: int
    w: int
    h: int

    @classmethod
    def from_value(cls, value) -> "RectBox":
        if all(hasattr(value, name) for name in ("x", "y", "w", "h")):
            return cls(int(value.x), int(value.y), int(value.w), int(value.h))
        if isinstance(value, (tuple, list)) and len(value) == 4:
            return cls(*(int(item) for item in value))
        raise TypeError(f"unsupported rect value: {value!r}")

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.w, self.h


@dataclass(frozen=True)
class VisibleSupportRow:
    index: int
    confirm_button: RectBox
    row_box: RectBox
    portrait_roi: RectBox
    level_roi: RectBox
    name_roi: RectBox
    np_roi: RectBox


def build_visible_rows(
    confirm_buttons: Iterable[RectBox | tuple[int, int, int, int]],
    *,
    frame_width: int,
    frame_height: int,
) -> tuple[VisibleSupportRow, ...]:
    """把确认按钮锚点转换成同一行的字段 ROI。

    顶部严重截断、按钮越界或宝具信息区不可见的记录会被忽略。行位置
    始终由按钮 Y 坐标推导，不依赖固定的第 1/2/3 行坐标。
    """
    boxes = sorted((RectBox.from_value(value) for value in confirm_buttons), key=lambda box: box.y)
    rows: list[VisibleSupportRow] = []
    for button in boxes:
        if button.w <= 0 or button.h <= 0:
            continue
        if button.x < 0 or button.y < 0:
            continue
        if button.x + button.w > frame_width or button.y + button.h > frame_height:
            continue

        row_top = button.y + _ROW_TOP_FROM_BUTTON
        if row_top < _LIST_TOP:
            continue

        np_roi = RectBox(380, row_top + 100, 450, 55)
        if np_roi.y + np_roi.h > frame_height:
            continue

        row_bottom = min(frame_height, row_top + _ROW_HEIGHT)
        row_box = RectBox(
            _LIST_LEFT,
            row_top,
            min(_LIST_RIGHT, frame_width) - _LIST_LEFT,
            row_bottom - row_top,
        )
        rows.append(
            VisibleSupportRow(
                index=len(rows),
                confirm_button=button,
                row_box=row_box,
                portrait_roi=RectBox(47, row_top + 4, 168, min(150, row_bottom - row_top - 4)),
                level_roi=RectBox(47, row_top + 2, 165, 42),
                name_roi=RectBox(380, row_top + 34, 445, 42),
                np_roi=np_roi,
            )
        )
    return tuple(rows)