"""原生自动战斗入口参数与 TurnPlan 解析单测。"""

import os
import sys
import types

AGENT = os.path.join(os.path.dirname(__file__), "..", "..", "agent")
sys.path.insert(0, os.path.abspath(AGENT))

# 参数解析测试不需要 Maa 运行时日志模块。
fake_mfaalog = types.ModuleType("mfaalog")
fake_mfaalog.info = lambda *_args, **_kwargs: None
sys.modules.setdefault("mfaalog", fake_mfaalog)

from battle.core.plan_parser import (_load_action_param, _parse_plan,
                                    _parse_strategy_profile)


def test_load_action_param_accepts_dict():
    value = {"plan": {"turns": []}}
    assert _load_action_param(value) is value


def test_load_action_param_accepts_json_object():
    assert _load_action_param('{"plan": {"turns": []}}') == {"plan": {"turns": []}}


def test_load_action_param_rejects_empty_invalid_and_non_object():
    assert _load_action_param(None) == {}
    assert _load_action_param("") == {}
    assert _load_action_param("not-json") == {}
    assert _load_action_param("null") == {}
    assert _load_action_param("[]") == {}


def test_parse_plan_with_servant_master_skills_and_np_order():
    plan = _parse_plan({
        "plan": {
            "turns": [{
                "servant_skills": [{"servant_slot": 1, "skill_index": 2, "target_ally": 3}],
                "master_skills": [{"skill_index": 1}],
                "np_order": [2, 1],
                "target_enemy": 3,
                "order_change": {"starting_member_idx": 1, "sub_member_idx": 4},
            }]
        }
    })
    assert plan is not None
    turn = plan.turn(0)
    assert turn.servant_skills[0].target_ally == 3
    assert turn.master_skills[0].skill_index == 1
    assert turn.np_order == (2, 1)
    assert turn.target_enemy == 3
    assert turn.order_change is not None


def test_parse_plan_ignores_missing_plan():
    assert _parse_plan({}) is None
    assert _parse_plan(None) is None

def test_parse_plan_preserves_multiple_turns_in_order():
    plan = _parse_plan({
        "plan": {
            "turns": [
                {"servant_skills": [{"servant_slot": 1, "skill_index": 1}], "np_order": [1]},
                {"servant_skills": [{"servant_slot": 3, "skill_index": 2, "target_ally": 1}]},
            ]
        }
    })
    assert plan is not None
    assert len(plan.turns) == 2
    assert plan.turn(0).servant_skills[0].servant_slot == 1
    assert plan.turn(0).np_order == (1,)
    assert plan.turn(1).servant_skills[0].servant_slot == 3
    assert plan.turn(1).servant_skills[0].target_ally == 1


def test_parse_plan_skips_malformed_actions_without_breaking_other_turns():
    plan = _parse_plan({
        "plan": {
            "turns": [
                {
                    "servant_skills": [
                        {"servant_slot": 9, "skill_index": 1},
                        {"servant_slot": 1, "skill_index": 1},
                    ],
                    "np_order": [0, 2, "3"],
                },
                None,
                {"servant_skills": "invalid"},
            ]
        }
    })
    assert plan is not None
    assert len(plan.turns) == 3
    assert len(plan.turn(0).servant_skills) == 1
    assert plan.turn(0).np_order == (2,)
    assert plan.turn(1).servant_skills == ()
    assert plan.turn(2).servant_skills == ()

def test_parse_strategy_profile_reads_and_bounds_max_turns():
    profile = _parse_strategy_profile({"strategy_profile": "custom-v1", "max_turns": 3})
    assert profile.id == "custom-v1"
    assert profile.max_turns == 3

    assert _parse_strategy_profile({"max_turns": 0}).max_turns == 1
    assert _parse_strategy_profile({"max_turns": 999}).max_turns == 100
    assert _parse_strategy_profile({"max_turns": True}).max_turns == 20
    assert _parse_strategy_profile({"max_turns": "3"}).max_turns == 20

