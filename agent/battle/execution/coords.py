"""720x1280? 不——统一 1280x720 逻辑坐标。MFW Controller 已把设备分辨率归一到此坐标系。

TODO(校准)：以下坐标全部为占位，需用真实 1280x720 截图标定后填写。
坐标 = (x, y) 点击点。
"""
from __future__ import annotations

# 下排 5 张面卡中心点
CARD_COORD = {
    1: (0, 0),   # TODO
    2: (0, 0),   # TODO
    3: (0, 0),   # TODO
    4: (0, 0),   # TODO
    5: (0, 0),   # TODO
}

# 上排最多 3 张宝具卡（按从者槽位）
NP_COORD = {
    1: (0, 0),   # TODO
    2: (0, 0),   # TODO
    3: (0, 0),   # TODO
}

# 敌方槽位（选目标）
ENEMY_COORD = {
    1: (0, 0),   # TODO
    2: (0, 0),   # TODO
    3: (0, 0),   # TODO
}

ATTACK_BTN = (0, 0)   # TODO 攻击按钮
