"""MFW Custom Action 入口：原生自动战斗（V1b）。

与 bbc_action 并列，作为可选战斗后端；不与 bbc_* 相互 import。
默认不改变现有 pipeline，需在节点显式使用 custom_action="auto_battle"。

节点参数（custom_action_param，JSON）示例：
  {"strategy_profile":"farm-safe-v1","max_turns":20,"save_evidence":true}
"""
import json
import os
import sys

# 让 agent/battle 可作为顶层包导入（main.py 只把 custom 目录加进了 path）
_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

import mfaalog
from battle.core.decider import RuleDecider
from battle.core.models import (BattlePlan, MasterSkillAction, OrderChangeAction,
                                ServantSkillAction, TurnPlan)
from battle.core.policy import CardPolicy, StrategyProfile
from battle.runtime.runtime import AutoBattleRuntime


def _parse_plan(param: dict) -> BattlePlan | None:
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
    plan_data = param.get("plan")
    if not plan_data or not isinstance(plan_data, dict):
        return None

    turns_data = plan_data.get("turns", [])
    if not isinstance(turns_data, list):
        return None

    turns: list[TurnPlan] = []
    for td in turns_data:
        if not isinstance(td, dict):
            continue

        svts: tuple[ServantSkillAction, ...] = ()
        for s in td.get("servant_skills", []):
            svts = (*svts, ServantSkillAction(
                servant_slot=s["servant_slot"],
                skill_index=s["skill_index"],
                target_ally=s.get("target_ally"),
            ))

        masters: tuple[MasterSkillAction, ...] = ()
        for m in td.get("master_skills", []):
            masters = (*masters, MasterSkillAction(
                skill_index=m["skill_index"],
                target_ally=m.get("target_ally"),
            ))

        oc = None
        oc_data = td.get("order_change")
        if oc_data and isinstance(oc_data, dict):
            oc = OrderChangeAction(
                starting_member_idx=oc_data["starting_member_idx"],
                sub_member_idx=oc_data["sub_member_idx"],
            )

        np_order = tuple(td.get("np_order", []))

        turns.append(TurnPlan(
            servant_skills=svts,
            master_skills=masters,
            order_change=oc,
            np_order=np_order,
            target_enemy=td.get("target_enemy"),
        ))

    return BattlePlan(turns=tuple(turns))


@AgentServer.custom_action("auto_battle")
class AutoBattleAction(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            param = json.loads(argv.custom_action_param or "{}")
        except json.JSONDecodeError:
            param = {}

        profile = StrategyProfile()
        plan = _parse_plan(param)
        decider = RuleDecider(CardPolicy(), plan=plan)

        # Agent 模式下，每次调用 context.tasker.controller 都会通过反向 IPC
        # 获取一个新的 handle，只有第一次有效。因此在这里获取一次并传递下去。
        controller = context.tasker.controller

        mfaalog.info(f"[auto_battle] start profile={profile.id} max_turns={profile.max_turns}")
        result = AutoBattleRuntime(context, controller, decider, profile).run()
        mfaalog.info(f"[auto_battle] end ok={result.ok} reason={result.reason} turns={result.turns}")

        # TODO(save_evidence)：失败时保存截图/状态证据
        return CustomAction.RunResult(success=result.ok)
