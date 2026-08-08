"""Chaldea actions -> BattlePlan 转换器离线测试。"""
from __future__ import annotations

import os
import sys
import types

AGENT = os.path.join(os.path.dirname(__file__), "..", "..", "agent")
sys.path.insert(0, os.path.abspath(AGENT))

fake_mfaalog = types.ModuleType("mfaalog")
fake_mfaalog.info = lambda *_args, **_kwargs: None
sys.modules.setdefault("mfaalog", fake_mfaalog)

from battle.data.chaldea_converter import convert_chaldea_actions_to_battle_plan


def test_empty_actions_yields_one_empty_turn():
    plan = convert_chaldea_actions_to_battle_plan([])
    assert len(plan.turns) == 1
    assert plan.turn(0).servant_skills == ()
    assert plan.turn(0).np_order == ()


def test_not_a_list_yields_one_empty_turn():
    plan = convert_chaldea_actions_to_battle_plan(None)
    assert len(plan.turns) == 1


def test_skill_before_attack_groups_into_turn():
    actions = [
        {"type": "skill", "svt": 0, "skill": 1},
        {"type": "skill", "svt": 1, "skill": 3, "options": {"playerTarget": 1}},
        {"type": "attack", "attacks": [{"isTD": False, "svt": 0}, {"isTD": True, "svt": 2}]},
    ]
    plan = convert_chaldea_actions_to_battle_plan(actions)
    assert len(plan.turns) == 1
    t = plan.turn(0)
    assert len(t.servant_skills) == 2
    assert t.servant_skills[0].servant_slot == 1
    assert t.servant_skills[0].skill_index == 1
    assert t.servant_skills[0].target_ally is None
    assert t.servant_skills[1].servant_slot == 2
    assert t.servant_skills[1].skill_index == 3
    assert t.servant_skills[1].target_ally == 2
    # isTD 宝具 -> servant_slot 3 的 np
    assert t.np_order == (3,)


def test_master_skill_and_multiple_turns():
    actions = [
        {"type": "skill", "svt": None, "skill": 1},
        {"type": "attack", "attacks": [{"isTD": True, "svt": 1}]},
        {"type": "attack", "attacks": [{"isTD": True, "svt": 2}]},
    ]
    plan = convert_chaldea_actions_to_battle_plan(actions)
    assert len(plan.turns) == 2
    assert plan.turn(0).master_skills[0].skill_index == 1
    assert plan.turn(0).np_order == (2,)
    assert plan.turn(1).np_order == (3,)


def test_trailing_skills_without_attack_become_final_turn():
    actions = [
        {"type": "skill", "svt": 2, "skill": 2},
    ]
    plan = convert_chaldea_actions_to_battle_plan(actions)
    assert len(plan.turns) == 1
    assert plan.turn(0).servant_skills[0].servant_slot == 3
    assert plan.turn(0).servant_skills[0].skill_index == 2


def test_order_change_from_mystic_code_delegate():
    actions = [
        {"type": "skill", "svt": None, "skill": 3},
        {"type": "attack", "attacks": []},
    ]
    delegate = {"replaceMemberIndexes": [[0, 3]]}
    plan = convert_chaldea_actions_to_battle_plan(
        actions, delegate=delegate, mystic_code_id=20
    )
    oc = plan.turn(0).order_change
    assert oc is not None
    assert oc.starting_member_idx == 1
    assert oc.sub_member_idx == 4


def test_non_order_change_mystic_code_skill3_is_master_skill():
    actions = [
        {"type": "skill", "svt": None, "skill": 3},
        {"type": "attack", "attacks": []},
    ]
    plan = convert_chaldea_actions_to_battle_plan(actions, mystic_code_id=1)
    assert plan.turn(0).order_change is None
    assert len(plan.turn(0).master_skills) == 1


def test_invalid_actions_are_ignored():
    actions = [
        "garbage",
        {"type": "unknown", "svt": 0, "skill": 1},
        {"type": "skill", "svt": 7, "skill": 1},   # 越界 svt
        {"type": "skill", "svt": 0, "skill": 4},   # 越界 skill
        {"type": "attack", "attacks": "not-a-list"},
    ]
    plan = convert_chaldea_actions_to_battle_plan(actions)
    assert len(plan.turns) == 1
    assert plan.turn(0).servant_skills == ()


def test_converts_from_full_chaldea_share_dict():
    """验证 _build_plan_from_share 内部的完整 share dict 解析（mysticCode + actions）。"""
    share = {
        "mysticCode": {"id": 1},
        "actions": [
            {"type": "skill", "svt": 0, "skill": 1},
            {"type": "attack", "attacks": [{"isTD": True, "svt": 1}]},
            {"type": "skill", "svt": 2, "skill": 2},
            {"type": "attack", "attacks": []},
        ],
    }
    from battle.data.chaldea_converter import convert_chaldea_actions_to_battle_plan
    actions = share.get("actions")
    mystic_code_id = (share.get("mysticCode") or {}).get("id")
    plan = convert_chaldea_actions_to_battle_plan(
        actions,
        delegate=share.get("delegate"),
        mystic_code_id=mystic_code_id,
    )
    assert len(plan.turns) == 2
    # turn0: skill1 + np for svt2
    assert plan.turn(0).servant_skills[0].servant_slot == 1
    assert plan.turn(0).np_order == (2,)
    # turn1: servant3 skill2
    assert plan.turn(1).servant_skills[0].servant_slot == 3
    assert plan.turn(1).np_order == ()


def test_share_without_actions_yields_one_empty_turn():
    """验证没有 actions 的 share dict —— 回退为空回合（不崩溃）。"""
    share = {"mysticCode": {"id": 1}}
    from battle.data.chaldea_converter import convert_chaldea_actions_to_battle_plan
    plan = convert_chaldea_actions_to_battle_plan(
        share.get("actions"),
        delegate=share.get("delegate"),
        mystic_code_id=(share.get("mysticCode") or {}).get("id"),
    )
    assert len(plan.turns) == 1
    assert plan.turn(0).servant_skills == ()
