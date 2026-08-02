"""原子操作层：受限原子动作 -> 1280x720 坐标点击。

真实流程（已按游戏确认）：
  主界面(有攻击钮) --open_command_cards()--> 选卡界面 --选3张--> 选完第3张自动发动 -> 动画

安全边界（硬禁区）：本类**故意不提供** 令咒/圣晶石复活/氪金/抽卡/补 AP 入口。
"""
from __future__ import annotations

from . import coords


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
        self._click(coords.ENEMY_POINT[slot])
        return True

    def cast_servant_skill(self, servant_slot: int, skill_index: int) -> bool:
        self._click(coords.SERVANT_SKILL_CLICK[(servant_slot, skill_index)])
        return True

    def select_skill_target(self, target_ally: int) -> bool:
        self._click(coords.SKILL_TARGET_ALLY[target_ally])
        return True

    def cast_master_skill(self, skill_index: int) -> bool:
        import time
        self._click(coords.MASTER_SKILL_MENU_BTN)
        time.sleep(0.5)
        self._click(coords.MASTER_SKILL_CLICK[skill_index])
        return True

    def order_change(self, starting_member_idx: int, sub_member_idx: int) -> bool:
        import time
        self._click(coords.ORDER_CHANGE_MEMBER[starting_member_idx])
        time.sleep(0.3)
        self._click(coords.ORDER_CHANGE_MEMBER[sub_member_idx])
        time.sleep(0.3)
        self._click(coords.ORDER_CHANGE_CONFIRM_BTN)
        return True

    def cancel_order_change(self) -> bool:
        """换人界面点取消/退出按钮。"""
        self._click(coords.ORDER_CHANGE_CANCEL_BTN)
        return True

    # 注意：无 attack()——选完第 3 张卡自动发动；也没有令咒/圣晶石/氪金/抽卡入口

    def _click(self, xy) -> None:
        x, y = xy
        self.controller.post_click(int(x), int(y)).wait()
