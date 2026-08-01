"""原子操作层：受限原子动作 -> 1280x720 坐标点击 + 后置确认。

真实流程（已按游戏确认）：
  主界面(有攻击钮) --open_command_cards()--> 选卡界面 --选3张--> 选完第3张自动发动 -> 动画

安全边界（硬禁区）：本类**故意不提供** 令咒/圣晶石复活/氪金/抽卡/补 AP 入口。

选卡后置确认：点击前后对同一张卡的 ROI 做像素差；差异超阈值 = 该卡视觉确实变化（出现"行动N"
徽标/高亮）= 选中成功。不依赖训练"已选中"模板，只需 ROI 框 + 阈值。

TODO(校准)：coords 坐标、确认节点、以及 _DIFF_THRESHOLD 需用真实截图标定。
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from . import coords

# 开卡后确认已进入选卡界面（待在 resource 创建）
CONFIRM_COMMAND_SCENE = "战斗_选卡场景"

# 选卡像素差阈值（平均绝对差），TODO 用真实截图标定
_DIFF_THRESHOLD = 12.0


class Executor:
    def __init__(self, context, controller=None) -> None:
        self.ctx = context
        self.controller = controller or context.tasker.controller

    # ---- 原子动作（V1b）----

    def open_command_cards(self) -> bool:
        """主界面点攻击钮。"""
        self._click(coords.ATTACK_BTN)
        return True

    def select_card(self, ui_slot: int) -> bool:
        self._click(coords.center(coords.CARD_ROI[ui_slot]))
        return True

    def select_np(self, servant_slot: int) -> bool:
        self._click(coords.NP_CLICK[servant_slot])
        return True

    def select_enemy(self, slot: int) -> bool:
        # V1b 暂不主动选目标（用当前默认目标）；保留接口供以后使用
        self._click(coords.ENEMY_POINT[slot])
        return True

    # 注意：无 attack()——选完第 3 张卡自动发动；也没有令咒/圣晶石/氪金/抽卡入口

    # ---- 内部 ----

    def _select_with_diff(self, box) -> bool:
        before = self._crop(self._screencap(), box)
        self._click(coords.center(box))
        after = self._crop(self._screencap(), box)
        return self._changed(before, after)

    def _changed(self, a: Optional[np.ndarray], b: Optional[np.ndarray]) -> bool:
        if a is None or b is None or a.size == 0 or a.shape != b.shape:
            return False
        return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16)))) > _DIFF_THRESHOLD

    def _screencap(self) -> np.ndarray:
        return self.controller.post_screencap().wait().get()

    @staticmethod
    def _crop(img: np.ndarray, box) -> Optional[np.ndarray]:
        x, y, w, h = box
        if w <= 0 or h <= 0:
            return None
        return img[y:y + h, x:x + w]

    def _click(self, xy) -> None:
        x, y = xy
        self.controller.post_click(int(x), int(y)).wait()
