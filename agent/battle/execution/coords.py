"""1280x720 逻辑坐标。MFW Controller 已把设备分辨率归一到此坐标系。

卡片用 ROI 框 (x, y, w, h)：点击取框中心。
其余交互点用 (x, y) 点。
"""
from __future__ import annotations

from typing import Tuple

# 下排 5 张面卡的 ROI 框（用于 OCR 识别卡牌类型）
CARD_ROI = {
    1: (75, 529, 138, 98),
    2: (320, 529, 136, 98),
    3: (578, 529, 136, 98),
    4: (842, 529, 127, 98),
    5: (1097, 529, 129, 98),
}

# 上排最多 3 张宝具卡的 ROI 框（按从者槽位）——用于 OCR 检测 NP 数值
NP_ROI = {
    1: (222, 656, 83, 26),
    2: (540, 657, 83, 24),
    3: (865, 655, 73, 25),
}

# 宝具卡点击位置（上排卡牌位置，用于 select_np 点击）
NP_CLICK = {
    1: (410, 138),
    2: (640, 138),
    3: (875, 138),
}

# 主界面"攻击"按钮点击点（开卡）
ATTACK_BTN = (1136, 601)

# 敌方槽位点击点（选目标；V1b 暂不主动使用）
ENEMY_POINT = {
    1: (0, 0),
    2: (0, 0),
    3: (0, 0),
}


def center(box: Tuple[int, int, int, int]) -> Tuple[int, int]:
    x, y, w, h = box
    return (x + w // 2, y + h // 2)
