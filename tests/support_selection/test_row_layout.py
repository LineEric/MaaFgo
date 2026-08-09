"""助战选择可见行布局离线测试。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "agent"
sys.path.insert(0, os.fspath(AGENT))

from support_selection.row_layout import RectBox, build_visible_rows


def _read_image(path: Path):
    data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    assert image is not None, path
    return image


def _template_boxes(image, template, threshold=0.8):
    roi_x, roi_y, roi_w, roi_h = 1020, 165, 195, 545
    roi = image[roi_y:roi_y + roi_h, roi_x:roi_x + roi_w]
    scores = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(scores >= threshold)
    candidates = sorted(
        (
            (float(scores[y, x]), RectBox(int(x + roi_x), int(y + roi_y), template.shape[1], template.shape[0]))
            for y, x in zip(ys, xs)
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    selected: list[RectBox] = []
    for _, box in candidates:
        if all(abs(box.x - old.x) > 20 or abs(box.y - old.y) > 25 for old in selected):
            selected.append(box)
    return tuple(sorted(selected, key=lambda box: box.y))


def test_reference_screenshot_locates_complete_support_rows():
    screenshot = _read_image(ROOT / "docs/zh_cn/screenshot/8-3/助战选择界面.png")
    template = _read_image(ROOT / "assets/resource/base/image/support/助战编入确认.png")

    buttons = _template_boxes(screenshot, template)
    rows = build_visible_rows(
        buttons,
        frame_width=screenshot.shape[1],
        frame_height=screenshot.shape[0],
    )

    assert [(box.x, box.y) for box in buttons] == [(1085, 358), (1085, 558)]
    assert [row.confirm_button.as_tuple() for row in rows] == [
        (1085, 358, 99, 60),
        (1085, 558, 99, 60),
    ]
    assert rows[0].level_roi.as_tuple() == (47, 346, 165, 42)
    assert rows[0].np_roi.as_tuple() == (380, 444, 450, 55)
    assert rows[1].level_roi.as_tuple() == (47, 546, 165, 42)
    assert rows[1].np_roi.as_tuple() == (380, 644, 450, 55)


def test_clipped_top_row_and_invalid_buttons_are_ignored():
    rows = build_visible_rows(
        (
            (1085, 158, 99, 60),
            (1085, 358, 99, 60),
            (-1, 558, 99, 60),
            (1085, 700, 99, 60),
        ),
        frame_width=1280,
        frame_height=720,
    )

    assert len(rows) == 1
    assert rows[0].confirm_button.as_tuple() == (1085, 358, 99, 60)