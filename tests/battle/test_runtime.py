"""AutoBattleRuntime 安全边界测试。"""
from __future__ import annotations

import os
import sys
import types

AGENT = os.path.join(os.path.dirname(__file__), "..", "..", "agent")
sys.path.insert(0, os.path.abspath(AGENT))

fake_mfaalog = types.ModuleType("mfaalog")
fake_mfaalog.info = lambda *_args, **_kwargs: None
sys.modules.setdefault("mfaalog", fake_mfaalog)

from battle.core.enums import CardColor, PrimitiveKind, Scene
from battle.core.models import (
    BattleAction,
    BattleState,
    CardPick,
    CommandCard,
    Confidence,
    EnemyState,
    ServantSkillAction,
    ServantState,
    SkillState,
)
from battle.core.policy import StrategyProfile
from battle.runtime.runtime import AutoBattleRuntime


class _Controller:
    pass


class _Context:
    def wait_freezes(self, _milliseconds):
        return None


class _Decider:
    def decide(self, _state, turn_index=0):
        return BattleAction(
            target_enemy=1,
            picks=(),
            servant_skills=(ServantSkillAction(1, 1),),
        )


class _ExecutorTracksCalls:
    def __init__(self):
        self.skill_called = False
        self.open_cards_called = False
        self.close_dialog_called = False
        self.card_slots = []
        self.np_slots = []

    def cast_servant_skill(self, *_args, **_kwargs):
        self.skill_called = True
        return True

    def open_command_cards(self):
        self.open_cards_called = True
        return True

    def close_skill_use_dialog(self):
        self.close_dialog_called = True
        return True

    def select_card(self, slot):
        self.card_slots.append(slot)
        return True

    def select_np(self, slot):
        self.np_slots.append(slot)
        return True


def test_runtime_skips_unknown_skill_and_continues_to_attack(monkeypatch):
    state = BattleState(
        scene=Scene.MAIN_BATTLE,
        scene_confidence=Confidence(0.99, "test"),
        cards=(),
        np_cards=(),
        enemies=(EnemyState(1, True, True, Confidence(0.99, "test")),),
        servants=(ServantState(
            slot=1,
            skills=(
                SkillState(None, Confidence(0.0, "test")),
                SkillState(True, Confidence(0.99, "test")),
                SkillState(True, Confidence(0.99, "test")),
            ),
            confidence=Confidence(0.99, "test"),
        ),),
    )
    defeat = BattleState(
        scene=Scene.DEFEAT,
        scene_confidence=Confidence(0.99, "test"),
        cards=(),
        np_cards=(),
        enemies=(),
    )
    observations = iter((state, defeat))
    runtime = AutoBattleRuntime(_Context(), _Controller(), _Decider(), StrategyProfile())
    executor = _ExecutorTracksCalls()
    runtime.executor = executor
    monkeypatch.setattr(runtime, "_observe", lambda: next(observations))
    monkeypatch.setattr(runtime, "_wait_until", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(runtime, "_close_skill_use_dialog_if_present", lambda: False)

    result = runtime.run()

    assert result.ok is False
    assert result.reason == "defeat"
    assert executor.skill_called is False
    assert executor.open_cards_called is True


class _Reco:
    hit = True


class _PostResult:
    def wait(self):
        return self

    def get(self):
        return object()


class _ScreenshotController:
    def post_screencap(self):
        return _PostResult()


class _DialogContext(_Context):
    def run_recognition(self, node_name, _img):
        if node_name == "战斗_技能使用弹窗":
            return _Reco()
        return None


def test_runtime_closes_skill_use_dialog_and_continues():
    runtime = AutoBattleRuntime(
        _DialogContext(),
        _ScreenshotController(),
        _Decider(),
        StrategyProfile(),
    )
    executor = _ExecutorTracksCalls()
    runtime.executor = executor

    assert runtime._close_skill_use_dialog_if_present() is True
    assert executor.close_dialog_called is True


class _FixedDecider:
    def __init__(self, action):
        self.action = action

    def decide(self, _state, turn_index=0):
        return self.action


def test_runtime_rejects_structurally_invalid_main_action():
    state = BattleState(
        scene=Scene.MAIN_BATTLE,
        scene_confidence=Confidence(0.99, "test"),
        cards=(),
        np_cards=(),
        enemies=(EnemyState(1, True, True, Confidence(0.99, "test")),),
        servants=(),
    )
    action = BattleAction(
        target_enemy=1,
        picks=(),
        servant_skills=(ServantSkillAction(4, 1),),
    )
    runtime = AutoBattleRuntime(
        _Context(), _Controller(), _FixedDecider(action), StrategyProfile()
    )
    executor = _ExecutorTracksCalls()
    runtime.executor = executor
    runtime._observe = lambda: state

    result = runtime.run()

    assert result.ok is False
    assert result.reason == "invalid_main_action:invalid_servant_skill"
    assert executor.skill_called is False
    assert executor.open_cards_called is False


def test_runtime_replaces_unavailable_np_with_face_card(monkeypatch):
    command = BattleState(
        scene=Scene.COMMAND_SELECTION,
        scene_confidence=Confidence(0.99, "test"),
        cards=tuple(
            CommandCard(
                ui_slot=slot,
                color=CardColor.BUSTER,
                owner_slot=None,
                confidence=Confidence(0.99, "test"),
            )
            for slot in range(1, 6)
        ),
        np_cards=(),
        enemies=(),
    )
    defeat = BattleState(
        scene=Scene.DEFEAT,
        scene_confidence=Confidence(0.99, "test"),
        cards=(),
        np_cards=(),
        enemies=(),
    )
    action = BattleAction(
        target_enemy=None,
        picks=(
            CardPick(PrimitiveKind.SELECT_NP, 1),
            CardPick(PrimitiveKind.SELECT_CARD, 1),
            CardPick(PrimitiveKind.SELECT_CARD, 2),
        ),
    )
    # 选卡：进入选卡 -> 确认第1张仍选卡 -> 确认第2张仍选卡 -> 第3张自动开火 -> 下一回合读到失败
    observations = iter((command, command, command, defeat))
    runtime = AutoBattleRuntime(
        _Context(), _Controller(), _FixedDecider(action), StrategyProfile()
    )
    executor = _ExecutorTracksCalls()
    runtime.executor = executor
    monkeypatch.setattr(runtime, "_observe", lambda: next(observations))
    monkeypatch.setattr(runtime, "_wait_turn_settled", lambda: True)
    monkeypatch.setattr("battle.runtime.runtime.time.sleep", lambda _seconds: None)

    result = runtime.run()

    assert result.ok is False
    assert result.reason == "defeat"
    assert executor.np_slots == []
    assert executor.card_slots == [1, 2, 3]


def test_runtime_rejects_invalid_np_slot_without_clicking():
    command = BattleState(
        scene=Scene.COMMAND_SELECTION,
        scene_confidence=Confidence(0.99, "test"),
        cards=tuple(
            CommandCard(
                ui_slot=slot,
                color=CardColor.BUSTER,
                owner_slot=None,
                confidence=Confidence(0.99, "test"),
            )
            for slot in range(1, 6)
        ),
        np_cards=(),
        enemies=(),
    )
    action = BattleAction(
        target_enemy=None,
        picks=(
            CardPick(PrimitiveKind.SELECT_NP, 4),
            CardPick(PrimitiveKind.SELECT_CARD, 1),
            CardPick(PrimitiveKind.SELECT_CARD, 2),
        ),
    )
    runtime = AutoBattleRuntime(
        _Context(), _Controller(), _FixedDecider(action), StrategyProfile()
    )
    executor = _ExecutorTracksCalls()
    runtime.executor = executor
    runtime._observe = lambda: command

    result = runtime.run()

    assert result.ok is False
    assert result.reason == "invalid_card_action:np_not_present"
    assert executor.np_slots == []
    assert executor.card_slots == []


def test_runtime_confirms_still_in_command_selection_between_picks(monkeypatch):
    """点击非末张卡后仍在选卡界面 -> 继续执行全部 picks。"""
    command = BattleState(
        scene=Scene.COMMAND_SELECTION,
        scene_confidence=Confidence(0.99, "test"),
        cards=tuple(
            CommandCard(
                ui_slot=slot,
                color=CardColor.BUSTER,
                owner_slot=None,
                confidence=Confidence(0.99, "test"),
            )
            for slot in range(1, 6)
        ),
        np_cards=(),
        enemies=(),
    )
    defeat = BattleState(
        scene=Scene.DEFEAT,
        scene_confidence=Confidence(0.99, "test"),
        cards=(),
        np_cards=(),
        enemies=(),
    )
    action = BattleAction(
        target_enemy=None,
        picks=tuple(
            CardPick(PrimitiveKind.SELECT_CARD, slot) for slot in (1, 2, 3)
        ),
    )
    # 进入选卡 -> 确认 pick1 -> 确认 pick2 -> 第 3 张开火 -> 下一回合 defeat
    observations = iter((command, command, command, defeat))
    runtime = AutoBattleRuntime(
        _Context(), _Controller(), _FixedDecider(action), StrategyProfile()
    )
    executor = _ExecutorTracksCalls()
    runtime.executor = executor
    monkeypatch.setattr(runtime, "_observe", lambda: next(observations))
    monkeypatch.setattr(runtime, "_wait_turn_settled", lambda: True)
    monkeypatch.setattr("battle.runtime.runtime.time.sleep", lambda _seconds: None)

    result = runtime.run()

    assert result.reason == "defeat"
    assert executor.card_slots == [1, 2, 3]


def test_runtime_fails_closed_when_leaves_command_selection_mid_pick(monkeypatch):
    """点击非末张卡后不再处于选卡界面 -> fail-closed, 不执行剩余 pick。"""
    command = BattleState(
        scene=Scene.COMMAND_SELECTION,
        scene_confidence=Confidence(0.99, "test"),
        cards=tuple(
            CommandCard(
                ui_slot=slot,
                color=CardColor.BUSTER,
                owner_slot=None,
                confidence=Confidence(0.99, "test"),
            )
            for slot in range(1, 6)
        ),
        np_cards=(),
        enemies=(),
    )
    main = BattleState(
        scene=Scene.MAIN_BATTLE,
        scene_confidence=Confidence(0.99, "test"),
        cards=(),
        np_cards=(),
        enemies=(),
    )
    action = BattleAction(
        target_enemy=None,
        picks=tuple(
            CardPick(PrimitiveKind.SELECT_CARD, slot) for slot in (1, 2, 3)
        ),
    )
    # 进入选卡 -> 点第1张后异常离开选卡界面(主界面) -> 确认失败
    observations = iter((command, main))
    runtime = AutoBattleRuntime(
        _Context(), _Controller(), _FixedDecider(action), StrategyProfile()
    )
    executor = _ExecutorTracksCalls()
    runtime.executor = executor
    monkeypatch.setattr(runtime, "_observe", lambda: next(observations))
    monkeypatch.setattr("battle.runtime.runtime.time.sleep", lambda _seconds: None)

    result = runtime.run()

    assert result.ok is False
    assert result.reason == "selection_confirm_failed"
    assert executor.card_slots == [1]
