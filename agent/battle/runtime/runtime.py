"""回合循环：观测 -> 决策 -> 校验 -> 执行 -> 确认。fail-closed。

编排层：依赖 core（纯逻辑）+ perception/execution（集成层）+ 传入的 context。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..core.decider import Decider
from ..core.enums import PrimitiveKind, Scene
from ..core.policy import StrategyProfile
from ..core.validator import validate
from ..execution.executor import Executor
from ..perception import perception

# 连续非选卡场景（动画/加载）的最大等待次数，超过即判异常停止
_MAX_NONCOMMAND_WAITS = 60


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
        waits = 0
        while turns < self.profile.max_turns:
            img = self.controller.post_screencap().wait().get()
            state = perception.build(self.ctx, img)

            if state.scene is Scene.VICTORY:
                return BattleResult.success(turns)
            if state.scene in (Scene.DEFEAT, Scene.DIALOG, Scene.UNKNOWN):
                return BattleResult.fail(f"unsafe_scene:{state.scene.value}", turns)

            if state.scene is not Scene.COMMAND_SELECTION:
                waits += 1
                if waits > _MAX_NONCOMMAND_WAITS:
                    return BattleResult.fail("stuck_non_command", turns)
                self.ctx.wait_freezes(300)
                continue
            waits = 0

            action = self.decider.decide(state)
            verdict = validate(action, state, self.profile)
            if not verdict.ok:
                return BattleResult.fail(f"action_rejected:{verdict.reason}", turns)

            step = self._execute_turn(action)
            if not step:
                return BattleResult.fail("execution_confirm_failed", turns)
            turns += 1

        return BattleResult.fail("max_turns_exceeded", turns)

    def _execute_turn(self, action) -> bool:
        if action.target_enemy is not None:
            if not self.executor.select_enemy(action.target_enemy):
                return False
        for p in action.picks:
            if p.kind is PrimitiveKind.SELECT_NP:
                ok = self.executor.select_np(p.slot)
            else:
                ok = self.executor.select_card(p.slot)
            if not ok:
                return False
        return self.executor.attack()
