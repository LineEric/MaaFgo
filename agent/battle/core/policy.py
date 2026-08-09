"""策略与关卡档。纯 stdlib。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Tuple

from .enums import CardColor


class Goal(str, Enum):
    FINISH_WAVE = "finish_wave"
    BUILD_NP = "build_np"
    BUILD_STARS = "build_stars"


@dataclass(frozen=True)
class CardPolicy:
    goal: Goal = Goal.FINISH_WAVE
    color_priority: Tuple[CardColor, ...] = (CardColor.BUSTER, CardColor.ARTS, CardColor.QUICK)
    np_first: bool = True                 # 有宝具卡则优先出
    prefer_mighty_chain: bool = True      # 三色连锁（红蓝绿各一张）优先


@dataclass(frozen=True)
class StrategyProfile:
    id: str = "farm-safe-v1"
    min_scene_confidence: float = 0.95
    min_card_confidence: float = 0.50
    min_enemy_confidence: float = 0.80
    min_skill_confidence: float = 0.80
    max_turns: int = 20
    # 高风险开关：V1 全部关闭，且执行层根本不提供入口
    allow_command_spell: bool = False
    allow_sq_revive: bool = False
    allow_ap_refill: bool = False
    fallback: str = "stop"                # stop | bbc（仅外层显式允许时）
