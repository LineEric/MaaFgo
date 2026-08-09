"""MFW Custom Action：专门测试 slot=3 三个技能能否被点击到。

目标：绕过完整战斗流程，直接对 slot=3 的 3 个技能按钮逐个执行原子点击，
并在每次点击后轮询感知若干帧，通过画面变化判定这次点击是否生效。

判定标准（log 里的结论列）：
  - 点击后出现 SKILL_TARGET_SELECTION（技能目标子屏）→ 点击生效，技能是"需选目标"类
  - 点击后出现 UNKNOWN 动画帧然后恢复 → 点击生效，触发了技能施放动画
  - 点击后画面一直保持 MAIN_BATTLE 静止（若干帧无变化）→ 点击未生效/未点到

用法（pipeline 节点参数）：
  {"custom_action":"test_slot3_click","custom_action_param":{
      "slots":[1,2,3],            // 可选，默认 [1,2,3]
      "frames":4,                 // 可选，每次点击后轮询次数，默认 4
      "wait_ms":500,              // 可选，每次轮询间隔，默认 500
      "target_ally":null          // 可选，指定则点击后等该目标子屏；默认 null（只观察）
  }}
"""
from __future__ import annotations

import os
import sys
import time

# 让 agent/battle 可作为顶层包导入（main.py 只把 custom 目录加进了 path）
_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

import mfaalog
from battle.core.enums import Scene
from battle.execution import coords
from battle.execution.executor import Executor
from battle.perception import perception


def _load_param(raw_param: object) -> dict:
    if isinstance(raw_param, dict):
        return raw_param
    if not isinstance(raw_param, str) or not raw_param.strip():
        return {}
    import json
    try:
        parsed = json.loads(raw_param)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _observe(ctx, controller, tag: str) -> str:
    """截图并感知，返回该帧识别的场景名（字符串）。"""
    try:
        img = controller.post_screencap().wait().get()
    except Exception as exc:  # pragma: no cover - 设备截图失败
        mfaalog.error(f"[slot3click] {tag} screencap failed: {exc}")
        return "ERR"
    if img is None:
        return "NONE"
    try:
        state = perception.build(ctx, img)
        return state.scene.name
    except Exception as exc:  # 感知异常时返回 ERR 而不是中断，便于观察
        mfaalog.error(f"[slot3click] {tag} perception failed: {exc}")
        return "ERR"


@AgentServer.custom_action("test_slot3_click")
class Slot3ClickTestAction(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        param = _load_param(argv.custom_action_param)

        slots = param.get("slots", [1, 2, 3])
        if not isinstance(slots, list) or not slots:
            slots = [1, 2, 3]
        slots = [s for s in slots if isinstance(s, int) and 1 <= s <= 3]
        frames = int(param.get("frames", 4) or 4)
        wait_ms = int(param.get("wait_ms", 500) or 500)

        # Agent 模式下 controller handle 每次取值只有第一次有效，务必只取一次。
        controller = context.tasker.controller
        executor = Executor(context, controller)

        mfaalog.info(
            f"[slot3click] start slots={list(slots)} frames={frames} wait_ms={wait_ms}"
        )

        overall_ok = True
        for skill_index in slots:
            key = (3, skill_index)
            click_pt = coords.SERVANT_SKILL_CLICK[key]
            mfaalog.info(
                f"[slot3click] slot=3 skill={skill_index} click=({click_pt[0]},{click_pt[1]})"
            )

            # 点击前先截一帧作为基线。
            baseline = _observe(context, controller, f"pre-skill{skill_index}")
            mfaalog.info(f"[slot3click] skill{skill_index} baseline_scene={baseline}")

            try:
                executor.cast_servant_skill(3, skill_index)
            except Exception as exc:  # pragma: no cover
                mfaalog.error(f"[slot3click] skill{skill_index} click raised: {exc}")
                overall_ok = False
                continue

            # 点击后轮询 frames 帧，记录场景序列。
            seq: list[str] = []
            saw_animation = False
            saw_target = False
            for _ in range(frames):
                time.sleep(wait_ms / 1000.0)
                scene_name = _observe(context, controller, f"post-skill{skill_index}")
                seq.append(scene_name)
                if scene_name == Scene.SKILL_TARGET_SELECTION.name:
                    saw_target = True
                if scene_name == Scene.UNKNOWN.name:
                    saw_animation = True

            # 判定结论。
            if saw_target:
                conclusion = "HIT (target sub-screen appeared)"
            elif saw_animation:
                conclusion = "HIT (skill animation observed)"
            else:
                conclusion = "MISS (screen stayed MAIN_BATTLE)"
                overall_ok = False

            mfaalog.info(
                f"[slot3click] skill{skill_index} screen_seq={seq} -> {conclusion}"
            )

        mfaalog.info(f"[slot3click] end overall_ok={overall_ok}")
        return CustomAction.RunResult(success=overall_ok)
