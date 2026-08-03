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

from battle.core.enums import Scene
from battle.core.models import (
    BattleAction,
    BattleState,
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

    def cast_servant_skill(self, *_args, **_kwargs):
        self.skill_called = True
        return True

    def open_command_cards(self):
        self.open_cards_called = True
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

    result = runtime.run()

    assert result.ok is False
    assert result.reason == "defeat"
    assert executor.skill_called is False
    assert executor.open_cards_called is True