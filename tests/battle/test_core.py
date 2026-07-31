"""纯核心单测：不导入 maa / cv2 / 设备。运行：pytest tests/battle -q"""
import os
import sys

AGENT = os.path.join(os.path.dirname(__file__), "..", "..", "agent")
sys.path.insert(0, os.path.abspath(AGENT))

from battle.core.enums import CardColor, PrimitiveKind, Scene
from battle.core.models import (BattleAction, BattleState, CardPick, CommandCard,
                                 Confidence, EnemyState, NpCard)
from battle.core.policy import CardPolicy, Goal, StrategyProfile
from battle.core.decider import RuleDecider
from battle.core.validator import validate


def _card(ui_slot, color, conf=0.95):
    return CommandCard(ui_slot, CardColor(color), None, Confidence(conf, "cm"))


def make_state(cards, np_cards=(), enemies=None, scene_conf=0.97):
    if enemies is None:
        enemies = (EnemyState(1, True, True, Confidence(0.95, "tpl")),)
    return BattleState(
        scene=Scene.COMMAND_SELECTION,
        scene_confidence=Confidence(scene_conf, "tpl"),
        cards=tuple(cards),
        np_cards=tuple(NpCard(s, Confidence(0.9, "tpl")) for s in np_cards),
        enemies=tuple(enemies),
        screenshot_id="t",
    )


PROFILE = StrategyProfile()


# ---------- RuleDecider ----------

def test_np_first_then_fill_to_three():
    state = make_state(
        cards=[_card(1, "A"), _card(2, "B"), _card(3, "Q"), _card(4, "B"), _card(5, "A")],
        np_cards=(1, 3),
    )
    action = RuleDecider().decide(state)
    assert len(action.picks) == 3
    np_picks = [p for p in action.picks if p.kind is PrimitiveKind.SELECT_NP]
    assert {p.slot for p in np_picks} == {1, 3}          # 两张宝具卡都被优先选中
    assert action.picks[0].kind is PrimitiveKind.SELECT_NP


def test_no_np_finish_wave_prefers_buster_first_and_last():
    state = make_state(
        cards=[_card(1, "A"), _card(2, "B"), _card(3, "Q"), _card(4, "B"), _card(5, "A")],
    )
    policy = CardPolicy(goal=Goal.FINISH_WAVE)
    action = RuleDecider(policy).decide(state)
    slots = [p.slot for p in action.picks]
    assert all(p.kind is PrimitiveKind.SELECT_CARD for p in action.picks)
    assert slots[0] in (2, 4)     # 首卡 Buster
    assert slots[-1] in (2, 4)    # 末卡 Buster


def test_three_np_cards_all_np():
    state = make_state(
        cards=[_card(i, "A") for i in range(1, 6)],
        np_cards=(1, 2, 3),
    )
    action = RuleDecider().decide(state)
    assert all(p.kind is PrimitiveKind.SELECT_NP for p in action.picks)


def test_target_prefers_targeted_then_first_alive():
    enemies = (EnemyState(1, True, False, Confidence(0.95, "t")),
               EnemyState(2, True, True, Confidence(0.95, "t")),
               EnemyState(3, False, False, Confidence(0.95, "t")))
    state = make_state([_card(i, "B") for i in range(1, 6)], enemies=enemies)
    assert RuleDecider().decide(state).target_enemy == 2


# ---------- Validator ----------

def test_valid_action_passes():
    state = make_state([_card(i, "B") for i in range(1, 6)], np_cards=(1,))
    action = RuleDecider().decide(state)
    assert validate(action, state, PROFILE).ok


def test_reject_duplicate_picks():
    state = make_state([_card(i, "B") for i in range(1, 6)])
    action = BattleAction(1, (CardPick(PrimitiveKind.SELECT_CARD, 1),
                              CardPick(PrimitiveKind.SELECT_CARD, 1),
                              CardPick(PrimitiveKind.SELECT_CARD, 2)))
    assert not validate(action, state, PROFILE).ok


def test_reject_card_not_present():
    state = make_state([_card(i, "B") for i in range(1, 6)])
    action = BattleAction(1, (CardPick(PrimitiveKind.SELECT_CARD, 9),
                              CardPick(PrimitiveKind.SELECT_CARD, 2),
                              CardPick(PrimitiveKind.SELECT_CARD, 3)))
    v = validate(action, state, PROFILE)
    assert not v.ok and v.reason == "card_not_present"


def test_reject_np_not_present():
    state = make_state([_card(i, "B") for i in range(1, 6)], np_cards=(1,))
    action = BattleAction(1, (CardPick(PrimitiveKind.SELECT_NP, 2),
                              CardPick(PrimitiveKind.SELECT_CARD, 2),
                              CardPick(PrimitiveKind.SELECT_CARD, 3)))
    assert validate(action, state, PROFILE).reason == "np_not_present"


def test_reject_low_scene_confidence():
    state = make_state([_card(i, "B") for i in range(1, 6)], scene_conf=0.5)
    action = BattleAction(1, (CardPick(PrimitiveKind.SELECT_CARD, 1),
                              CardPick(PrimitiveKind.SELECT_CARD, 2),
                              CardPick(PrimitiveKind.SELECT_CARD, 3)))
    assert validate(action, state, PROFILE).reason == "scene_not_command_selection"


def test_reject_invalid_enemy_target():
    enemies = (EnemyState(1, False, False, Confidence(0.95, "t")),)
    state = make_state([_card(i, "B") for i in range(1, 6)], enemies=enemies)
    action = BattleAction(1, (CardPick(PrimitiveKind.SELECT_CARD, 1),
                              CardPick(PrimitiveKind.SELECT_CARD, 2),
                              CardPick(PrimitiveKind.SELECT_CARD, 3)))
    assert validate(action, state, PROFILE).reason == "invalid_enemy_target"
