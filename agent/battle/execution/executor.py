"""原子操作层：把受限原子动作翻译成 720x720? -> 1280x720 坐标点击 + 后置确认。

安全边界（硬禁区）：本类**故意不提供** 令咒/圣晶石复活/氪金/抽卡/补 AP 等入口。
决策层即便想触发也没有方法可调。

后置确认：每个动作点击后重新截图并跑确认识别节点；确认失败 -> 返回 False（由上层 fail-closed 停止），
不盲目重点。

TODO(校准)：coords.py 坐标、以及下面确认节点均需用真实截图标定/创建。
"""
from __future__ import annotations

from . import coords

# 确认节点（待在 resource 中创建）
CONFIRM_STILL_COMMAND = "战斗_选卡界面"          # 施放后仍在选卡界面
CONFIRM_CARD_SELECTED = "战斗_已选卡计数变化"     # TODO: 真正的确认需比较点击前后已选卡计数
CONFIRM_ATTACK_STARTED = "战斗_攻击已开始"        # 选卡区消失/进入动画


class Executor:
    def __init__(self, context) -> None:
        self.ctx = context
        self.controller = context.tasker.controller

    # ---- 原子动作（V1b）----

    def select_enemy(self, slot: int) -> bool:
        self._click(coords.ENEMY_COORD[slot])
        return self._confirm(CONFIRM_STILL_COMMAND)

    def select_card(self, ui_slot: int) -> bool:
        self._click(coords.CARD_COORD[ui_slot])
        return self._confirm(CONFIRM_CARD_SELECTED)

    def select_np(self, servant_slot: int) -> bool:
        self._click(coords.NP_COORD[servant_slot])
        return self._confirm(CONFIRM_CARD_SELECTED)

    def attack(self) -> bool:
        self._click(coords.ATTACK_BTN)
        return self._confirm(CONFIRM_ATTACK_STARTED)

    # 注意：这里没有、也不会有 command_spell / sq_revive / gacha / ap_refill

    # ---- 内部 ----

    def _click(self, xy) -> None:
        x, y = xy
        self.controller.post_click(int(x), int(y)).wait()

    def _confirm(self, node: str) -> bool:
        img = self.controller.post_screencap().wait().get()
        r = self.ctx.run_recognition(node, img)
        return bool(r and r.hit)
