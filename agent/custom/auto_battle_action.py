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
from battle.core.policy import CardPolicy, StrategyProfile
from battle.runtime.runtime import AutoBattleRuntime


@AgentServer.custom_action("auto_battle")
class AutoBattleAction(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            param = json.loads(argv.custom_action_param or "{}")
        except json.JSONDecodeError:
            param = {}

        profile = StrategyProfile(
            id=param.get("strategy_profile", "farm-safe-v1"),
            max_turns=int(param.get("max_turns", 20)),
        )
        decider = RuleDecider(CardPolicy())

        mfaalog.info(f"[auto_battle] start profile={profile.id} max_turns={profile.max_turns}")
        result = AutoBattleRuntime(context, decider, profile).run()
        mfaalog.info(f"[auto_battle] end ok={result.ok} reason={result.reason} turns={result.turns}")

        # TODO(save_evidence)：失败时保存截图/状态证据
        return CustomAction.RunResult(success=result.ok)
