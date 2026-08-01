"""1280x720 逻辑坐标。MFW Controller 已把设备分辨率归一到此坐标系。

卡片用 ROI 框 (x, y, w, h)：点击取框中心；选卡后置确认对同一框做前后像素差。
其余交互点用 (x, y) 点。

TODO(校准)：全部为占位，需用 MFW 截的真实 1280x720 图标定后填写。
"""
from __future__ import annotations

from typing import Tuple

# 下排 5 张面卡的 ROI 框
CARD_ROI = {
    1: (75, 529, 138, 98),
    2: (329, 529, 118, 98),
    3: (578, 529, 136, 98),
    4: (842, 529, 127, 98),
    5: (1097, 529, 129, 98),
}

# 上排最多 3 张宝具卡的 ROI 框（按从者槽位）
NP_ROI = {
    1: (340, 118, 141, 40),
    2: (570, 118, 141, 40),
    3: (805, 118, 141, 40),
}

# 主界面"攻击"按钮点击点（开卡）
ATTACK_BTN = (1136,601)    # TODO

# 敌方槽位点击点（选目标；V1b 暂不主动使用）
ENEMY_POINT = {
    1: (0, 0),   # TODO
    2: (0, 0),   # TODO
    3: (0, 0),   # TODO
}


def center(box: Tuple[int, int, int, int]) -> Tuple[int, int]:
    x, y, w, h = box
    return (x + w // 2, y + h // 2)
