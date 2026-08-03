"""纯核心单测：不导入 maa / cv2 / 设备。运行：pytest tests/battle -q"""
import os
import sys

AGENT = os.path.join(os.path.dirname(__file__), "..", "..", "agent")
sys.path.insert(0, os.path.abspath(AGENT))

from battle.core.enums import CardColor, PrimitiveKind, Scene
from battle.core.models import (BattleAction, BattlePlan, BattleState, CardPick,
                                 CommandCard, Confidence, EnemyState, MasterSkillAction,
                                 NpCard, OrderChangeAction, ServantSkillAction, SkillState, ServantState, TurnPlan)
from battle.core.policy import CardPolicy, Goal, StrategyProfile
from battle.core.decider import RuleDecider
from battle.core.validator import (
    skip_unusable_servant_skills,
    validate,
    validate_card_action,
    validate_main_action,
)


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


# ---------- V2 TurnPlan / BattlePlan ----------

def _main_battle_state(enemies=None):
    if enemies is None:
        enemies = (EnemyState(1, True, True, Confidence(0.95, "t")),)
    return BattleState(
        scene=Scene.MAIN_BATTLE,
        scene_confidence=Confidence(0.97, "tpl"),
        cards=(),
        np_cards=(),
        enemies=tuple(enemies),
        servants=(ServantState(
            slot=1,
            skills=(
                SkillState(True, Confidence(0.99, "test")),
                SkillState(True, Confidence(0.99, "test")),
                SkillState(True, Confidence(0.99, "test")),
            ),
            confidence=Confidence(0.99, "test"),
        ),),
        screenshot_id="t",
    )


def test_main_battle_no_plan_returns_empty_skills():
    state = _main_battle_state()
    action = RuleDecider().decide(state, turn_index=0)
    assert action.servant_skills == ()
    assert action.master_skills == ()
    assert action.order_change is None
    assert action.picks == ()


def test_main_battle_with_plan_returns_skills():
    plan = BattlePlan(turns=(
        TurnPlan(
            servant_skills=(ServantSkillAction(1, 1, target_ally=2),),
            master_skills=(MasterSkillAction(1),),
        ),
    ))
    state = _main_battle_state()
    action = RuleDecider(plan=plan).decide(state, turn_index=0)
    assert len(action.servant_skills) == 1
    assert action.servant_skills[0].servant_slot == 1
    assert action.servant_skills[0].skill_index == 1
    assert action.servant_skills[0].target_ally == 2
    assert len(action.master_skills) == 1
    assert action.master_skills[0].skill_index == 1


def test_main_battle_with_order_change():
    plan = BattlePlan(turns=(
        TurnPlan(
            master_skills=(MasterSkillAction(2),),
            order_change=OrderChangeAction(starting_member_idx=1, sub_member_idx=4),
        ),
    ))
    state = _main_battle_state()
    action = RuleDecider(plan=plan).decide(state, turn_index=0)
    assert action.order_change is not None
    assert action.order_change.starting_member_idx == 1
    assert action.order_change.sub_member_idx == 4


def test_plan_turn_index_out_of_range_returns_empty():
    plan = BattlePlan(turns=(
        TurnPlan(servant_skills=(ServantSkillAction(1, 1),)),
    ))
    state = _main_battle_state()
    action = RuleDecider(plan=plan).decide(state, turn_index=5)
    assert action.servant_skills == ()
    assert action.master_skills == ()


def test_command_selection_plan_np_order():
    plan = BattlePlan(turns=(
        TurnPlan(np_order=(3, 1)),  # 先出从者3宝具，再出从者1宝具
    ))
    state = make_state(
        cards=[_card(i, "A") for i in range(1, 6)],
        np_cards=(1, 2, 3),
    )
    action = RuleDecider(plan=plan).decide(state, turn_index=0)
    np_picks = [p for p in action.picks if p.kind is PrimitiveKind.SELECT_NP]
    assert len(np_picks) == 2
    assert np_picks[0].slot == 3   # 计划指定先出3
    assert np_picks[1].slot == 1   # 再出1


def test_command_selection_plan_target_enemy():
    enemies = (EnemyState(1, True, False, Confidence(0.95, "t")),
               EnemyState(2, True, False, Confidence(0.95, "t")),
               EnemyState(3, True, True, Confidence(0.95, "t")))
    plan = BattlePlan(turns=(
        TurnPlan(target_enemy=1),  # 计划指定打敌人1，即使敌人3被选中
    ))
    state = make_state([_card(i, "B") for i in range(1, 6)], enemies=enemies)
    action = RuleDecider(plan=plan).decide(state, turn_index=0)
    assert action.target_enemy == 1


def test_command_selection_no_plan_falls_back_to_np_first():
    state = make_state(
        cards=[_card(i, "A") for i in range(1, 6)],
        np_cards=(1, 3),
    )
    action = RuleDecider().decide(state, turn_index=0)
    np_picks = [p for p in action.picks if p.kind is PrimitiveKind.SELECT_NP]
    assert {p.slot for p in np_picks} == {1, 3}


def test_battle_plan_turn_method():
    plan = BattlePlan(turns=(
        TurnPlan(servant_skills=(ServantSkillAction(1, 1),)),
        TurnPlan(master_skills=(MasterSkillAction(1),)),
    ))
    assert len(plan.turn(0).servant_skills) == 1
    assert len(plan.turn(1).master_skills) == 1
    assert plan.turn(99).servant_skills == ()  # 越界返回空

def test_main_battle_safety_gate_skips_unusable_servant_skills():
    plan = BattlePlan(turns=(
        TurnPlan(servant_skills=(
            ServantSkillAction(1, 1),
            ServantSkillAction(1, 2),
            ServantSkillAction(1, 3),
        )),
    ))
    servants = (ServantState(
        slot=1,
        skills=(
            SkillState(False, Confidence(0.99, "ocr:cd")),
            SkillState(None, Confidence(0.0, "ocr:unknown")),
            SkillState(True, Confidence(0.99, "ocr:available")),
        ),
        confidence=Confidence(0.99, "composite"),
    ),)
    state = BattleState(
        scene=Scene.MAIN_BATTLE,
        scene_confidence=Confidence(0.97, "tpl"),
        cards=(),
        np_cards=(),
        enemies=(EnemyState(1, True, True, Confidence(0.95, "t")),),
        servants=servants,
        screenshot_id="t",
    )
    planned = RuleDecider(plan=plan).decide(state, turn_index=0)
    action, skipped = skip_unusable_servant_skills(planned, state, PROFILE)

    assert [(s.servant_slot, s.skill_index) for s in planned.servant_skills] == [
        (1, 1), (1, 2), (1, 3)
    ]
    assert [(s.servant_slot, s.skill_index) for s in action.servant_skills] == [(1, 3)]
    assert skipped == (
        "servant[1].skill[1].available:cooldown",
        "servant[1].skill[2].available:unknown",
    )


def test_main_battle_safety_gate_skips_low_confidence_and_missing_state():
    state = BattleState(
        scene=Scene.MAIN_BATTLE,
        scene_confidence=Confidence(0.97, "tpl"),
        cards=(),
        np_cards=(),
        enemies=(EnemyState(1, True, True, Confidence(0.95, "t")),),
        servants=(ServantState(
            slot=1,
            skills=(
                SkillState(True, Confidence(0.4, "ocr:available")),
                SkillState(True, Confidence(0.99, "ocr:available")),
                SkillState(True, Confidence(0.99, "ocr:available")),
            ),
            confidence=Confidence(0.99, "composite"),
        ),),
    )
    planned = _main_action(servant_skills=(
        ServantSkillAction(1, 1),
        ServantSkillAction(2, 1),
    ))

    action, skipped = skip_unusable_servant_skills(planned, state, PROFILE)

    assert action.servant_skills == ()
    assert skipped == (
        "servant[1].skill[1].available:low_confidence",
        "servant[2].skill[1].available:state_missing",
    )
# ---------- Unified Action Validator ----------

def _main_action(
    *,
    target_enemy=1,
    servant_skills=(),
    master_skills=(),
    order_change=None,
    picks=(),
):
    return BattleAction(
        target_enemy=target_enemy,
        picks=tuple(picks),
        servant_skills=tuple(servant_skills),
        master_skills=tuple(master_skills),
        order_change=order_change,
    )


def _main_state_with_skill(available, *, enemy_confidence=0.95):
    return BattleState(
        scene=Scene.MAIN_BATTLE,
        scene_confidence=Confidence(0.97, "test"),
        cards=(),
        np_cards=(),
        enemies=(EnemyState(1, True, True, Confidence(enemy_confidence, "test")),),
        servants=(ServantState(
            slot=1,
            skills=(
                SkillState(available, Confidence(0.99 if available is not None else 0.0, "test")),
                SkillState(True, Confidence(0.99, "test")),
                SkillState(True, Confidence(0.99, "test")),
            ),
            confidence=Confidence(0.99, "test"),
        ),),
        screenshot_id="validator-main",
    )


def test_validate_main_action_accepts_legal_skill_and_target():
    state = _main_state_with_skill(True)
    action = _main_action(
        servant_skills=(ServantSkillAction(1, 1, target_ally=2),),
        master_skills=(MasterSkillAction(1),),
    )
    assert validate_main_action(action, state, PROFILE).ok


def test_validate_main_action_rejects_wrong_scene():
    state = make_state([_card(i, "B") for i in range(1, 6)])
    verdict = validate_main_action(_main_action(), state, PROFILE)
    assert verdict.reason == "scene_not_main_battle"


def test_validate_main_action_rejects_card_picks():
    state = _main_state_with_skill(True)
    action = _main_action(picks=(CardPick(PrimitiveKind.SELECT_CARD, 1),))
    assert validate_main_action(action, state, PROFILE).reason == "main_action_contains_card_picks"


def test_validate_main_action_rejects_unknown_skill_state():
    state = _main_state_with_skill(None)
    action = _main_action(servant_skills=(ServantSkillAction(1, 1),))
    assert validate_main_action(action, state, PROFILE).reason == "skill_state_unknown"


def test_validate_main_action_rejects_unavailable_skill():
    state = _main_state_with_skill(False)
    action = _main_action(servant_skills=(ServantSkillAction(1, 1),))
    assert validate_main_action(action, state, PROFILE).reason == "skill_not_available"


def test_validate_main_action_rejects_low_confidence_skill():
    state = BattleState(
        scene=Scene.MAIN_BATTLE,
        scene_confidence=Confidence(0.97, "test"),
        cards=(),
        np_cards=(),
        enemies=(EnemyState(1, True, True, Confidence(0.95, "test")),),
        servants=(ServantState(
            slot=1,
            skills=(
                SkillState(True, Confidence(0.5, "test")),
                SkillState(True, Confidence(0.99, "test")),
                SkillState(True, Confidence(0.99, "test")),
            ),
            confidence=Confidence(0.99, "test"),
        ),),
    )
    action = _main_action(servant_skills=(ServantSkillAction(1, 1),))
    assert validate_main_action(action, state, PROFILE).reason == "skill_state_not_confident"

def test_validate_main_action_rejects_missing_servant_state():
    state = _main_state_with_skill(True)
    action = _main_action(servant_skills=(ServantSkillAction(2, 1),))
    assert validate_main_action(action, state, PROFILE).reason == "servant_state_missing"


def test_validate_main_action_rejects_duplicate_servant_skill():
    state = _main_state_with_skill(True)
    skill = ServantSkillAction(1, 1)
    action = _main_action(servant_skills=(skill, skill))
    filtered, skipped = skip_unusable_servant_skills(action, state, PROFILE)
    assert filtered is action
    assert skipped == ()
    assert validate_main_action(filtered, state, PROFILE).reason == "duplicate_servant_skill"


def test_validate_main_action_rejects_invalid_skill_target():
    state = _main_state_with_skill(True)
    action = _main_action(servant_skills=(ServantSkillAction(1, 1, target_ally=4),))
    filtered, skipped = skip_unusable_servant_skills(action, state, PROFILE)
    assert filtered is action
    assert skipped == ()
    assert validate_main_action(filtered, state, PROFILE).reason == "invalid_skill_target"


def test_validate_main_action_rejects_duplicate_master_skill():
    state = _main_state_with_skill(True)
    skill = MasterSkillAction(1)
    action = _main_action(master_skills=(skill, skill))
    assert validate_main_action(action, state, PROFILE).reason == "duplicate_master_skill"


def test_validate_main_action_rejects_order_change_without_master_skill():
    state = _main_state_with_skill(True)
    action = _main_action(order_change=OrderChangeAction(1, 4))
    assert validate_main_action(action, state, PROFILE).reason == "order_change_without_master_skill"


def test_validate_main_action_accepts_structurally_valid_order_change():
    state = _main_state_with_skill(True)
    action = _main_action(
        master_skills=(MasterSkillAction(2),),
        order_change=OrderChangeAction(1, 4),
    )
    assert validate_main_action(action, state, PROFILE).ok


def test_validate_main_action_rejects_low_confidence_enemy_target():
    state = _main_state_with_skill(True, enemy_confidence=0.5)
    verdict = validate_main_action(_main_action(target_enemy=1), state, PROFILE)
    assert verdict.reason == "enemy_target_not_confident"


def test_validate_card_action_rejects_main_battle_actions():
    state = make_state([_card(i, "B") for i in range(1, 6)])
    action = BattleAction(
        target_enemy=1,
        picks=(
            CardPick(PrimitiveKind.SELECT_CARD, 1),
            CardPick(PrimitiveKind.SELECT_CARD, 2),
            CardPick(PrimitiveKind.SELECT_CARD, 3),
        ),
        master_skills=(MasterSkillAction(1),),
    )
    assert validate_card_action(action, state, PROFILE).reason == "card_action_contains_main_actions"
