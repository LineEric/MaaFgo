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
import mfaalog

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
    def __init__(self, context, controller, decider: Decider, profile: StrategyProfile) -> None:
        self.ctx = context
        self.controller = controller
        self.decider = decider
        self.profile = profile
        self.executor = Executor(context, controller)

    def run(self) -> BattleResult:
        mfaalog.info(f"[AutoBattle] run() start, max_turns={self.profile.max_turns}")
        turns = 0
        while turns < self.profile.max_turns:
            state = self._observe()
            scene = state.scene
            mfaalog.info(f"[AutoBattle] Turn {turns+1} | scene={scene.name} | unknown={state.unknown_fields}")

            if scene is Scene.VICTORY:
                mfaalog.info(f"[AutoBattle] Victory! turns={turns}")
                return BattleResult.success(turns)
            if scene is Scene.DEFEAT:
                mfaalog.info(f"[AutoBattle] Defeat. turns={turns}")
                return BattleResult.fail("defeat", turns)
            if scene is Scene.DIALOG:
                mfaalog.info(f"[AutoBattle] Unexpected dialog. turns={turns}")
                return BattleResult.fail("unexpected_dialog", turns)

            if scene is Scene.MAIN_BATTLE:
                # V1b：主界面只点攻击开卡，不放主动技能
                mfaalog.info("[AutoBattle] MAIN_BATTLE -> opening command cards (click attack)")
                if not self.executor.open_command_cards():
                    mfaalog.info(f"[AutoBattle] open_command_cards failed. turns={turns}")
                    return BattleResult.fail("open_cards_failed", turns)
                mfaalog.info("[AutoBattle] command cards opened, waiting for card animation...")
                time.sleep(2)
                mfaalog.info("[AutoBattle] continuing after card animation wait")
                continue

            if scene is Scene.COMMAND_SELECTION:
                mfaalog.info(f"[AutoBattle] ========== Turn {turns+1} ==========")
                mfaalog.info(f"[AutoBattle] State: {state}")
                action = self.decider.decide(state)
                mfaalog.info(f"[AutoBattle] Decided Action: {action}")
                
                verdict = validate(action, state, self.profile)
                if not verdict.ok:
                    mfaalog.info(f"[AutoBattle] Action rejected by validator! Reason: {verdict.reason}")
                    return BattleResult.fail(f"action_rejected:{verdict.reason}", turns)
                    
                mfaalog.info("[AutoBattle] Executing picks...")
                if not self._execute_selection(action):
                    mfaalog.info("[AutoBattle] Execution failed (confirmation error).")
                    return BattleResult.fail("selection_confirm_failed", turns)
                mfaalog.info("[AutoBattle] Picks executed, waiting for attack animation to settle...")
                # 选完第 3 张自动发动 -> 等动画结束
                if not self._wait_turn_settled():
                    mfaalog.info(f"[AutoBattle] Stuck after attack (no scene change within {_ANIMATION_TIMEOUT_S}s). turns={turns}")
                    return BattleResult.fail("stuck_after_attack", turns)
                mfaalog.info(f"[AutoBattle] Turn {turns+1} settled, advancing to turn {turns+2}")
                turns += 1
                continue

            # UNKNOWN / ANIMATION（非攻击后语境，如加载）：有界等待
            mfaalog.info(f"[AutoBattle] Unknown scene, waiting up to {_UNKNOWN_TIMEOUT_S}s for known scene...")
            if not self._wait_until(_KNOWN_SCENES, _UNKNOWN_TIMEOUT_S):
                mfaalog.info(f"[AutoBattle] Stuck in unknown scene for {_UNKNOWN_TIMEOUT_S}s. turns={turns}")
                return BattleResult.fail("stuck_unknown_scene", turns)
            mfaalog.info("[AutoBattle] Recovered from unknown scene, continuing")

        mfaalog.info(f"[AutoBattle] Max turns ({self.profile.max_turns}) exceeded")
        return BattleResult.fail("max_turns_exceeded", turns)

    # ---- 内部 ----

    def _observe(self):
        mfaalog.info("[AutoBattle] _observe() -> post_screencap")
        img = self.controller.post_screencap().wait().get()
        mfaalog.info(f"[AutoBattle] _observe() -> screencap done, shape={img.shape if img is not None else 'None'}")
        result = perception.build(self.ctx, img)
        mfaalog.info(f"[AutoBattle] _observe() -> perception built, scene={result.scene.name}")
        return result

    def _execute_selection(self, action) -> bool:
        mfaalog.info(f"[AutoBattle] _execute_selection() picks={action.picks}")
        for p in action.picks:
            if p.kind is PrimitiveKind.SELECT_NP:
                mfaalog.info(f"[AutoBattle] select_np(slot={p.slot})")
                ok = self.executor.select_np(p.slot)
            else:
                mfaalog.info(f"[AutoBattle] select_card(slot={p.slot})")
                ok = self.executor.select_card(p.slot)
            mfaalog.info(f"[AutoBattle] pick result: ok={ok}")
            if not ok:
                return False
            time.sleep(1)
        return True

    def _wait_turn_settled(self) -> bool:
        mfaalog.info(f"[AutoBattle] _wait_turn_settled() timeout={_ANIMATION_TIMEOUT_S}s")
        result = self._wait_until(_TERMINAL_OR_MAIN, _ANIMATION_TIMEOUT_S)
        mfaalog.info(f"[AutoBattle] _wait_turn_settled() result={result}")
        return result

    def _wait_until(self, scenes, timeout_s: float) -> bool:
        mfaalog.info(f"[AutoBattle] _wait_until() scenes={[s.name for s in scenes]} timeout={timeout_s}s")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            state = self._observe()
            if state.scene in scenes:
                mfaalog.info(f"[AutoBattle] _wait_until() matched scene={state.scene.name}")
                return True
            mfaalog.info(f"[AutoBattle] _wait_until() scene={state.scene.name}, waiting freezes {_POLL_FREEZE_MS}ms")
            self.ctx.wait_freezes(_POLL_FREEZE_MS)
        mfaalog.info(f"[AutoBattle] _wait_until() timed out")
        return False
