"""决策层（可插拔）。V1 为 RuleDecider；以后 LLMDecider 实现同一 Protocol 即可替换。

纯逻辑，无设备/无网络/无 maa 依赖，可离线单测。
V1b 策略：宝具卡优先出，剩余槽位用面卡按卡色枚举打分补齐，共 3 张。
owner_slot 在 V1 为 None，故暂不计 Brave / 从者优先级。
"""
from __future__ import annotations

from itertools import permutations
from typing import List, Protocol, Tuple

from .enums import CardColor, PrimitiveKind
from .models import BattleAction, BattleState, CardPick, CommandCard
from .policy import CardPolicy, Goal

_GOAL_COLOR = {
    Goal.FINISH_WAVE: CardColor.BUSTER,
    Goal.BUILD_NP: CardColor.ARTS,
    Goal.BUILD_STARS: CardColor.QUICK,
}


class Decider(Protocol):
    def decide(self, state: BattleState) -> BattleAction: ...


class RuleDecider:
    def __init__(self, policy: CardPolicy | None = None) -> None:
        self.policy = policy or CardPolicy()

    def decide(self, state: BattleState) -> BattleAction:
        target = _pick_target(state)

        if state.scene is Scene.MAIN_BATTLE:
            # TODO: 读取 TurnPlan 并填入需要释放的技能
            # V2 暂时返回空的技能列表，由 runtime 触发进入下个阶段
            return BattleAction(
                target_enemy=target,
                picks=(),
                servant_skills=(),
                master_skills=(),
                order_change=None,
                rationale_tag="v2:main_battle_placeholder"
            )

        # 选卡阶段
        np_picks: List[CardPick] = []
        if self.policy.np_first:
            np_picks = [CardPick(PrimitiveKind.SELECT_NP, c.servant_slot) for c in state.np_cards][:3]

        need = 3 - len(np_picks)
        face_picks: List[CardPick] = []
        if need > 0 and state.cards:
            slots = _best_face_order(state.cards, min(need, len(state.cards)), self.policy)
            face_picks = [CardPick(PrimitiveKind.SELECT_CARD, s) for s in slots]

        picks = tuple((np_picks + face_picks)[:3])
        return BattleAction(
            target_enemy=target, 
            picks=picks,
            rationale_tag=f"v2:{self.policy.goal.value}"
        )


def _pick_target(state: BattleState):
    tgt = next((e.slot for e in state.enemies if e.alive and e.targeted), None)
    if tgt is None:
        tgt = next((e.slot for e in state.enemies if e.alive), None)
    return tgt


def _best_face_order(cards: Tuple[CommandCard, ...], need: int, policy: CardPolicy) -> List[int]:
    weights = {c: len(policy.color_priority) - i for i, c in enumerate(policy.color_priority)}
    goal_color = _GOAL_COLOR.get(policy.goal)

    def score(seq: Tuple[CommandCard, ...]) -> float:
        s = 0.0
        n = len(seq)
        for pos, card in enumerate(seq):
            s += weights.get(card.color, 0) * (n - pos)      # 靠前的卡权重更高
        if len(set(c.color for c in seq)) == 1:              # 同色链
            s += 5.0
        if goal_color is not None:
            if seq[0].color is goal_color:
                s += 3.0
            if seq[-1].color is goal_color:
                s += 2.0
        return s

    best = max(permutations(cards, need), key=score)
    return [c.ui_slot for c in best]
