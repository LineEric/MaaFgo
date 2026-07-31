"""路 A：离线参考 CV（cv2/numpy），用于标定与黄金集验证。

注意：这是运行时 MFW ColorMatch 的**忠实代理**，不是运行时代码。
运行时 `agent/battle/perception` 走 MFW `run_recognition`；本文件用同一套 HSV 逻辑
在无设备下复现，便于标定 ROI/阈值和跑黄金集。
**HSV 阈值必须与 resource 里的 ColorMatch 节点保持一致**，否则离线结论不代表真机。

OpenCV HSV 约定：H 0-179, S 0-255, V 0-255。Buster 红跨 0/179 两端，用两段区间。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

# 卡色 HSV 边界（起始值，需用真实截图标定收紧）
HSV_BOUNDS: Dict[str, List[Tuple[Tuple[int, int, int], Tuple[int, int, int]]]] = {
    "B": [((0, 100, 100), (10, 255, 255)), ((170, 100, 100), (179, 255, 255))],  # 力击/红，跨端
    "A": [((95, 80, 80), (130, 255, 255))],   # 技击/蓝
    "Q": [((40, 80, 80), (85, 255, 255))],    # 迅击/绿
}


def crop(img: np.ndarray, box) -> Optional[np.ndarray]:
    x, y, w, h = box
    if w <= 0 or h <= 0:
        return None
    return img[y:y + h, x:x + w]


def color_counts(bgr_roi: np.ndarray) -> Dict[str, int]:
    hsv = cv2.cvtColor(bgr_roi, cv2.COLOR_BGR2HSV)
    counts: Dict[str, int] = {}
    for color, ranges in HSV_BOUNDS.items():
        mask = None
        for lo, hi in ranges:
            m = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
            mask = m if mask is None else cv2.bitwise_or(mask, m)
        counts[color] = int(np.count_nonzero(mask))
    return counts


def predict_color(bgr_roi: np.ndarray) -> Tuple[Optional[str], float, Dict[str, int]]:
    counts = color_counts(bgr_roi)
    total = sum(counts.values())
    if total == 0:
        return None, 0.0, counts
    best = max(counts, key=counts.get)
    return best, counts[best] / total, counts


def mean_hsv(bgr_roi: np.ndarray) -> Tuple[float, float, float]:
    hsv = cv2.cvtColor(bgr_roi, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0].mean(), hsv[:, :, 1].mean(), hsv[:, :, 2].mean()
    return float(h), float(s), float(v)


def template_match(img: np.ndarray, template: np.ndarray) -> float:
    """返回最佳匹配得分 [0,1]，供场景/NP 卡等模板类校验用（需先裁好模板）。"""
    res = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
    return float(res.max())
