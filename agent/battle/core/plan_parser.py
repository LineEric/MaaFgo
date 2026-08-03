"""原生自动战斗计划解析。

该模块只依赖核心模型，便于离线单元测试，不加载 Maa 运行时。
"""
from __future__ import annotations

import json

from battle.core.models import (BattlePlan, MasterSkillAction, OrderChangeAction,
                                ServantSkillAction, TurnPlan)
from battle.core.policy import StrategyProfile


def _parse_plan(param: dict | None) -> BattlePlan | None:
    """从 custom_action_param 解析 BattlePlan。

    参数格式示例：
    {
        "plan": {
            "turns": [
                {
                    "servant_skills": [{"servant_slot": 1, "skill_index": 1, "target_ally": 2}],
                    "master_skills": [{"skill_index": 1}],
                    "np_order": [1, 3],
                    "target_enemy": 1,
                    "order_change": {"starting_member_idx": 1, "sub_member_idx": 4}
                }
            ]
        }
    }
    """
    # Maa 的 custom_action_param 可能为空字符串、JSON null，或其他合法
    # JSON 标量。只有对象才有 plan 配置；其余情况按“无攻略计划”处理，
    # 让 RuleDecider 回退到默认 V1 策略，而不是让 Custom Action 回调异常。
    if not isinstance(param, dict):
        return None

    plan_data = param.get("plan")
    if not plan_data or not isinstance(plan_data, dict):
        return None

    turns_data = plan_data.get("turns", [])
    if not isinstance(turns_data, list):
        return None

    turns: list[TurnPlan] = []
    for td in turns_data:
        # 保留空回合占位，避免坏配置导致后续回合索引前移。
        if not isinstance(td, dict):
            turns.append(TurnPlan())
            continue

        servant_data = td.get("servant_skills", [])
        master_data = td.get("master_skills", [])
        np_data = td.get("np_order", [])
        if not isinstance(servant_data, list):
            servant_data = []
        if not isinstance(master_data, list):
            master_data = []
        if not isinstance(np_data, list):
            np_data = []

        svts: tuple[ServantSkillAction, ...] = ()
        for s in servant_data:
            if not isinstance(s, dict):
                continue
            if not isinstance(s.get("servant_slot"), int) or not isinstance(s.get("skill_index"), int):
                continue
            if not 1 <= s["servant_slot"] <= 3 or not 1 <= s["skill_index"] <= 3:
                continue
            target_ally = s.get("target_ally")
            if target_ally is not None and (not isinstance(target_ally, int) or not 1 <= target_ally <= 3):
                continue
            svts = (*svts, ServantSkillAction(
                servant_slot=s["servant_slot"],
                skill_index=s["skill_index"],
                target_ally=target_ally,
            ))

        masters: tuple[MasterSkillAction, ...] = ()
        for m in master_data:
            if not isinstance(m, dict) or not isinstance(m.get("skill_index"), int):
                continue
            if not 1 <= m["skill_index"] <= 3:
                continue
            target_ally = m.get("target_ally")
            if target_ally is not None and (not isinstance(target_ally, int) or not 1 <= target_ally <= 3):
                continue
            masters = (*masters, MasterSkillAction(
                skill_index=m["skill_index"],
                target_ally=target_ally,
            ))

        oc = None
        oc_data = td.get("order_change")
        if isinstance(oc_data, dict):
            starting = oc_data.get("starting_member_idx")
            sub = oc_data.get("sub_member_idx")
            if (isinstance(starting, int) and 1 <= starting <= 3
                    and isinstance(sub, int) and 4 <= sub <= 6):
                oc = OrderChangeAction(
                    starting_member_idx=starting,
                    sub_member_idx=sub,
                )

        np_order = tuple(slot for slot in np_data if isinstance(slot, int) and 1 <= slot <= 3)

        turns.append(TurnPlan(
            servant_skills=svts,
            master_skills=masters,
            order_change=oc,
            np_order=np_order,
            target_enemy=td.get("target_enemy"),
        ))

    return BattlePlan(turns=tuple(turns))


def _load_action_param(raw_param: object) -> dict:
    """将 Maa 传入的 custom_action_param 规范化为对象。"""
    if isinstance(raw_param, dict):
        return raw_param
    if not isinstance(raw_param, str) or not raw_param.strip():
        return {}
    try:
        parsed = json.loads(raw_param)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}

def _parse_strategy_profile(param: dict) -> StrategyProfile:
    """解析运行参数；非法或越界值回退到安全默认值。"""
    profile_id = param.get("strategy_profile", "farm-safe-v1")
    if not isinstance(profile_id, str) or not profile_id.strip():
        profile_id = "farm-safe-v1"

    max_turns = param.get("max_turns", 20)
    if isinstance(max_turns, bool) or not isinstance(max_turns, int):
        max_turns = 20
    max_turns = max(1, min(max_turns, 100))
    return StrategyProfile(id=profile_id, max_turns=max_turns)

