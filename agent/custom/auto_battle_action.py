"""MFW Custom Action 入口：原生自动战斗（V1b）。

与 bbc_action 并列，作为可选战斗后端；不与 bbc_* 相互 import。
默认不改变现有 pipeline，需在节点显式使用 custom_action="auto_battle"。

节点参数（custom_action_param，JSON）示例：
  {"strategy_profile":"farm-safe-v1","max_turns":20,"save_evidence":true}
"""
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
from battle.core.plan_parser import (_load_action_param, _parse_plan,
                                    _parse_strategy_profile)
from battle.core.policy import CardPolicy, StrategyProfile
from battle.runtime.runtime import AutoBattleRuntime
from battle.vision.config import VisionConfig
from battle.vision.provider import create_provider
from battle.vision.orchestrator import VisionOrchestrator


@AgentServer.custom_action("auto_battle")
class AutoBattleAction(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        param = _load_action_param(argv.custom_action_param)
        profile = _parse_strategy_profile(param)
        plan = _parse_plan(param)
        decider = RuleDecider(CardPolicy(), plan=plan)

        # Agent 模式下，每次调用 context.tasker.controller 都会通过反向 IPC
        # 获取一个新的 handle，只有第一次有效。因此在这里获取一次并传递下去。
        controller = context.tasker.controller

        vision_orchestrator = None
        vision_config = VisionConfig.from_mapping(param.get("vision"))
        if vision_config.enabled:
            vision_provider = create_provider(vision_config)
            vision_orchestrator = VisionOrchestrator(vision_provider, vision_config)
            mfaalog.info(
                f"[auto_battle] vision enabled provider={vision_config.provider} "
                f"model={vision_config.model} max_calls_per_turn={vision_config.max_calls_per_turn}"
            )

        plan_status = "loaded" if plan is not None else "none"
        plan_turns = len(plan.turns) if plan is not None else 0
        mfaalog.info(
            f"[auto_battle] start profile={profile.id} "
            f"max_turns={profile.max_turns} plan={plan_status} plan_turns={plan_turns}"
        )
        result = AutoBattleRuntime(context, controller, decider, profile, vision_orchestrator=vision_orchestrator).run()
        mfaalog.info(f"[auto_battle] end ok={result.ok} reason={result.reason} turns={result.turns}")

        # TODO(save_evidence)：失败时保存截图/状态证据
        return CustomAction.RunResult(success=result.ok)
