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
from ..core.validator import (
    skip_unusable_servant_skills,
    validate_card_action,
    validate_main_action,
)
from ..execution.executor import Executor
from ..perception import perception
from ..vision.orchestrator import VisionOrchestrator, encode_png
from ..vision.trigger import VisionRuntimeTracker
import mfaalog

# 选完卡后等攻击动画结束（20~40s，留足余量）
_ANIMATION_TIMEOUT_S = 60.0
# 意外 UNKNOWN（加载等）的最大等待
_UNKNOWN_TIMEOUT_S = 15.0
# 每次轮询之间等画面静止的窗口（ms）
_POLL_FREEZE_MS = 500
# 胜利后结算点击流（掉落/羁绊/结果多屏）的最大耗时
_SETTLEMENT_TIMEOUT_S = 90.0

_TERMINAL_OR_MAIN = (Scene.MAIN_BATTLE, Scene.VICTORY, Scene.DEFEAT)
_KNOWN_SCENES = (Scene.MAIN_BATTLE, Scene.COMMAND_SELECTION, Scene.SKILL_TARGET_SELECTION, Scene.ORDER_CHANGE, Scene.VICTORY, Scene.DEFEAT)


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
    def __init__(self, context, controller, decider: Decider, profile: StrategyProfile, vision_orchestrator: VisionOrchestrator | None = None, vision_tracker: VisionRuntimeTracker | None = None) -> None:
        self.ctx = context
        self.controller = controller
        self.decider = decider
        self.profile = profile
        self.executor = Executor(context, controller)
        self.vision_orchestrator = vision_orchestrator
        self.vision_tracker = vision_tracker or VisionRuntimeTracker()
        self._turn_index = 0

    def run(self) -> BattleResult:
        mfaalog.info(f"[AutoBattle] run() start, max_turns={self.profile.max_turns}")
        turns = 0
        while turns < self.profile.max_turns:
            self._turn_index = turns
            state = self._observe()
            scene = state.scene
            mfaalog.info(f"[AutoBattle] Turn {turns+1} | scene={scene.name} | unknown={state.unknown_fields}")

            if scene is Scene.VICTORY:
                mfaalog.info(f"[AutoBattle] Victory! turns={turns} -> driving settlement")
                return self._drive_settlement(turns)
            if scene is Scene.DEFEAT:
                mfaalog.info(f"[AutoBattle] Defeat. turns={turns}")
                return BattleResult.fail("defeat", turns)
            if scene is Scene.DIALOG:
                mfaalog.info(f"[AutoBattle] Unexpected dialog. turns={turns}")
                return BattleResult.fail("unexpected_dialog", turns)

            if scene is Scene.MAIN_BATTLE:
                mfaalog.info("[AutoBattle] MAIN_BATTLE -> deciding skills...")
                action = self.decider.decide(state, turn_index=turns)
                action, skipped_skills = skip_unusable_servant_skills(
                    action, state, self.profile
                )
                for skipped in skipped_skills:
                    mfaalog.info(
                        f"[AutoBattle] servant skill skipped safely: {skipped}"
                    )
                verdict = validate_main_action(action, state, self.profile)
                if not verdict.ok:
                    mfaalog.info(
                        f"[AutoBattle] Main action rejected by validator: {verdict.reason}"
                    )
                    return BattleResult.fail(
                        f"main_action_rejected:{verdict.reason}", turns
                    )

                if action.target_enemy is not None:
                    enemy = next(
                        (
                            e for e in state.enemies
                            if e.slot == action.target_enemy and e.alive
                        ),
                        None,
                    )
                    if enemy is None:
                        mfaalog.info(
                            f"[AutoBattle] target enemy {action.target_enemy} not detected"
                        )
                        return BattleResult.fail("target_enemy_not_detected", turns)

                    if enemy.targeted:
                        mfaalog.info(
                            f"[AutoBattle] enemy {action.target_enemy} already targeted, skip click"
                        )
                    else:
                        mfaalog.info(
                            f"[AutoBattle] selecting enemy target {action.target_enemy}"
                        )
                        self._mark_action("select_enemy")
                        if not self.executor.select_enemy(action.target_enemy):
                            return BattleResult.fail("select_enemy_failed", turns)
                        if not self._wait_until_enemy_targeted(action.target_enemy):
                            return BattleResult.fail("enemy_target_confirm_failed", turns)

                if not self._execute_skills(action):
                    return BattleResult.fail("skill_execution_failed", turns)

                mfaalog.info("[AutoBattle] MAIN_BATTLE -> opening command cards (click attack)")
                self._mark_action("open_command_cards")
                if not self.executor.open_command_cards():
                    mfaalog.info(f"[AutoBattle] open_command_cards failed. turns={turns}")
                    return BattleResult.fail("open_cards_failed", turns)
                mfaalog.info("[AutoBattle] command cards clicked, confirming command selection scene...")
                if not self._wait_until((Scene.COMMAND_SELECTION,), 5.0):
                    mfaalog.info("[AutoBattle] command selection confirmation failed; stopping safely")
                    return BattleResult.fail("open_cards_confirm_failed", turns)
                mfaalog.info("[AutoBattle] command cards opened and confirmed")
                continue

            if scene is Scene.ORDER_CHANGE:
                # 换人界面：不应在主循环顶层出现，说明 _execute_skills 没处理完
                mfaalog.info(f"[AutoBattle] Unexpected ORDER_CHANGE scene in main loop. turns={turns}")
                return BattleResult.fail("unexpected_order_change_scene", turns)

            if scene is Scene.COMMAND_SELECTION:
                mfaalog.info(f"[AutoBattle] ========== Turn {turns+1} ==========")
                mfaalog.info(f"[AutoBattle] State: {state}")
                action = self.decider.decide(state, turn_index=turns)
                mfaalog.info(f"[AutoBattle] Decided Action: {action}")
                
                verdict = validate_card_action(action, state, self.profile)
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
        vision_result = None
        mfaalog.info(f"[AutoBattle] _observe() -> perception built, scene={result.scene.name}")
        if self.vision_orchestrator is not None and img is not None:
            image_bytes = encode_png(img)
            tracked = self.vision_tracker.observe(result.scene, image_bytes, unknown_fields=result.unknown_fields)
            vision_result = self.vision_orchestrator.analyze_state_if_needed(image_bytes, result, tracked.context, turn_index=self._turn_index)
            if vision_result.call.response is not None:
                response = vision_result.call.response
                if response.observation is not None:
                    mfaalog.info(f"[AutoBattle] vision supplement accepted model={response.model} conflicts={len(vision_result.conflicts)}")
                elif response.error:
                    mfaalog.info(f"[AutoBattle] vision supplement failed: {response.error}")
            elif vision_result.call.skipped:
                mfaalog.info(f"[AutoBattle] vision skipped: {vision_result.call.reason}")
        if vision_result is not None and vision_result.effective_state is not None:
            result = vision_result.effective_state
        return result

    def _mark_action(self, action: str) -> None:
        if self.vision_orchestrator is not None:
            self.vision_tracker.mark_action(action)

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

    def _wait_until_enemy_targeted(self, slot: int, timeout_s: float = 3.0) -> bool:
        """点击敌人后确认职介框蓝色选中态已经出现。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            state = self._observe()
            enemy = next((e for e in state.enemies if e.slot == slot and e.alive), None)
            if enemy is not None and enemy.targeted:
                mfaalog.info(f"[AutoBattle] enemy {slot} target confirmed")
                return True
            self.ctx.wait_freezes(_POLL_FREEZE_MS)
        mfaalog.info(f"[AutoBattle] enemy {slot} target confirmation timed out")
        return False

    def _execute_skills(self, action) -> bool:
        for sk in action.servant_skills:
            mfaalog.info(f"[AutoBattle] cast_servant_skill(slot={sk.servant_slot}, idx={sk.skill_index})")
            self._mark_action("cast_servant_skill")
            self.executor.cast_servant_skill(sk.servant_slot, sk.skill_index)
            if sk.target_ally is not None:
                mfaalog.info("[AutoBattle] waiting for skill target sub-screen...")
                if self._wait_until((Scene.SKILL_TARGET_SELECTION,), 5.0):
                    mfaalog.info(f"[AutoBattle] selecting skill target ally={sk.target_ally}")
                    self.executor.select_skill_target(sk.target_ally)
                else:
                    mfaalog.info("[AutoBattle] failed to see skill target sub-screen!")
                    return False
            # Wait for animation to finish and return to MAIN_BATTLE
            if not self._wait_until((Scene.MAIN_BATTLE,), 15.0):
                return False

        for sk in action.master_skills:
            mfaalog.info(f"[AutoBattle] cast_master_skill(idx={sk.skill_index})")
            self._mark_action("cast_master_skill")
            self.executor.cast_master_skill(sk.skill_index)
            if sk.target_ally is not None:
                mfaalog.info("[AutoBattle] waiting for skill target sub-screen...")
                if self._wait_until((Scene.SKILL_TARGET_SELECTION,), 5.0):
                    mfaalog.info(f"[AutoBattle] selecting skill target ally={sk.target_ally}")
                    self.executor.select_skill_target(sk.target_ally)
                else:
                    return False
            # 御主技能可能是换人技能 → 进入 ORDER_CHANGE 场景
            # 也可能直接回 MAIN_BATTLE（普通技能）
            if action.order_change is not None:
                # 换人技能：等 ORDER_CHANGE 场景，由后面的 order_change 逻辑处理
                if not self._wait_until((Scene.ORDER_CHANGE, Scene.MAIN_BATTLE), 10.0):
                    return False
            else:
                if not self._wait_until((Scene.MAIN_BATTLE,), 15.0):
                    return False

        if action.order_change is not None:
            oc = action.order_change
            mfaalog.info(f"[AutoBattle] order_change(starting={oc.starting_member_idx}, sub={oc.sub_member_idx})")
            # 换人技能已由前面的 master_skills 触发（御主换人服技能）
            # 等待换人界面出现
            if not self._wait_until((Scene.ORDER_CHANGE,), 10.0):
                mfaalog.info("[AutoBattle] failed to see order change screen!")
                return False
            # 在换人界面选择首发成员和候补成员
            self._mark_action("order_change")
            self.executor.order_change(oc.starting_member_idx, oc.sub_member_idx)
            # 等待回到主界面
            if not self._wait_until((Scene.MAIN_BATTLE,), 25.0):
                return False

        return True

    def _drive_settlement(self, turns: int) -> BattleResult:
        """胜利后点击穿过结算多屏（掉落/羁绊/结果）直到回关卡列表/主界面。

        标定护栏：坐标未标定时（executor.tap_settlement_continue 返回 False），
        不盲点，直接按现有行为返回胜利（战斗已赢，只是暂不能自动点回主界面）。
        """
        mfaalog.info(f"[AutoBattle] _drive_settlement() timeout={_SETTLEMENT_TIMEOUT_S}s")
        deadline = time.monotonic() + _SETTLEMENT_TIMEOUT_S
        while time.monotonic() < deadline:
            img = self.controller.post_screencap().wait().get()
            if perception.reached_post_battle(self.ctx, img):
                mfaalog.info("[AutoBattle] settlement done -> back to quest list")
                return BattleResult.success(turns)
            if not self.executor.tap_settlement_continue():
                mfaalog.info("[AutoBattle] settlement not calibrated -> reporting victory without click-through")
                return BattleResult.success(turns)
            self.ctx.wait_freezes(_POLL_FREEZE_MS)
        mfaalog.info(f"[AutoBattle] settlement did not finish within {_SETTLEMENT_TIMEOUT_S}s")
        return BattleResult.fail("settlement_timeout", turns)

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
