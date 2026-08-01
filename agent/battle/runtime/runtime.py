"""回合循环（按真实两屏流程 + 长动画宽容等待）。

流程：
  MAIN_BATTLE --点攻击--> COMMAND_SELECTION --选3张--> 自动发动 -> 20~40s 动画 -> 回主界面/胜利

设计要点：
- 攻击动画期间画面既非主界面也非选卡，会读成 UNKNOWN；这段是"宽容等待"（轮询到已知场景/超时），
  **不走 fail-closed 停止**，否则每回合攻击都会被误中止。
- 决策/校验失败、开卡/选卡确认失败、真正卡死超时才停止。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from ..core.decider import Decider
from ..core.enums import PrimitiveKind, Scene
from ..core.policy import StrategyProfile
from ..core.validator import validate
from ..execution.executor import Executor
from ..perception import perception

# 选完卡后等攻击动画结束（20~40s，留足余量）
_ANIMATION_TIMEOUT_S = 60.0
# 意外 UNKNOWN（加载等）的最大等待
_UNKNOWN_TIMEOUT_S = 15.0
# 每次轮询之间等画面静止的窗口（ms）
_POLL_FREEZE_MS = 500

_TERMINAL_OR_MAIN = (Scene.MAIN_BATTLE, Scene.VICTORY, Scene.DEFEAT)
_KNOWN_SCENES = (Scene.MAIN_BATTLE, Scene.COMMAND_SELECTION, Scene.VICTORY, Scene.DEFEAT)


@dataclass(frozen=True)
class BattleResult:
    ok: bool
    reason: str = ""
    turns: int = 0

    @staticmethod
    def success(turns: int) -> "BattleResult":
        return BattleResult(True, "victory", turns)

    @staticmethod
    def fail(reason: str, turns: int = 0) -> "BattleResult":
        return BattleResult(False, reason, turns)


class AutoBattleRuntime:
    def __init__(self, context, decider: Decider, profile: StrategyProfile) -> None:
        self.ctx = context
        self.controller = context.tasker.controller
        self.decider = decider
        self.profile = profile
        self.executor = Executor(context)

    def run(self) -> BattleResult:
        turns = 0
        while turns < self.profile.max_turns:
            state = self._observe()
            scene = state.scene

            if scene is Scene.VICTORY:
                return BattleResult.success(turns)
            if scene is Scene.DEFEAT:
                return BattleResult.fail("defeat", turns)
            if scene is Scene.DIALOG:
                return BattleResult.fail("unexpected_dialog", turns)

            if scene is Scene.MAIN_BATTLE:
                # V1b：主界面只点攻击开卡，不放主动技能
                if not self.executor.open_command_cards():
                    return BattleResult.fail("open_cards_failed", turns)
                continue

            if scene is Scene.COMMAND_SELECTION:
                print(f"[AutoBattle] ========== Turn {turns+1} ==========")
                print(f"[AutoBattle] State: {state}")
                action = self.decider.decide(state)
                print(f"[AutoBattle] Decided Action: {action}")
                
                verdict = validate(action, state, self.profile)
                if not verdict.ok:
                    print(f"[AutoBattle] Action rejected by validator! Reason: {verdict.reason}")
                    return BattleResult.fail(f"action_rejected:{verdict.reason}", turns)
                    
                print(f"[AutoBattle] Executing picks...")
                if not self._execute_selection(action):
                    print(f"[AutoBattle] Execution failed (confirmation error).")
                    return BattleResult.fail("selection_confirm_failed", turns)
                # 选完第 3 张自动发动 -> 等动画结束
                if not self._wait_turn_settled():
                    return BattleResult.fail("stuck_after_attack", turns)
                turns += 1
                continue

            # UNKNOWN / ANIMATION（非攻击后语境，如加载）：有界等待
            if not self._wait_until(_KNOWN_SCENES, _UNKNOWN_TIMEOUT_S):
                return BattleResult.fail("stuck_unknown_scene", turns)

        return BattleResult.fail("max_turns_exceeded", turns)

    # ---- 内部 ----

    def _observe(self):
        img = self.controller.post_screencap().wait().get()
        return perception.build(self.ctx, img)

    def _execute_selection(self, action) -> bool:
        for p in action.picks:
            if p.kind is PrimitiveKind.SELECT_NP:
                ok = self.executor.select_np(p.slot)
            else:
                ok = self.executor.select_card(p.slot)
            if not ok:
                return False
        return True

    def _wait_turn_settled(self) -> bool:
        return self._wait_until(_TERMINAL_OR_MAIN, _ANIMATION_TIMEOUT_S)

    def _wait_until(self, scenes, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._observe().scene in scenes:
                return True
            self.ctx.wait_freezes(_POLL_FREEZE_MS)
        return False
